'use strict';
/* 织梦 3D 世界：核心常量、数学工具、DOM 引用与共享状态。
   以原生 ES 模块拆分为多个文件，无依赖、无构建步骤。 */

export const TAU = Math.PI * 2;
export const DEG = Math.PI / 180;
export const PROPS_PER_SCENE = 90;
export const MAX_VERTEX_FLOATS = 3_000_000;
export const EYE_DEFAULT = 1.65;
export const THIRD_DISTANCE_DEFAULT = 5.4;
export const WALK_SPEED = 4.5;
export const RUN_SPEED = 8.0;
export const PITCH_LIMIT = 85 * DEG;
export const NPC_TAG_DISTANCE = 240;

export const NIGHT_COLOR = [0.027, 0.043, 0.071];   // #070b12
export const DAWN_COLOR = [0.788, 0.482, 0.290];    // #c97b4a
export const DAY_COLOR = [0.498, 0.722, 0.910];     // #7fb8e8

export const WEATHER_LABELS = {
  clear: '晴', rain: '雨', snow: '雪', storm: '风暴', fog: '雾',
  heatwave: '热浪', coldwave: '寒潮', sandstorm: '沙尘暴',
  hurricane: '飓风', aurora: '极光'
};
export const EVENT_LABELS = {
  weather: '天气', npc_move: '迁移', npc_activity: '日程', effect_on: '状态',
  effect_off: '恢复', ecology: '生态', survival: '生存'
};
export const FALLBACK_PALETTE = {
  built: ['#4a4f56', '#58c4ff'],
  natural: ['#4f7a45', '#8fd3a0'],
  transition: ['#3f4a55', '#58c4ff'],
  extraterrestrial: ['#5b5f66', '#c7d2fe'],
  fantasy: ['#3d4a63', '#a78bfa']
};

/* ---------------- 基础工具 ---------------- */

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const num = (v, fallback = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);
export const str = (v, fallback = '') => (v === null || v === undefined ? fallback : String(v));
export const list = (v) => (Array.isArray(v) ? v : []);

export function vec3(value, fallback = null) {
  if (!Array.isArray(value) || value.length < 3) return fallback;
  const out = [Number(value[0]), Number(value[1]), Number(value[2])];
  return out.every(Number.isFinite) ? out : fallback;
}

export function escapeHtml(value) {
  return str(value).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
  ));
}

