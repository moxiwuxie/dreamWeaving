'use strict';
/* HUD：DOM 面板渲染、场景标题、提示与网络请求。 */

import {
  EYE_DEFAULT, THIRD_DISTANCE_DEFAULT, WEATHER_LABELS, EVENT_LABELS,
  clamp, num, str, list, escapeHtml, timeLabel,
  els, state
} from './core.js';
import { sceneAt, sceneSignature, rebuildSceneCache, syncServerFeet } from './geometry.js';
import { uploadStatic } from './render.js';

/* ---------------- 对话 / 承载提示 ---------------- */

export function showDialogue(speaker, line) {
  if (!els.dialogue || !els.dialogueLine) return;
  els.dialogueSpeaker.textContent = str(speaker, '');
  els.dialogueLine.textContent = str(line, '');
  els.dialogue.classList.add('show');
  window.clearTimeout(state.dialogueTimer);
  state.dialogueTimer = window.setTimeout(() => {
    els.dialogue.classList.remove('show');
  }, 7000);
}

export function renderCarry() {
  if (!els.carryChip || !els.carryName) return;
  if (state.carried) {
    els.carryName.textContent = str(state.carried.name, '物件');
    els.carryChip.classList.remove('hidden');
  } else {
    els.carryChip.classList.add('hidden');
  }
}

/* ---------------- HUD 渲染 ---------------- */

function barHtml(label, valueText, percent, color) {
  const width = clamp(num(percent, 0), 0, 100);
  return `<div><div class="bar-label"><span>${escapeHtml(label)}</span><b>${escapeHtml(valueText)}</b></div>`
    + `<div class="bar"><i style="width:${width.toFixed(1)}%;background:${color}"></i></div></div>`;
}

function renderVitals(view) {
  const player = (view && view.player) || {};
  const vitals = player.vitals || {};
  const health = num(vitals.health, num(player.health, 100));
  const temp = num(vitals.core_temperature_c, 36.8);
  const hunger = clamp(num(vitals.hunger, 1), 0, 1);
  const thirst = clamp(num(vitals.thirst, 1), 0, 1);
  const fatigue = clamp(num(vitals.fatigue, 0), 0, 1);
  const oxygen = Math.max(0, num(vitals.oxygen_seconds, 0));
  const tempPct = clamp((temp - 20) / 22 * 100, 0, 100);
  const tempTone = temp < 35 ? 'var(--bad)' : temp > 39.5 ? 'var(--bad)' : temp > 38.5 ? 'var(--warn)' : '#58c4ff';

  els.vitals.innerHTML = [
    barHtml('生命', health.toFixed(0), health, health < 40 ? 'var(--bad)' : 'var(--good)'),
    barHtml('体温', `${temp.toFixed(1)}℃`, tempPct, tempTone),
    barHtml('饱腹', `${Math.round(hunger * 100)}%`, hunger * 100, '#58c4ff'),
    barHtml('水分', `${Math.round(thirst * 100)}%`, thirst * 100, '#38bdf8'),
    barHtml('疲劳', `${Math.round(fatigue * 100)}%`, fatigue * 100, 'var(--violet)'),
    barHtml('氧气', `${oxygen.toFixed(0)} 秒`, clamp(oxygen / 90 * 100, 0, 100), '#38bdf8')
  ].join('');

  const survival = player.survival || {};
  const chips = [
    ['饮水', 'drank'], ['进食', 'ate'], ['水源', 'water'], ['食物', 'food']
  ].map(([label, key]) => {
    const on = Boolean(survival[key]);
    return `<span class="chip ${on ? 'on' : ''}">${escapeHtml(label)}${on ? ' 正常' : ' 未满足'}</span>`;
  });
  const effects = list(player.effects).map((effect) => {
    const id = str(effect && effect.id, '异常');
    const severity = num(effect && effect.severity, 0);
    return `<span class="chip warn">${escapeHtml(id)} ${severity.toFixed(2)}</span>`;
  });
  els.survival.innerHTML = chips.join('');
  els.effects.innerHTML = effects.join('');

  const sceneId = str(player.scene_id, '无场景');
  els.position.textContent = `坐标 ${state.player.x.toFixed(1)}, ${state.player.y.toFixed(1)}, ${state.player.z.toFixed(1)} · ${sceneId}`;
}

