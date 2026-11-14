'use strict';
/* 输入：鼠标视角、键盘 / 触屏移动、方向键显隐偏好与动作按钮。 */

import {
  WALK_SPEED, RUN_SPEED, PITCH_LIMIT,
  clamp, num, els, state
} from './core.js';
import { obstacles, sceneAt, floorAt } from './geometry.js';
import { toast, postAction } from './hud.js';
import {
  ACTION_KEY_SLOTS, triggerAction, dropCarried,
  closeNoteEditor, submitNote, updateNoteCount
} from './interact.js';
import { resizeGL, resizeWeather } from './render.js';

function applyLook(dx, dy) {
  state.yaw += num(dx, 0) * 0.0022;
  state.pitch = clamp(state.pitch - num(dy, 0) * 0.0022, -PITCH_LIMIT, PITCH_LIMIT);
}

// 触屏设备：用拖动画面转视角，敌友 UI 均不受影响。
function isCoarsePointer() {
  return window.matchMedia('(pointer: coarse)').matches;
}

// 疾跑开关：键盘 Shift 与触屏按钮共用同一状态，保持按钮高亮同步。
function setSprint(on) {
  state.sprint = Boolean(on);
  const button = document.getElementById('sprint-toggle');
  if (button) {
    button.classList.toggle('active', state.sprint);
    button.textContent = state.sprint ? '跑 · 开' : '跑';
  }
}

function toggleView() {
  state.thirdPerson = !state.thirdPerson;
  els.crosshair.classList.toggle('off', state.thirdPerson);
  toast(state.thirdPerson ? '第三人称视角' : '第一人称视角');
}

function updateHint() {
  if (!els.hint) return;
  els.hint.textContent = state.locked
    ? 'Esc 释放鼠标 · WASD 移动 · Shift 疾跑 · E/F/R/Q 交互 · V 切换视角'
    : '点击画面锁定鼠标 · WASD 移动 · Shift 疾跑 · E/F/R/Q 交互 · V 切换视角';
}

// 屏幕方向键显隐：隐藏后仍可用键盘 WASD，偏好写入本地存储。
function controlsVisible() {
  const pad = document.getElementById('touch-controls');
  return Boolean(pad) && window.getComputedStyle(pad).display !== 'none';
}

function updateControlsToggleLabel() {
  if (els.controlsToggle) {
    els.controlsToggle.textContent = controlsVisible() ? '方向键 · 隐藏' : '方向键 · 显示';
  }
}

function setControlsHidden(hidden, force) {
  state.controlsHidden = Boolean(hidden);
  document.body.classList.toggle('controls-hidden', state.controlsHidden);
  document.body.classList.toggle('controls-forced', !state.controlsHidden && Boolean(force));
  updateControlsToggleLabel();
  try {
    window.localStorage.setItem('dw.controlsHidden', state.controlsHidden ? '1' : '0');
  } catch (error) { /* 隐私模式下忽略 */ }
}

export function applyControlsPreference() {
  let stored = null;
  try {
    stored = window.localStorage.getItem('dw.controlsHidden');
  } catch (error) { /* 忽略 */ }
  // 首次进入沿用样式表的默认可见性；之后按上次的选择恢复。
  if (stored === '1') setControlsHidden(true);
  else if (stored === '0') setControlsHidden(false, true);
  else updateControlsToggleLabel();

  // 手机/平板默认收起数据面板，把屏幕留给 3D 画面，点“显示面板”即可展开。
  if (!stored && (isCoarsePointer() || window.innerWidth <= 820)) {
    document.body.classList.add('hud-hidden');
    if (els.uiToggle) els.uiToggle.textContent = '显示面板';
  }
}

