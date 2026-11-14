'use strict';
/* 开放式交互：目标选择、动作提交、肩上物件与告示板书写。 */

import { num, str, list, vec3, escapeHtml, els, state } from './core.js';
import { npcFootY } from './geometry.js';
import { npcRenderPosition } from './npc.js';
import {
  requestJson, refreshView, toast, showDialogue
} from './hud.js';

const NPC_VERBS = [
  { action: 'talk', key: 'E' },
  { action: 'dance', key: 'F' },
  { action: 'hit', key: 'R' },
  { action: 'observe', key: 'Q' }
];
const PROP_VERBS = [
  { action: 'inspect', key: 'E' },
  { action: 'use', key: 'F' },
  { action: 'harvest', key: 'R' }
];
// 可扛在肩上的物件，采集动词在提示里改写为“扛起”。
const CARRY_KINDS = new Set(['crate', 'driftwood', 'net']);
const ACTION_LABELS = {
  talk: '交谈', dance: '共舞', hit: '击打', observe: '观察',
  inspect: '查看', use: '使用', harvest: '采集'
};
// 键位按动词顺序映射，和目标卡上的提示保持一致。
export const ACTION_KEY_SLOTS = { KeyE: 0, KeyF: 1, KeyR: 2, KeyQ: 3 };
const NPC_INTERACT_RANGE = 8.0;
const PROP_INTERACT_RANGE = 6.5;

function targetSignature(target) {
  return target ? `${target.kind}:${target.id}` : '';
}

// 每 140ms 选一次目标：视线前方、距离最近的可交互对象。
export function updateInteractionTarget(now) {
  const stamp = num(now, 0);
  if (stamp - state.targetCheckAt < 140) return;
  state.targetCheckAt = stamp;
  if (!state.loaded || !state.view || state.noteEditing) {
    if (state.target && !state.noteEditing) { state.target = null; renderTarget(); }
    return;
  }

  const forwardX = Math.sin(state.yaw);
  const forwardZ = -Math.cos(state.yaw);
  let best = null;
  const consider = (candidate) => {
    const p = candidate.position;
    const dx = p[0] - state.player.x;
    const dz = p[2] - state.player.z;
    const dist = Math.hypot(dx, dz);
    if (dist > candidate.range) return;
    // 贴脸时忽略朝向，避免目标在脚边却选不中。
    const dot = dist < 1.4 ? 1 : (dx * forwardX + dz * forwardZ) / Math.max(dist, 0.001);
    if (dot < 0.2) return;
    const score = dist - dot * 2.5;
    if (!best || score < best.score) best = { ...candidate, dist, dot, score };
  };

  for (const npc of list(state.view.npcs)) {
    const raw = vec3(npc && npc.position_m);
    if (!raw) continue;
    const motion = npcRenderPosition(npc);
    const p = [num(motion && motion.x, raw[0]), raw[1], num(motion && motion.z, raw[2])];
    consider({
      kind: 'npc',
      id: str(npc.id, str(npc.name, 'npc')),
      name: str(npc.name, str(npc.id, 'NPC')),
      activity: str(npc.activity, ''),
      trustLabel: str(npc.trust_label, ''),
      position: [p[0], npcFootY(npc, p), p[2]],
      range: NPC_INTERACT_RANGE,
      verbs: NPC_VERBS
    });
  }
  for (const prop of list(state.view.props)) {
    // 已扛在肩上的物件不再作为地面目标。
    if (state.carried && str(prop.id) === str(state.carried.id)) continue;
    const p = vec3(prop && prop.position_m);
    if (!p) continue;
    consider({
      kind: 'prop',
      id: str(prop.id),
      name: str(prop.name, '物件'),
      propKind: str(prop.kind),
      position: p,
      range: PROP_INTERACT_RANGE,
      verbs: PROP_VERBS
    });
  }

  const changed = targetSignature(best) !== targetSignature(state.target);
  state.target = best;
  if (changed) renderTarget();
}