function renderQuests(view) {
  const quests = list(view && view.quests);
  const done = quests.filter((item) => item && item.completed).length;
  els.questProgress.textContent = `${done}/${quests.length}`;
  if (!quests.length) {
    els.questList.innerHTML = '<div class="muted small">暂无任务</div>';
    return;
  }
  els.questList.innerHTML = quests.map((quest) => {
    const id = str(quest && quest.id);
    const completed = Boolean(quest && quest.completed);
    const label = str(quest && quest.action_label, str(quest && quest.action, '行动'));
    const sceneName = str(quest && quest.scene_name, '未知地点');
    const sceneId = str(quest && quest.scene_id, '');
    // 任务卡片配一张目标场景的概念原画，快速辨认任务地点。
    const thumb = sceneId
      ? `<img class="quest-thumb" src="/static/art_${escapeHtml(sceneId)}.jpg" alt="${escapeHtml(sceneName)}" loading="lazy" onerror="this.style.display='none'">`
      : '';
    return `<div class="quest">
      ${thumb}
      <div class="quest-main">
        <div class="row"><strong>${escapeHtml(str(quest && quest.title, '未命名任务'))}</strong>
          <span class="muted small">${escapeHtml(sceneName)}</span></div>
        <div class="desc">${escapeHtml(str(quest && quest.description, ''))}</div>
        <div class="foot"><span>${escapeHtml(label)} · ${num(quest && quest.duration_min, 0)} 分${
          quest && quest.tension_id ? ` · 矛盾 ${escapeHtml(quest.tension_id)}` : ''
        }</span>
        <button type="button" class="primary" data-quest="${escapeHtml(id)}" ${
          completed ? 'disabled' : ''
        }>${completed ? '已完成' : '前往并执行'}</button></div>
      </div>
    </div>`;
  }).join('');
}

// 概念原画集：标题原画 + 每个场景一张，图片缺失时自动隐藏该卡片。
let galleryKey = '';
function renderGallery(view) {
  if (!els.galleryBody) return;
  const scenes = list(view && view.scenes);
  const key = scenes.map((scene) => str(scene && scene.id)).join('|');
  if (key === galleryKey) return;
  galleryKey = key;
  const cards = [{
    id: 'title',
    name: '织梦 · 标题原画',
    desc: '世界主题概念图'
  }];
  for (const scene of scenes) {
    cards.push({
      id: str(scene && scene.id),
      name: str(scene && scene.name, str(scene && scene.id, '场景')),
      desc: str(scene && scene.category, '')
    });
  }
  els.galleryBody.innerHTML = `<div class="gallery-grid">${cards.map((card) => `
    <figure class="art-card">
      <img src="/static/art_${escapeHtml(card.id)}.jpg" alt="${escapeHtml(card.name)}" loading="lazy" onerror="this.closest('figure').style.display='none'">
      <figcaption><strong>${escapeHtml(card.name)}</strong><span>${escapeHtml(card.desc)}</span></figcaption>
    </figure>`).join('')}</div>`;
}

function tensionColor(stage) {
  const value = str(stage);
  if (value === 'open' || value === 'climax') return 'var(--bad)';
  if (value === 'escalating') return 'var(--warn)';
  return '#58c4ff';
}