export function bindInput() {
  const canvas = els.canvas;
  canvas.addEventListener('click', () => {
    if (isCoarsePointer()) return; // 触屏用拖动转视角，无需指针锁定
    if (document.pointerLockElement === canvas) return;
    if (canvas.requestPointerLock) {
      const result = canvas.requestPointerLock();
      if (result && typeof result.catch === 'function') result.catch(() => {});
    }
  });

  document.addEventListener('pointerlockchange', () => {
    state.locked = document.pointerLockElement === canvas;
    if (!state.locked) state.dragging = false;
    updateHint();
  });

  document.addEventListener('mousemove', (event) => {
    if (state.locked) {
      applyLook(event.movementX, event.movementY);
    } else if (state.dragging) {
      applyLook(event.movementX, event.movementY);
    }
  });

  canvas.addEventListener('mousedown', (event) => {
    if (!state.locked && event.button === 0) state.dragging = true;
  });
  window.addEventListener('mouseup', () => { state.dragging = false; });

  // 触屏视角：单指在画面上滑动即可转动镜头（移动端主流做法）。
  let lookPointer = null;
  let lookX = 0;
  let lookY = 0;
  canvas.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'mouse' || state.noteEditing) return;
    lookPointer = event.pointerId;
    lookX = event.clientX;
    lookY = event.clientY;
    if (canvas.setPointerCapture) {
      try { canvas.setPointerCapture(event.pointerId); } catch (error) { /* 忽略 */ }
    }
    event.preventDefault();
  });
  canvas.addEventListener('pointermove', (event) => {
    if (event.pointerId !== lookPointer) return;
    // 触屏像素密度更高，乘一个系数让转动幅度接近鼠标手感。
    applyLook((event.clientX - lookX) * 1.7, (event.clientY - lookY) * 1.7);
    lookX = event.clientX;
    lookY = event.clientY;
    event.preventDefault();
  });
  const endTouchLook = (event) => {
    if (event.pointerId !== lookPointer) return;
    lookPointer = null;
  };
  canvas.addEventListener('pointerup', endTouchLook);
  canvas.addEventListener('pointercancel', endTouchLook);

  window.addEventListener('blur', () => {
    state.dragging = false;
    state.keys = Object.create(null);
    setSprint(false);
    highlightKeys();
  });

  const keyMap = {
    KeyW: 'forward', KeyS: 'back', KeyA: 'left', KeyD: 'right',
    ArrowUp: 'forward', ArrowDown: 'back', ArrowLeft: 'left', ArrowRight: 'right'
  };
  // 键盘状态同步高亮屏幕 WASD 键，触屏与键盘共用同一套移动语义。
  function highlightKeys() {
    document.querySelectorAll('#touch-controls [data-move]').forEach((button) => {
      button.classList.toggle('active', Boolean(state.keys[button.dataset.move]));
    });
  }
  window.addEventListener('keydown', (event) => {
    // 书写告示时把键盘交给输入框，Esc 退出编辑。
    if (state.noteEditing) {
      if (event.code === 'Escape') {
        event.preventDefault();
        closeNoteEditor();
      }
      return;
    }
    if (event.target && (event.target.tagName === 'TEXTAREA' || event.target.tagName === 'INPUT')) return;
    if (event.repeat) return;
    if (event.code === 'KeyV') { toggleView(); return; }
    if (event.code === 'ShiftLeft' || event.code === 'ShiftRight') {
      setSprint(true);
      return;
    }
    const slot = ACTION_KEY_SLOTS[event.code];
    if (slot !== undefined) {
      const target = state.target;
      const verb = target && target.verbs[slot];
      if (verb) {
        event.preventDefault();
        triggerAction(verb.action);
      } else if (event.code === 'KeyR' && state.carried) {
        // 没有可交互目标时，R 直接放下肩上的物件。
        event.preventDefault();
        dropCarried();
      }
      return;
    }
    const action = keyMap[event.code];
    if (action) {
      state.keys[action] = true;
      highlightKeys();
      event.preventDefault();
    }
  });
  window.addEventListener('keyup', (event) => {
    if (event.code === 'ShiftLeft' || event.code === 'ShiftRight') {
      setSprint(false);
      return;
    }
    const action = keyMap[event.code];
    if (action) {
      state.keys[action] = false;
      highlightKeys();
    }
  });

  document.querySelectorAll('[data-tick]').forEach((button) => {
    button.addEventListener('click', () => {
      const minutes = clamp(Math.round(num(Number(button.dataset.tick), 60)), 1, 43200);
      postAction('/api/tick', { minutes, step: 60 }, `推进 ${minutes} 分钟`);
    });
  });

  const advance = document.getElementById('advance');
  if (advance) advance.addEventListener('click', () => postAction('/api/advance', { days: 1 }, '推进剧情 1 天'));

  const reset = document.getElementById('reset');
  if (reset) reset.addEventListener('click', () => postAction('/api/reset', {}, '重置世界'));

  if (els.uiToggle) {
    els.uiToggle.addEventListener('click', () => {
      const hidden = document.body.classList.toggle('hud-hidden');
      els.uiToggle.textContent = hidden ? '显示面板' : '隐藏面板';
    });
  }

  if (els.controlsToggle) {
    els.controlsToggle.addEventListener('click', () => setControlsHidden(!state.controlsHidden, true));
  }

  if (els.noteSubmit) els.noteSubmit.addEventListener('click', submitNote);
  if (els.noteCancel) els.noteCancel.addEventListener('click', closeNoteEditor);
  if (els.noteInput) {
    els.noteInput.addEventListener('input', updateNoteCount);
    els.noteInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        submitNote();
      }
    });
  }

  document.addEventListener('click', (event) => {
    const interactButton = event.target.closest('[data-interact]');
    if (interactButton) {
      triggerAction(interactButton.dataset.interact);
      return;
    }
    const dropButton = event.target.closest('[data-drop]');
    if (dropButton) {
      dropCarried();
      return;
    }
    const questButton = event.target.closest('[data-quest]');
    if (questButton && !questButton.disabled) {
      postAction('/api/act', { quest_id: questButton.dataset.quest }, '前往并执行任务');
      return;
    }
    const flowButton = event.target.closest('[data-timeflow]');
    if (flowButton && !flowButton.disabled) {
      const days = clamp(Math.round(num(Number(flowButton.dataset.timeflow), 1)), 1, 30);
      postAction('/api/timeflow', { days, step: 60 }, `压缩推进 ${days} 天`);
    }
  });

  document.querySelectorAll('#touch-controls [data-move]').forEach((button) => {
    const direction = button.dataset.move;
    const press = (event) => {
      event.preventDefault();
      button.classList.add('active');
      if (direction === 'forward') state.keys.forward = true;
      else if (direction === 'back') state.keys.back = true;
      else if (direction === 'left') state.keys.left = true;
      else if (direction === 'right') state.keys.right = true;
      if (button.setPointerCapture && event.pointerId !== undefined) {
        try { button.setPointerCapture(event.pointerId); } catch (error) { /* 忽略 */ }
      }
    };
    const release = () => {
      button.classList.remove('active');
      if (direction === 'forward') state.keys.forward = false;
      else if (direction === 'back') state.keys.back = false;
      else if (direction === 'left') state.keys.left = false;
      else if (direction === 'right') state.keys.right = false;
    };
    button.addEventListener('pointerdown', press);
    button.addEventListener('pointerup', release);
    button.addEventListener('pointercancel', release);
    button.addEventListener('pointerleave', release);
    button.addEventListener('contextmenu', (event) => event.preventDefault());
  });

  const viewToggle = document.getElementById('view-toggle');
  if (viewToggle) viewToggle.addEventListener('click', toggleView);

  const sprintToggle = document.getElementById('sprint-toggle');
  if (sprintToggle) sprintToggle.addEventListener('click', () => setSprint(!state.sprint));

  window.addEventListener('resize', () => {
    resizeGL();
    resizeWeather();
    updateControlsToggleLabel();
  });

  window.addEventListener('beforeunload', () => {
    if (document.pointerLockElement === canvas) document.exitPointerLock();
  });
}

