'use strict';
/* 织梦 3D 世界入口：初始化 WebGL、输入与 HUD，然后驱动渲染主循环。 */

import { num, str, list, mat4Identity, els, state } from './core.js';
import {
  initGL, hasGL, showWebglError, resizeGL, resizeWeather,
  updateWeather, renderScene, updateNpcTags, updateQuestMarkers
} from './render.js';
import { updateInteractionTarget } from './interact.js';
import { syncNpcMotion } from './npc.js';
import { updateSceneLabel, refreshView } from './hud.js';
import {
  bindInput, updatePlayer, applyControlsPreference, updateHint
} from './input.js';

/* ---------------- 主循环 ---------------- */

function updatePositionLine() {
  if (!els.position || !state.view) return;
  const sceneId = str(state.view.player && state.view.player.scene_id, '无场景');
  const text = `坐标 ${state.player.x.toFixed(1)}, ${state.player.y.toFixed(1)}, ${state.player.z.toFixed(1)} · ${sceneId}`;
  if (els.position.textContent !== text) els.position.textContent = text;
}

function frame(timestamp) {
  const now = num(timestamp, 0);
  const delta = state.lastFrame ? Math.min(0.05, Math.max(0, (now - state.lastFrame) / 1000)) : 0;
  state.lastFrame = now;
  state.cloudOffset += delta;

  updateWeather(delta);
  updatePlayer(delta);
  syncNpcMotion(state.view && list(state.view.npcs), delta);
  updateInteractionTarget(now);
  renderScene();
  updateNpcTags();
  updateQuestMarkers();
  updateSceneLabel();
  updatePositionLine();

  window.requestAnimationFrame(frame);
}

/* ---------------- 启动 ---------------- */

function boot() {
  try {
    initGL();
  } catch (error) {
    showWebglError(str(error && error.message, 'WebGL 初始化失败'));
  }
  if (!hasGL()) {
    if (els.webglError && els.webglError.hidden) showWebglError('浏览器未返回 WebGL 上下文');
  }

  resizeGL();
  resizeWeather();
  mat4Identity(state.mvp);
  bindInput();
  applyControlsPreference();
  updateHint();

  refreshView({ silent: true });
  window.setInterval(() => refreshView({ silent: true }), 5000);
  window.requestAnimationFrame(frame);
}

boot();