function renderTensions(view) {
  const tensions = list(view && view.tensions);
  els.tensionCount.textContent = String(tensions.length);
  if (!tensions.length) {
    els.tensionList.innerHTML = '<div class="muted small">暂无矛盾</div>';
    return;
  }
  els.tensionList.innerHTML = tensions.map((item) => {
    const intensity = clamp(num(item && item.intensity, 0), 0, 1);
    return `<div class="tension">
      <div class="row"><span>${escapeHtml(str(item && item.id, '矛盾'))}
        <span class="tag">${escapeHtml(str(item && item.stake, '—'))}</span></span>
        <span class="meta">${escapeHtml(str(item && item.stage, '—'))} · ${intensity.toFixed(2)}</span></div>
      <div class="bar"><i style="width:${(intensity * 100).toFixed(1)}%;background:${tensionColor(item && item.stage)}"></i></div>
    </div>`;
  }).join('');
}

function renderChronicle(view) {
  const chronicle = list(view && view.chronicle);
  if (!chronicle.length) {
    els.chronicleList.innerHTML = '<div class="muted small">尚无记录</div>';
    return;
  }
  els.chronicleList.innerHTML = `<div class="list">${chronicle.slice().reverse().map((item) => `
    <div class="item"><time>第 ${num(item && item.day, 1)} 天</time>
      <span class="grow">${escapeHtml(str(item && item.summary, ''))}</span></div>
  `).join('')}</div>`;
}

function renderEvents(view) {
  const events = list(view && view.events).slice().reverse();
  if (!events.length) {
    els.eventList.innerHTML = '<div class="muted small">暂无事件</div>';
    return;
  }
  els.eventList.innerHTML = `<div class="list">${events.map((item) => `
    <div class="item"><time>${escapeHtml(timeLabel(item && item.minute))}</time>
      <span class="muted">${escapeHtml(EVENT_LABELS[str(item && item.type)] || str(item && item.type, '事件'))}</span>
      <span class="val grow">${escapeHtml(str(item && item.value, ''))}</span></div>
  `).join('')}</div>`;
}

function renderRecap(view) {
  const recap = (view && view.recap) || (view && view.timeflow && view.timeflow.recap) || null;
  if (!recap) {
    els.recapBody.innerHTML = '<div class="muted small">压缩推进后，这里回放关键感知节点。</div>';
    return;
  }
  const beats = list(recap.beats);
  const chips = [
    `事件 ${num(recap.event_count, beats.length)}`,
    `节拍 ${beats.length}`,
    `密度 ${num(recap.density, 0)}/天`,
    `主观 ${num(recap.subjective_days, 0)} 天`
  ].map((text) => `<span class="chip">${escapeHtml(text)}</span>`).join('');
  els.recapBody.innerHTML = `
    <div class="summary">${escapeHtml(str(recap.summary, '这段时间没有可感知事件'))}</div>
    <div class="chips">${chips}</div>
    <div class="list">${beats.slice().reverse().map((beat) => `
      <div class="item"><time>D${num(beat && beat.day, 1)} ${escapeHtml(str(beat && beat.time, '00:00'))}</time>
        <span class="muted">${escapeHtml(str(beat && beat.label, ''))}</span>
        <span class="val grow">${escapeHtml(str(beat && beat.value, ''))}</span></div>
    `).join('') || '<div class="muted small">没有可感知事件</div>'}</div>`;
}

function renderActions(view) {
  const actions = list(view && view.actions).slice().reverse();
  if (!actions.length) {
    els.actionList.innerHTML = '<div class="muted small">尚未执行任务</div>';
    return;
  }
  els.actionList.innerHTML = `<div class="list">${actions.map((item) => `
    <div class="item"><time>D${num(item && item.day, 1)} ${escapeHtml(str(item && item.time, '00:00'))}</time>
      <span class="grow">${escapeHtml(str(item && item.title, ''))}</span>
      <span class="muted">${num(item && item.travel_min, 0)}+${num(item && item.duration_min, 0)} 分</span></div>
  `).join('')}</div>`;
}