/* ---------------- 玩家移动 ---------------- */

export function updatePlayer(dt) {
  if (!state.loaded) return;

  const forwardX = Math.sin(state.yaw);
  const forwardZ = -Math.cos(state.yaw);
  const rightX = Math.cos(state.yaw);
  const rightZ = Math.sin(state.yaw);

  let moveX = 0;
  let moveZ = 0;
  if (state.keys.forward) { moveX += forwardX; moveZ += forwardZ; }
  if (state.keys.back) { moveX -= forwardX; moveZ -= forwardZ; }
  if (state.keys.left) { moveX -= rightX; moveZ -= rightZ; }
  if (state.keys.right) { moveX += rightX; moveZ += rightZ; }

  const length = Math.hypot(moveX, moveZ);
  if (length > 0.0001) {
    const carryPenalty = state.carried ? 0.82 : 1;
    const speed = (state.sprint ? RUN_SPEED : WALK_SPEED) * carryPenalty;
    const stepX = (moveX / length) * speed * dt;
    const stepZ = (moveZ / length) * speed * dt;
    const x = state.player.x;
    const z = state.player.z;
    const blockedAt = (px, pz) => {
      for (let i = 0; i < obstacles.length; i += 1) {
        const o = obstacles[i];
        if (px > o.x0 && px < o.x1 && pz > o.z0 && pz < o.z1) return true;
      }
      return false;
    };
    // 意外落进建筑内（任务传送等）时允许先走出来，避免卡死。
    const stuck = blockedAt(x, z);
    const free = (px, pz) => sceneAt(px, pz) && (stuck || !blockedAt(px, pz));

    // 只允许停留在场景 bounds 并集内；分轴尝试可沿边界与墙面滑行。
    if (free(x + stepX, z + stepZ)) {
      state.player.x = x + stepX;
      state.player.z = z + stepZ;
    } else if (free(x + stepX, z)) {
      state.player.x = x + stepX;
    } else if (free(x, z + stepZ)) {
      state.player.z = z + stepZ;
    }
  }

  const floor = floorAt(state.player.x, state.player.z);
  if (Number.isFinite(floor)) state.player.y = floor;
}

export { updateHint };