function renderTarget() {
  if (!els.interact || !els.interactTarget || !els.interactVerbs) return;
  if (state.noteEditing) {
    els.interact.classList.add('hidden');
    els.interactTarget.textContent = '';
    els.interactVerbs.textContent = '';
    return;
  }
  const target = state.target;
  if (!target) {
    els.interact.classList.add('hidden');
    els.interactTarget.textContent = '';
    els.interactVerbs.textContent = '';
    return;
  }
  els.interact.classList.remove('hidden');
  const meta = target.kind === 'npc'
    ? `${target.activity || '闲逛'} · ${target.trustLabel || '平淡'} · ${target.dist.toFixed(1)}m`
    : `${target.dist.toFixed(1)}m · 可交互`;
  els.interactTarget.innerHTML = `<strong>${escapeHtml(target.name)}</strong><span>${escapeHtml(meta)}</span>`;
  els.interactVerbs.innerHTML = target.verbs.map((verb) => {
    let label = ACTION_LABELS[verb.action] || verb.action;
    if (target.propKind === 'notice' && verb.action === 'use') label = '书写';
    if (CARRY_KINDS.has(str(target.propKind)) && verb.action === 'harvest') label = '扛起';
    return `<button type="button" data-interact="${escapeHtml(verb.action)}">`
      + `<b>${escapeHtml(verb.key)}</b> ${escapeHtml(label)}</button>`;
  }).join('');
}

async function performInteraction(action, extra = null) {
  const target = state.target;
  if (!target || state.interacting) return;
  state.interacting = true;
  try {
    const result = await requestJson('/api/interact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: target.kind,
        id: target.id,
        action,
        position_m: [state.player.x, state.player.y, state.player.z],
        ...(extra || {})
      })
    });
    const line = str(result && result.speech, str(result && result.message, ''));
    showDialogue(target.name, line);
    toast(str(result && result.message, '完成'));
    const now = performance.now();
    if (action === 'dance') {
      state.danceNpc = target.id;
      state.danceUntil = now + 6000;
    } else if (action === 'hit') {
      state.hitNpc = target.id;
      state.hitUntil = now + 900;
    }
    await refreshView({ silent: true });
  } catch (error) {
    toast(str(error && error.message, '交互失败'), true);
  } finally {
    state.interacting = false;
  }
}

export async function dropCarried() {
  if (!state.carried || state.interacting) return;
  state.interacting = true;
  try {
    const result = await requestJson('/api/interact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: 'carry',
        id: str(state.carried.id, 'held'),
        action: 'drop',
        position_m: [state.player.x, state.player.y, state.player.z]
      })
    });
    showDialogue('肩上', str(result && result.message, '你放下了肩上的物件。'));
    toast(str(result && result.message, '已放下'));
    await refreshView({ silent: true });
  } catch (error) {
    toast(str(error && error.message, '放下失败'), true);
  } finally {
    state.interacting = false;
  }
}

// 告示板：使用动词先弹出编辑器，再把文字随交互请求提交。
export function openNoteEditor(target) {
  if (!els.noteEditor || !els.noteInput) return;
  state.noteEditing = true;
  state.noteTarget = target;
  if (document.pointerLockElement) document.exitPointerLock();
  state.keys = Object.create(null);
  els.noteInput.value = '';
  updateNoteCount();
  els.noteEditor.classList.remove('hidden');
  renderTarget();
  window.setTimeout(() => els.noteInput.focus(), 30);
}

export function closeNoteEditor() {
  state.noteEditing = false;
  state.noteTarget = null;
  if (els.noteEditor) els.noteEditor.classList.add('hidden');
  renderTarget();
}

export function updateNoteCount() {
  if (!els.noteCount || !els.noteInput) return;
  els.noteCount.textContent = `${els.noteInput.value.length}/60`;
}

export function submitNote() {
  const target = state.noteTarget;
  const text = els.noteInput ? els.noteInput.value.trim() : '';
  closeNoteEditor();
  if (!target) return;
  state.target = target;
  performInteraction('use', { text });
}

// 使用告示板先进入书写，其余动作直接提交。
export function triggerAction(action) {
  const target = state.target;
  if (target && target.kind === 'prop' && target.propKind === 'notice' && action === 'use') {
    openNoteEditor(target);
    return;
  }
  performInteraction(action);
}