export function updateSceneLabel() {
  let title = '世界边界';
  const local = sceneAt(state.player.x, state.player.z);
  if (local) {
    title = str(local.scene.name, str(local.scene.id, '未知场景'));
  } else {
    const serverSceneId = str(state.view && state.view.player && state.view.player.scene_id);
    const found = state.scenes.find((scene) => str(scene.id) === serverSceneId);
    if (found) title = str(found.name, serverSceneId);
  }
  if (title !== state.currentSceneTitle) {
    state.currentSceneTitle = title;
    els.sceneName.textContent = title;
  }
}

function renderView(view) {
  if (!view || typeof view !== 'object') return;
  state.view = view;

  const world = view.world || {};
  els.worldName.textContent = `${str(world.name, str(world.id, '未知世界'))} · ${str(world.template, '世界')}`;
  const time = view.time || {};
  els.clock.textContent = `第 ${num(time.day, 1)} 天 ${str(time.clock, '00:00')}`;
  const weatherInfo = view.weather || {};
  const weatherLabel = str(weatherInfo.label, WEATHER_LABELS[str(weatherInfo.kind)] || str(weatherInfo.kind, '晴'));
  els.weatherLabel.textContent = `${weatherLabel} · ${num(weatherInfo.temperature_c, 0).toFixed(1)}℃`;

  const camera = view.camera || {};
  state.eyeHeight = num(camera.eye_height_m, EYE_DEFAULT);
  state.thirdDistance = num(camera.third_person_distance_m, THIRD_DISTANCE_DEFAULT);

  const scenes = list(view.scenes);
  state.carried = view.carried && typeof view.carried === 'object' ? view.carried : null;
  renderCarry();
  // 场景条目每次视图都刷新（开销小），几何上传只在内容变化时重建。
  rebuildSceneCache(scenes);
  const signature = `${sceneSignature(scenes)}#${state.carried ? str(state.carried.id) : ''}`;
  if (signature !== state.staticSignature) {
    uploadStatic(scenes);
    state.staticSignature = signature;
  }

  syncServerFeet(view);
  renderVitals(view);
  renderQuests(view);
  renderTensions(view);
  renderChronicle(view);
  renderEvents(view);
  renderRecap(view);
  renderActions(view);
  renderGallery(view);
  updateSceneLabel();
}

/* ---------------- 网络请求 ---------------- */

export function toast(text, isError = false) {
  if (!els.toast) return;
  els.toast.textContent = str(text, '');
  els.toast.className = `show${isError ? ' error' : ''}`;
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => { els.toast.className = ''; }, 3600);
}

export function setLoading(active, text) {
  if (!els.loading) return;
  els.loading.classList.toggle('show', Boolean(active));
  if (text) els.loadingText.textContent = text;
}

export async function requestJson(path, options) {
  const response = await fetch(path, options || {});
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { error: '响应不是有效 JSON' };
    }
  }
  if (!response.ok) throw new Error(str(data && data.error, `请求失败（${response.status}）`));
  return data;
}

export async function refreshView(options = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const view = await requestJson('/api/view', { cache: 'no-store' });
    renderView(view);
    if (!state.loaded) {
      state.loaded = true;
      setLoading(false);
    }
    if (options.toastOnError) state.lastErrorShown = false;
  } catch (error) {
    if (!state.loaded) {
      setLoading(true, '连接服务失败，正在重试');
    } else if (!options.silent) {
      toast(str(error && error.message, '刷新失败'), true);
    }
  } finally {
    state.refreshing = false;
  }
}

export async function postAction(path, body, label) {
  if (state.busy) return;
  state.busy = true;
  setLoading(true, label);
  try {
    const result = await requestJson(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    await refreshView({ silent: true });
    toast(str(result && result.message, '完成'), Boolean(result && result.accepted === false));
  } catch (error) {
    toast(str(error && error.message, '请求失败'), true);
  } finally {
    state.busy = false;
    setLoading(!state.loaded, state.loaded ? '' : '等待后端服务');
  }
}