export function hexToRgb(hex, fallback) {
  const match = /^#?([0-9a-fA-F]{6})$/.exec(str(hex, '').trim());
  if (!match) return (fallback || [0.32, 0.36, 0.4]).slice();
  const n = parseInt(match[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function shade(color, factor) {
  return [
    clamp(color[0] * factor, 0, 1),
    clamp(color[1] * factor, 0, 1),
    clamp(color[2] * factor, 0, 1)
  ];
}

export function mixColor(a, b, t) {
  const k = clamp(t, 0, 1);
  return [
    a[0] + (b[0] - a[0]) * k,
    a[1] + (b[1] - a[1]) * k,
    a[2] + (b[2] - a[2]) * k
  ];
}

export function timeLabel(minute) {
  const m = Math.max(0, Math.floor(num(minute, 0)));
  const day = Math.floor(m / 1440) + 1;
  const rest = m % 1440;
  return `D${day} ${String(Math.floor(rest / 60)).padStart(2, '0')}:${String(rest % 60).padStart(2, '0')}`;
}

export function hashString(value) {
  const text = str(value);
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function random() {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ---------------- 矩阵：列主序，与 WebGL uniformMatrix4fv 一致 ---------------- */

export function mat4Identity(out) {
  out.fill(0);
  out[0] = 1; out[5] = 1; out[10] = 1; out[15] = 1;
  return out;
}

export function mat4Multiply(out, a, b) {
  for (let col = 0; col < 4; col += 1) {
    const b0 = b[col * 4], b1 = b[col * 4 + 1], b2 = b[col * 4 + 2], b3 = b[col * 4 + 3];
    out[col * 4] = a[0] * b0 + a[4] * b1 + a[8] * b2 + a[12] * b3;
    out[col * 4 + 1] = a[1] * b0 + a[5] * b1 + a[9] * b2 + a[13] * b3;
    out[col * 4 + 2] = a[2] * b0 + a[6] * b1 + a[10] * b2 + a[14] * b3;
    out[col * 4 + 3] = a[3] * b0 + a[7] * b1 + a[11] * b2 + a[15] * b3;
  }
  return out;
}

export function mat4Perspective(out, fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  out.fill(0);
  out[0] = f / Math.max(aspect, 0.0001);
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}

export function mat4LookAt(out, eye, center, up) {
  let zx = eye[0] - center[0];
  let zy = eye[1] - center[1];
  let zz = eye[2] - center[2];
  let len = Math.hypot(zx, zy, zz) || 1;
  zx /= len; zy /= len; zz /= len;

  let xx = up[1] * zz - up[2] * zy;
  let xy = up[2] * zx - up[0] * zz;
  let xz = up[0] * zy - up[1] * zx;
  len = Math.hypot(xx, xy, xz) || 1;
  xx /= len; xy /= len; xz /= len;

  const yx = zy * xz - zz * xy;
  const yy = zz * xx - zx * xz;
  const yz = zx * xy - zy * xx;

  out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
  out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
  out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
  out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
  out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
  out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
  out[15] = 1;
  return out;
}

/* ---------------- DOM 与运行状态 ---------------- */

export const els = {
  canvas: document.getElementById('gl-canvas'),
  weather: document.getElementById('weather-layer'),
  npcTags: document.getElementById('npc-tags'),
  questMarkers: document.getElementById('quest-markers'),
  crosshair: document.getElementById('crosshair'),
  worldName: document.getElementById('world-name'),
  clock: document.getElementById('clock'),
  weatherLabel: document.getElementById('weather-label'),
  sceneName: document.getElementById('scene-name'),
  vitals: document.getElementById('vitals'),
  survival: document.getElementById('survival-chips'),
  effects: document.getElementById('effects'),
  position: document.getElementById('position-line'),
  questProgress: document.getElementById('quest-progress'),
  questList: document.getElementById('quest-list'),
  tensionCount: document.getElementById('tension-count'),
  tensionList: document.getElementById('tension-list'),
  chronicleList: document.getElementById('chronicle-list'),
  eventList: document.getElementById('event-list'),
  recapBody: document.getElementById('recap-body'),
  actionList: document.getElementById('action-list'),
  galleryBody: document.getElementById('gallery-body'),
  hint: document.getElementById('hint'),
  toast: document.getElementById('toast'),
  interact: document.getElementById('interact'),
  interactTarget: document.getElementById('interact-target'),
  interactVerbs: document.getElementById('interact-verbs'),
  dialogue: document.getElementById('dialogue'),
  dialogueSpeaker: document.getElementById('dialogue-speaker'),
  dialogueLine: document.getElementById('dialogue-line'),
  loading: document.getElementById('loading'),
  loadingText: document.getElementById('loading-text'),
  webglError: document.getElementById('webgl-error'),
  uiToggle: document.getElementById('ui-toggle'),
  controlsToggle: document.getElementById('controls-toggle'),
  carryChip: document.getElementById('carry-chip'),
  carryName: document.getElementById('carry-name'),
  noteEditor: document.getElementById('note-editor'),
  noteInput: document.getElementById('note-input'),
  noteCount: document.getElementById('note-count'),
  noteSubmit: document.getElementById('note-submit'),
  noteCancel: document.getElementById('note-cancel')
};

export const state = {
  view: null,
  loaded: false,
  busy: false,
  refreshing: false,
  scenes: [],
  sceneEntries: [],
  staticSignature: '',
  serverFeet: null,
  spawned: false,
  player: { x: 0, y: 0, z: 0 },
  yaw: 0,
  pitch: -0.04,
  thirdPerson: false,
  eyeHeight: EYE_DEFAULT,
  thirdDistance: THIRD_DISTANCE_DEFAULT,
  keys: Object.create(null),
  sprint: false,
  locked: false,
  dragging: false,
  currentSceneTitle: '',
  mvp: mat4Identity(new Float32Array(16)),
  cameraPos: [0, 2, 6],
  npcTags: new Map(),
  lastFrame: 0,
  toastTimer: 0,
  target: null,
  targetCheckAt: 0,
  dialogueTimer: 0,
  danceNpc: null,
  danceUntil: 0,
  hitNpc: null,
  hitUntil: 0,
  cloudOffset: 0,
  interacting: false,
  carried: null,
  noteEditing: false,
  noteTarget: null,
  controlsHidden: false
};
