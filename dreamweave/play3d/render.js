'use strict';
/* WebGL 渲染：着色器、天空与光照、动态几何、天气叠加层、NPC 投影标签。 */

import {
  TAU, DEG, EYE_DEFAULT, NPC_TAG_DISTANCE,
  NIGHT_COLOR, DAWN_COLOR, DAY_COLOR,
  clamp, num, str, list, vec3, shade, mixColor, hexToRgb, hashString, mulberry32,
  mat4LookAt, mat4Multiply, mat4Perspective,
  els, state
} from './core.js';
import {
  addBoxTo, addHumanoidTo, buildWorldGeometry, floorAt, npcFootY,
  pushGradientQuad, pushQuad, pushTri
} from './geometry.js';
import { npcRenderPosition } from './npc.js';
import { createAtlasTexture, TILE } from './texture.js';

/* ---------------- WebGL 初始化 ---------------- */

const VERTEX_SHADER = `
  attribute vec3 aPosition;
  attribute vec3 aNormal;
  attribute vec3 aColor;
  attribute float aTile;
  uniform mat4 uMVP;
  uniform vec3 uCameraPos;
  uniform vec3 uSunDir;
  uniform float uAmbient;
  uniform float uSunStrength;
  uniform float uFogStart;
  uniform float uFogEnd;
  uniform float uUnlit;
  uniform float uFogEnabled;
  uniform float uTexRepeat;
  varying vec3 vColor;
  varying vec3 vNormal;
  varying vec3 vWorld;
  varying float vFog;
  varying vec2 vUv;
  varying float vTile;
  void main() {
    gl_Position = uMVP * vec4(aPosition, 1.0);
    vec3 n = normalize(aNormal);
    vNormal = n;
    vWorld = aPosition;
    // 三平面投影：按法线主轴向选择世界坐标对，让贴图在顶面/立面都自然平铺。
    vec3 blend = abs(n);
    vec2 uv = (blend.y > blend.x && blend.y > blend.z) ? aPosition.xz
            : ((blend.x > blend.z) ? aPosition.zy : aPosition.xy);
    vUv = uv * uTexRepeat;
    vTile = aTile;
    float diff = max(dot(n, uSunDir), 0.0);
    float hemi = 0.58 + 0.42 * max(n.y, 0.0);
    float light = uAmbient * hemi + diff * uSunStrength;
    vColor = aColor * mix(light + 0.07, 1.0, uUnlit);
    float dist = distance(aPosition, uCameraPos);
    vFog = clamp((dist - uFogStart) / max(uFogEnd - uFogStart, 1.0), 0.0, 1.0) * uFogEnabled;
  }
`;

const FRAGMENT_SHADER = `
  precision mediump float;
  varying vec3 vColor;
  varying vec3 vNormal;
  varying vec3 vWorld;
  varying float vFog;
  varying vec2 vUv;
  varying float vTile;
  uniform vec3 uFogColor;
  uniform highp vec3 uCameraPos;
  uniform highp vec3 uSunDir;
  uniform sampler2D uAtlas;
  uniform float uTexOn;
  uniform float uNight;
  uniform float uWet;
  void main() {
    vec3 albedo = vColor;
    if (uTexOn > 0.5) {
      // 4×4 图集：平铺 UV 取小数部分，再偏移到对应材质格。
      // 内缩半纹素，避免相邻材质格在双线性/mipmap 采样时互相渗色。
      vec2 within = fract(vUv) * 0.984375 + 0.0078125;
      vec2 cell = vec2(mod(vTile, 4.0), floor(vTile / 4.0));
      vec2 tc = (within + cell) / 4.0;
      vec3 tex = texture2D(uAtlas, tc).rgb;
      albedo = vColor * tex;
      // 夜间亮窗：玻璃幕墙贴图里的高亮窗格自发光，像城市夜景一样亮起。
      if (abs(vTile - 5.0) < 0.5 && uNight > 0.01) {
        float luma = dot(tex, vec3(0.3333));
        float glow = smoothstep(0.62, 0.95, luma);
        albedo += vec3(1.0, 0.74, 0.38) * glow * uNight * 0.9;
      }
    }
    // 高光：玻璃幕墙、水面与湿路面按 Blinn-Phong 提亮，增强材质区分度。
    float specK = 0.0;
    float specPow = 24.0;
    if (abs(vTile - 5.0) < 0.5) { specK = 0.5; specPow = 72.0; }
    else if (abs(vTile - 10.0) < 0.5) { specK = 0.42; specPow = 48.0; }
    else if (abs(vTile - 1.0) < 0.5) { specK = 0.08 + uWet * 0.4; specPow = 18.0; }
    if (specK > 0.001) {
      vec3 viewDir = normalize(uCameraPos - vWorld);
      vec3 halfDir = normalize(uSunDir + viewDir);
      float spec = pow(max(dot(normalize(vNormal), halfDir), 0.0), specPow);
      albedo += vec3(0.85, 0.9, 1.0) * spec * specK;
    }
    gl_FragColor = vec4(mix(albedo, uFogColor, vFog), 1.0);
  }
`;

let gfx = null;

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const info = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(info || '着色器编译失败');
  }
  return shader;
}

export function initGL() {
  const canvas = els.canvas;
  if (!canvas) return null;
  const gl = canvas.getContext('webgl', {
    antialias: true,
    alpha: false,
    depth: true,
    powerPreference: 'high-performance'
  }) || canvas.getContext('experimental-webgl');
  if (!gl) return null;

  const program = gl.createProgram();
  gl.attachShader(program, compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
  gl.attachShader(program, compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || '着色器链接失败');
  }
  gl.useProgram(program);

  const loc = {
    position: gl.getAttribLocation(program, 'aPosition'),
    normal: gl.getAttribLocation(program, 'aNormal'),
    color: gl.getAttribLocation(program, 'aColor'),
    tile: gl.getAttribLocation(program, 'aTile'),
    mvp: gl.getUniformLocation(program, 'uMVP'),
    cameraPos: gl.getUniformLocation(program, 'uCameraPos'),
    sunDir: gl.getUniformLocation(program, 'uSunDir'),
    ambient: gl.getUniformLocation(program, 'uAmbient'),
    sunStrength: gl.getUniformLocation(program, 'uSunStrength'),
    fogColor: gl.getUniformLocation(program, 'uFogColor'),
    fogStart: gl.getUniformLocation(program, 'uFogStart'),
    fogEnd: gl.getUniformLocation(program, 'uFogEnd'),
    unlit: gl.getUniformLocation(program, 'uUnlit'),
    fogEnabled: gl.getUniformLocation(program, 'uFogEnabled'),
    atlas: gl.getUniformLocation(program, 'uAtlas'),
    texOn: gl.getUniformLocation(program, 'uTexOn'),
    texRepeat: gl.getUniformLocation(program, 'uTexRepeat'),
    night: gl.getUniformLocation(program, 'uNight'),
    wet: gl.getUniformLocation(program, 'uWet')
  };

  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.disable(gl.CULL_FACE);
  gl.disable(gl.BLEND);
  gl.clearColor(NIGHT_COLOR[0], NIGHT_COLOR[1], NIGHT_COLOR[2], 1);

  let atlas = null;
  let atlasError = '';
  try {
    atlas = createAtlasTexture(gl);
  } catch (error) {
    // 贴图图集生成失败时退化为纯顶点色渲染。
    atlasError = str(error && error.message, String(error));
    console.warn('贴图图集生成失败，退化为顶点色渲染：', atlasError);
    atlas = null;
  }

  gfx = {
    gl,
    canvas,
    program,
    loc,
    atlas,
    staticBuffer: gl.createBuffer(),
    dynamicBuffer: gl.createBuffer(),
    skyBuffer: gl.createBuffer(),
    staticCount: 0,
    pixelRatio: 0
  };
  // 调试钩子：控制台可检查渲染器状态（贴图图集是否生成、静态几何规模）。
  window.__dwGfx = { atlas: Boolean(atlas), atlasError, staticVertices: 0 };
  return gfx;
}

export function hasGL() {
  return Boolean(gfx);
}

export function showWebglError(detail) {
  if (els.loading) els.loading.classList.remove('show');
  if (els.webglError) {
    els.webglError.hidden = false;
    if (detail) {
      const tiny = els.webglError.querySelector('.tiny');
      if (tiny) tiny.textContent = `技术信息：${detail}。世界数据与时间推进仍由后端提供。`;
    }
  }
}

/* ---------------- 天气粒子叠加层 ---------------- */

const weather = {
  ctx: null,
  width: 0,
  height: 0,
  dpr: 1,
  kind: '',
  particles: [],
  flash: 0
};

export function resizeWeather() {
  const canvas = els.weather;
  if (!canvas) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(1, window.innerWidth);
  const h = Math.max(1, window.innerHeight);
  if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
  }
  weather.width = w;
  weather.height = h;
  weather.dpr = dpr;
  if (!weather.ctx) weather.ctx = canvas.getContext('2d');
  if (weather.ctx) weather.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function weatherIntensity() {
  return clamp(num(state.view && state.view.weather && state.view.weather.intensity, 1), 0.3, 1.8);
}

function particleCount(kind) {
  const intensity = weatherIntensity();
  const base = kind === 'storm' ? 340 : kind === 'snow' ? 170 : 220;
  return Math.round(base * intensity);
}

function seedParticles(kind) {
  const rng = mulberry32(hashString(kind) ^ 0x9e3779b9);
  const count = particleCount(kind);
  const particles = [];
  for (let i = 0; i < count; i += 1) {
    const layer = rng();
    particles.push({
      x: rng(),
      y: rng(),
      layer,
      speed: kind === 'snow' ? 0.04 + rng() * 0.07 : 0.5 + rng() * 0.8,
      drift: (rng() - 0.5) * (kind === 'snow' ? 0.05 : 0.14),
      size: 0.5 + layer * 1.7 + rng() * 0.5,
      phase: rng() * TAU
    });
  }
  weather.particles = particles;
}

// 远景雾霭 / 沙尘 / 雨幕统一用一层竖向渐变，先铺底色再画粒子。
function drawHaze(ctx, kind, intensity, w, h) {
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  if (kind === 'fog') {
    grad.addColorStop(0, `rgba(198,208,218,${(0.05 * intensity).toFixed(3)})`);
    grad.addColorStop(0.55, `rgba(198,208,218,${(0.16 * intensity).toFixed(3)})`);
    grad.addColorStop(1, `rgba(214,221,228,${(0.28 * intensity).toFixed(3)})`);
  } else if (kind === 'sandstorm') {
    grad.addColorStop(0, `rgba(198,142,68,${(0.1 * intensity).toFixed(3)})`);
    grad.addColorStop(1, `rgba(166,108,50,${(0.3 * intensity).toFixed(3)})`);
  } else if (kind === 'heatwave') {
    grad.addColorStop(0, 'rgba(255,150,80,0.02)');
    grad.addColorStop(1, `rgba(255,118,56,${(0.1 * intensity).toFixed(3)})`);
  } else if (kind === 'storm') {
    grad.addColorStop(0, `rgba(18,28,42,${(0.2 * intensity).toFixed(3)})`);
    grad.addColorStop(1, 'rgba(10,16,26,0.05)');
  } else if (kind === 'rain') {
    grad.addColorStop(0, `rgba(58,82,102,${(0.1 * intensity).toFixed(3)})`);
    grad.addColorStop(1, 'rgba(40,58,74,0.03)');
  } else {
    grad.addColorStop(0, 'rgba(232,240,248,0.03)');
    grad.addColorStop(1, `rgba(232,240,248,${(0.06 * intensity).toFixed(3)})`);
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
}

export function updateWeather(dt) {
  resizeWeather();
  const ctx = weather.ctx;
  if (!ctx) return;
  ctx.clearRect(0, 0, weather.width, weather.height);
  const info = (state.view && state.view.weather) || {};
  const kind = str(info.kind, 'clear');
  const intensity = weatherIntensity();

  // 闪电能量按帧衰减；切换天气时不会残留高亮。
  weather.flash = Math.max(0, weather.flash - dt * 2.6);

  const precipitating = kind === 'rain' || kind === 'snow' || kind === 'storm';
  if (!precipitating) {
    weather.kind = kind;
    weather.particles = [];
  } else if (kind !== weather.kind) {
    weather.kind = kind;
    seedParticles(kind);
  }

  const w = weather.width;
  const h = weather.height;
  // 晴天且无闪电：天空穹顶已承担全部视觉，跳过渐变与粒子绘制。
  if (kind === 'clear' && weather.flash <= 0.01) {
    weather.kind = kind;
    weather.particles = [];
    return;
  }
  drawHaze(ctx, kind, intensity, w, h);
  if (!precipitating) return;

  const wind = list(info.wind);
  const windX = num(wind[0], 0);
  const windZ = num(wind[2], 0);
  const storm = kind === 'storm';

  if (kind === 'snow') {
    for (const p of weather.particles) {
      p.y += p.speed * dt * (0.6 + p.layer * 0.8);
      p.x += (p.drift + windX * 0.006 * (0.4 + p.layer)) * dt
        + Math.sin(p.phase + p.y * 12) * 0.0005;
      if (p.y > 1.02) { p.y = -0.02; p.x = Math.random(); }
      if (p.x > 1.02) p.x = -0.01;
      if (p.x < -0.02) p.x = 1.01;
    }
    // 远层细密、近层大颗，制造纵深。
    for (const pass of [
      { from: -1, to: 0.6, alpha: 0.42, scale: 0.8 },
      { from: 0.6, to: 1.01, alpha: 0.82, scale: 1.25 }
    ]) {
      ctx.fillStyle = `rgba(238,246,255,${pass.alpha})`;
      ctx.beginPath();
      for (const p of weather.particles) {
        if (p.layer < pass.from || p.layer >= pass.to) continue;
        ctx.moveTo(p.x * w + p.size * pass.scale, p.y * h);
        ctx.arc(p.x * w, p.y * h, p.size * pass.scale, 0, TAU);
      }
      ctx.fill();
    }
    return;
  }

  // 雨/暴雨：风决定倾斜方向与幅度，近层更长更亮。
  const slantPx = clamp((windX * 0.7 + windZ * 0.3) / 5, -1.6, 1.6) * (storm ? 16 : 10);
  for (const p of weather.particles) {
    p.y += p.speed * (storm ? 1.8 : 1.05) * dt;
    p.x += (p.drift + windX * 0.004 * (0.5 + p.layer)) * dt;
    if (p.y > 1.04) { p.y = -0.06 - Math.random() * 0.1; p.x = Math.random(); }
    if (p.x > 1.02) p.x = -0.01;
    if (p.x < -0.02) p.x = 1.01;
  }
  for (const pass of [
    { from: -1, to: 0.55, alpha: storm ? 0.22 : 0.16, width: 1, length: storm ? 15 : 11 },
    { from: 0.55, to: 1.01, alpha: storm ? 0.52 : 0.4, width: storm ? 1.5 : 1.15, length: storm ? 30 : 20 }
  ]) {
    ctx.strokeStyle = `rgba(160,198,234,${pass.alpha})`;
    ctx.lineWidth = pass.width;
    ctx.beginPath();
    for (const p of weather.particles) {
      if (p.layer < pass.from || p.layer >= pass.to) continue;
      const px = p.x * w;
      const py = p.y * h;
      const len = pass.length * (0.45 + p.layer) * (0.8 + p.size * 0.12);
      ctx.moveTo(px, py);
      ctx.lineTo(px + slantPx * (0.55 + p.layer * 0.7), py + len);
    }
    ctx.stroke();
  }

  if (storm) {
    if (weather.flash <= 0.01 && Math.random() < dt * (0.05 + intensity * 0.045)) {
      weather.flash = 0.65 + Math.random() * 0.35;
    }
    if (weather.flash > 0.01) {
      ctx.fillStyle = `rgba(206,228,255,${(weather.flash * 0.14).toFixed(3)})`;
      ctx.fillRect(0, 0, w, h);
    }
  }
}

/* ---------------- 天空与光照 ---------------- */

function computeSky() {
  const time = (state.view && state.view.time) || {};
  const weatherInfo = (state.view && state.view.weather) || {};
  const daylight = clamp(num(time.daylight, 0), 0, 1);
  const kind = str(weatherInfo.kind, 'clear');

  let color;
  if (daylight < 0.5) {
    color = mixColor(NIGHT_COLOR, DAWN_COLOR, daylight / 0.5);
  } else {
    color = mixColor(DAWN_COLOR, DAY_COLOR, (daylight - 0.5) / 0.5);
  }

  // 天气同时影响天空色、环境光与日照强度，避免只有雨丝在动、场景却依旧晴天。
  let ambientScale = 1;
  let sunScale = 1;
  if (kind === 'storm' || kind === 'hurricane') {
    color = shade(color, 0.55); ambientScale = 0.58; sunScale = 0.2;
  } else if (kind === 'rain') {
    color = shade(color, 0.8); ambientScale = 0.84; sunScale = 0.5;
  } else if (kind === 'sandstorm') {
    color = mixColor(shade(color, 0.78), [0.72, 0.53, 0.31], 0.35);
    ambientScale = 0.82; sunScale = 0.4;
  } else if (kind === 'snow') {
    color = mixColor(color, [0.82, 0.87, 0.92], 0.28);
    ambientScale = 1.04; sunScale = 0.6;
  } else if (kind === 'fog') {
    color = mixColor(color, [0.55, 0.6, 0.65], 0.42);
    ambientScale = 0.86; sunScale = 0.3;
  } else if (kind === 'heatwave') {
    color = mixColor(color, [1.0, 0.82, 0.6], 0.14);
    ambientScale = 1.06; sunScale = 1.18;
  } else if (kind === 'coldwave') {
    color = mixColor(color, [0.68, 0.78, 0.9], 0.22);
    ambientScale = 0.92; sunScale = 0.8;
  }

  const flash = clamp(weather.flash, 0, 1);
  if (flash > 0.01) color = mixColor(color, [0.86, 0.9, 1.0], flash * 0.5);

  const sun = time.sun || {};
  const az = num(sun.azimuth_deg, 0) * DEG;
  const el = num(sun.elevation_deg, -8) * DEG;
  const sunDir = [
    Math.cos(el) * Math.sin(az),
    Math.sin(el),
    Math.cos(el) * Math.cos(az)
  ];

  const intensity = clamp(num(weatherInfo.intensity, 1), 0, 2);
  let fogStart = 70;
  let fogEnd = 1100;
  if (kind === 'rain') { fogStart = 45; fogEnd = 680; }
  if (kind === 'fog') {
    fogStart = 24;
    fogEnd = clamp(560 - intensity * 180, 140, 720);
  }
  if (kind === 'snow') { fogStart = 38; fogEnd = 520; }
  if (kind === 'storm') { fogStart = 30; fogEnd = 420; }
  if (kind === 'sandstorm') { fogStart = 20; fogEnd = 300; }

  return {
    color,
    sunDir,
    ambient: (0.27 + daylight * 0.56) * ambientScale + flash * 0.35,
    sunStrength: (0.05 + clamp(Math.sin(el) * 2.4, 0.0, 1.0) * (0.35 + daylight * 0.65)) * sunScale,
    fogStart,
    fogEnd,
    daylight,
    wet: kind === 'rain' || kind === 'storm' ? 1 : 0,
    flash
  };
}

/* ---------------- 渲染 ---------------- */

const projection = new Float32Array(16);
const viewMatrix = new Float32Array(16);
const mvpMatrix = new Float32Array(16);

export function resizeGL() {
  if (!gfx) return;
  // 手机 GPU 填充率有限，触屏设备把像素比压到 1.5，帧率更稳。
  const coarse = window.matchMedia('(pointer: coarse)').matches;
  const dpr = Math.min(coarse ? 1.5 : 2, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.floor(window.innerWidth * dpr));
  const h = Math.max(1, Math.floor(window.innerHeight * dpr));
  if (gfx.canvas.width !== w || gfx.canvas.height !== h) {
    gfx.canvas.width = w;
    gfx.canvas.height = h;
  }
  gfx.gl.viewport(0, 0, w, h);
  gfx.pixelRatio = dpr;
}

export function uploadStatic(scenes) {
  if (!gfx) return;
  const data = buildWorldGeometry(scenes);
  const gl = gfx.gl;
  gl.bindBuffer(gl.ARRAY_BUFFER, gfx.staticBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  gfx.staticCount = Math.floor(data.length / 10);
  if (window.__dwGfx) window.__dwGfx.staticVertices = gfx.staticCount;
}

// 肩上物件：贴着玩家右肩绘制，随朝向一起转动。
function addCarriedTo(out, carried) {
  if (!carried) return;
  const kind = str(carried.kind);
  const rightX = Math.cos(state.yaw);
  const rightZ = Math.sin(state.yaw);
  const forwardX = Math.sin(state.yaw);
  const forwardZ = -Math.cos(state.yaw);
  const x = state.player.x + rightX * 0.42 + forwardX * 0.12;
  const z = state.player.z + rightZ * 0.42 + forwardZ * 0.12;
  const baseY = state.player.y + 1.24;
  const wood = [0.46, 0.34, 0.22];
  const dark = shade(wood, 0.72);
  if (kind === 'crate') {
    addBoxTo(out, x, baseY, z, 0.92, 0.84, 0.92, wood);
    addBoxTo(out, x, baseY + 0.84, z, 0.98, 0.08, 0.98, dark);
    addBoxTo(out, x - 0.47, baseY, z, 0.06, 0.84, 0.96, shade(wood, 0.86));
  } else if (kind === 'net') {
    addBoxTo(out, x, baseY, z, 0.68, 0.56, 1.05, [0.55, 0.58, 0.55]);
    addBoxTo(out, x, baseY + 0.5, z + 0.28, 0.16, 0.16, 0.78, wood);
  } else {
    addBoxTo(out, x, baseY + 0.16, z, 0.34, 0.34, 1.9, wood);
    addBoxTo(out, x, baseY + 0.34, z - 0.5, 0.2, 0.2, 0.5, dark);
  }
}

// 每帧重建的几何复用同一批数组，避免持续分配触发 GC。
const dynamicScratch = [];
const skyScratch = [];
const dynamicResult = new Float32Array(1 << 16);
const skyResult = new Float32Array(1 << 13);
const occupiedScratch = new Map();

function packScratch(scratch, result) {
  if (scratch.length > result.length) return Float32Array.from(scratch);
  result.set(scratch);
  return result.subarray(0, scratch.length);
}

function buildDynamicGeometry() {
  const out = dynamicScratch;
  out.length = 0;
  const npcs = list(state.view && state.view.npcs);
  const occupied = occupiedScratch;
  occupied.clear();
  for (const npc of npcs) {
    if (!npc || typeof npc !== 'object') continue;
    const raw = vec3(npc.position_m);
    if (!raw) continue;
    const motion = npcRenderPosition(npc);
    let p = [num(motion && motion.x, raw[0]), raw[1], num(motion && motion.z, raw[2])];
    const key = `${p[0].toFixed(1)}/${p[2].toFixed(1)}`;
    const dup = occupied.get(key) || 0;
    occupied.set(key, dup + 1);
    if (dup > 0) {
      const angle = dup * 2.399;
      p = [p[0] + Math.cos(angle) * 0.9 * dup, p[1], p[2] + Math.sin(angle) * 0.9 * dup];
    }
    const color = hexToRgb(npc.color, [0.45, 0.72, 0.95]);
    const now = performance.now();
    const cam = state.cameraPos;
    const near = Math.hypot(p[0] - cam[0], p[2] - cam[2]) < 40;
    let footY = npcFootY(npc, p);
    let offsetX = 0;
    let offsetZ = 0;
    const phase = (hashString(str(npc.id)) % 628) / 100;
    if (str(state.danceNpc) === str(npc.id) && now < state.danceUntil) {
      // 共舞反馈：被邀请的 NPC 踩着节拍上下起伏。
      footY += Math.abs(Math.sin(now * 0.008)) * 0.16;
    } else if (near) {
      // 呼吸起伏 + 行走步频：NPC 沿客户端路径移动时有轻微上下起伏。
      footY += (Math.sin(now * 0.0017 + phase) * 0.5 + 0.5) * 0.035;
      if (motion && motion.walking) footY += Math.abs(Math.sin(now * 0.011 + phase)) * 0.055;
    }
    if (str(state.hitNpc) === str(npc.id) && now < state.hitUntil) {
      // 被击打后的踉跄：短时间内左右偏移并逐渐衰减。
      const decay = (state.hitUntil - now) / 900;
      offsetX = Math.sin(now * 0.06) * 0.2 * decay;
      offsetZ = Math.cos(now * 0.045) * 0.14 * decay;
    }
    const shadowSize = 0.9;
    pushQuad(out,
      [p[0] - shadowSize, npcFootY(npc, p) + 0.02, p[2] - shadowSize],
      [p[0] - shadowSize, npcFootY(npc, p) + 0.02, p[2] + shadowSize],
      [p[0] + shadowSize, npcFootY(npc, p) + 0.02, p[2] + shadowSize],
      [p[0] + shadowSize, npcFootY(npc, p) + 0.02, p[2] - shadowSize],
      [0, 1, 0], [0.07, 0.09, 0.1]);
    addHumanoidTo(out, p[0] + offsetX, footY, p[2] + offsetZ, color, 1.74);
  }
  if (state.thirdPerson) {
    // 第三人称才绘制玩家身体；第一人称隐藏。
    pushQuad(out,
      [state.player.x - 1, state.player.y + 0.02, state.player.z - 1],
      [state.player.x - 1, state.player.y + 0.02, state.player.z + 1],
      [state.player.x + 1, state.player.y + 0.02, state.player.z + 1],
      [state.player.x + 1, state.player.y + 0.02, state.player.z - 1],
      [0, 1, 0], [0.07, 0.09, 0.1]);
    addHumanoidTo(out, state.player.x, state.player.y, state.player.z, [0.35, 0.77, 1.0], 1.8);
    addCarriedTo(out, state.carried);
  }
  return packScratch(out, dynamicResult);
}

function bindAttributes(gl, loc) {
  const stride = 10 * 4;
  gl.enableVertexAttribArray(loc.position);
  gl.enableVertexAttribArray(loc.normal);
  gl.enableVertexAttribArray(loc.color);
  gl.enableVertexAttribArray(loc.tile);
  gl.vertexAttribPointer(loc.position, 3, gl.FLOAT, false, stride, 0);
  gl.vertexAttribPointer(loc.normal, 3, gl.FLOAT, false, stride, 12);
  gl.vertexAttribPointer(loc.color, 3, gl.FLOAT, false, stride, 24);
  gl.vertexAttribPointer(loc.tile, 1, gl.FLOAT, false, stride, 36);
}

// 天空穹顶：以相机为圆心生成竖向渐变圆筒，顶部封口，避免只有一片纯色背景。
const SKY_RADIUS = 1500;
function buildSkyGeometry(eye, sky) {
  const out = skyScratch;
  out.length = 0;
  const base = eye[1] - 160;
  const top = eye[1] + 950;
  const zenith = sky.color;
  const daylight = clamp(num(sky.daylight, 0), 0, 1);
  const horizon = mixColor(sky.color, [0.74, 0.8, 0.9], 0.14 + daylight * 0.3);
  const segments = 12;
  for (let i = 0; i < segments; i += 1) {
    const a0 = (i / segments) * TAU;
    const a1 = ((i + 1) / segments) * TAU;
    const p0 = [eye[0] + Math.cos(a0) * SKY_RADIUS, base, eye[2] + Math.sin(a0) * SKY_RADIUS];
    const p1 = [eye[0] + Math.cos(a1) * SKY_RADIUS, base, eye[2] + Math.sin(a1) * SKY_RADIUS];
    const t0 = [p0[0], top, p0[2]];
    const t1 = [p1[0], top, p1[2]];
    pushGradientQuad(out, p0, p1, t1, t0, [0, 1, 0], [horizon, horizon, zenith, zenith]);
  }
  for (let i = 0; i < segments; i += 1) {
    const a0 = (i / segments) * TAU;
    const a1 = ((i + 1) / segments) * TAU;
    pushTri(out, [eye[0], top, eye[2]],
      [eye[0] + Math.cos(a0) * SKY_RADIUS, top, eye[2] + Math.sin(a0) * SKY_RADIUS],
      [eye[0] + Math.cos(a1) * SKY_RADIUS, top, eye[2] + Math.sin(a1) * SKY_RADIUS],
      [0, 1, 0], zenith);
  }

  // 太阳圆盘：只有日间才绘制，雨雾天由天空色自然压暗。
  const sunDir = list(sky.sunDir);
  if (num(sunDir[1], -1) > 0.03) {
    const cx = eye[0] + num(sunDir[0], 0) * 1250;
    const cy = eye[1] + num(sunDir[1], 0) * 1250;
    const cz = eye[2] + num(sunDir[2], 0) * 1250;
    const right = [Math.cos(state.yaw), 0, Math.sin(state.yaw)];
    const up = [0, 1, 0];
    const size = 34;
    const p = (u, v) => [cx + right[0] * u * size + up[0] * v * size, cy + right[1] * u * size + up[1] * v * size, cz + right[2] * u * size + up[2] * v * size];
    pushQuad(out, p(-1, -1), p(-1, 1), p(1, 1), p(1, -1), [0, 1, 0], [1.0, 0.96, 0.82]);
  }

  // 云层：几组低多边形云片缓慢漂移，避免天空过于空旷。
  const cloudBase = mixColor([0.92, 0.95, 1.0], sky.color, 0.28);
  for (let i = 0; i < 7; i += 1) {
    const angle = (i / 7) * TAU + state.cloudOffset * 0.02;
    const radius = 420 + (i % 3) * 150;
    const cx = eye[0] + Math.cos(angle) * radius;
    const cz = eye[2] + Math.sin(angle) * radius;
    const cy = eye[1] + 230 + (i % 4) * 26;
    const w = 90 + (i % 3) * 46;
    const d = 46 + (i % 2) * 34;
    const tone = i % 2 === 0 ? 1.0 : 0.9;
    pushQuad(out,
      [cx - w, cy, cz - d], [cx - w, cy, cz + d], [cx + w, cy, cz + d], [cx + w, cy, cz - d],
      [0, 1, 0], shade(cloudBase, tone));
    pushQuad(out,
      [cx - w * 0.6, cy + 14, cz - d * 0.7], [cx - w * 0.6, cy + 14, cz + d * 0.7],
      [cx + w * 0.6, cy + 14, cz + d * 0.7], [cx + w * 0.6, cy + 14, cz - d * 0.7],
      [0, 1, 0], shade(cloudBase, tone * 0.95));
  }
  return packScratch(out, skyResult);
}

export function renderScene() {
  if (!gfx) return;
  resizeGL();
  const gl = gfx.gl;
  const sky = computeSky();

  gl.clearColor(sky.color[0], sky.color[1], sky.color[2], 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.useProgram(gfx.program);

  const eyeH = state.eyeHeight;
  const cosPitch = Math.cos(state.pitch);
  const forward = [
    Math.sin(state.yaw) * cosPitch,
    Math.sin(state.pitch),
    -Math.cos(state.yaw) * cosPitch
  ];

  let eye;
  let target;
  if (state.thirdPerson) {
    const focus = [state.player.x, state.player.y + eyeH * 0.9, state.player.z];
    eye = [
      focus[0] - forward[0] * state.thirdDistance,
      focus[1] - forward[1] * state.thirdDistance,
      focus[2] - forward[2] * state.thirdDistance
    ];
    eye[1] = Math.max(eye[1], floorAt(state.player.x, state.player.z) + 0.4);
    target = focus;
  } else {
    eye = [state.player.x, state.player.y + eyeH, state.player.z];
    target = [eye[0] + forward[0], eye[1] + forward[1], eye[2] + forward[2]];
  }

  state.cameraPos = eye;
  mat4LookAt(viewMatrix, eye, target, [0, 1, 0]);
  mat4Perspective(projection, 72 * DEG, window.innerWidth / Math.max(window.innerHeight, 1), 0.1, 3000);
  mat4Multiply(mvpMatrix, projection, viewMatrix);
  state.mvp.set(mvpMatrix);

  gl.uniformMatrix4fv(gfx.loc.mvp, false, mvpMatrix);
  gl.uniform3fv(gfx.loc.cameraPos, eye);
  gl.uniform3fv(gfx.loc.sunDir, sky.sunDir);
  gl.uniform1f(gfx.loc.ambient, sky.ambient);
  gl.uniform1f(gfx.loc.sunStrength, sky.sunStrength);
  gl.uniform3fv(gfx.loc.fogColor, sky.color);
  gl.uniform1f(gfx.loc.fogStart, sky.fogStart);
  gl.uniform1f(gfx.loc.fogEnd, sky.fogEnd);
  // 材质图集：一块贴图覆盖全部静态/动态几何，tile=0 为白格等效纯顶点色。
  gl.uniform1f(gfx.loc.texRepeat, 0.32);
  gl.uniform1f(gfx.loc.texOn, gfx.atlas ? 1 : 0);
  // 夜间亮窗强度与雨天湿滑高光。
  gl.uniform1f(gfx.loc.night, clamp(1 - sky.daylight / 0.55, 0, 1));
  gl.uniform1f(gfx.loc.wet, sky.wet);
  if (gfx.atlas) {
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, gfx.atlas.texture);
    gl.uniform1i(gfx.loc.atlas, 0);
  }

  // 天空穹顶：不受光照/雾影响，不写深度，先铺满远景。
  gl.uniform1f(gfx.loc.unlit, 1);
  gl.uniform1f(gfx.loc.fogEnabled, 0);
  const skyGeometry = buildSkyGeometry(eye, sky);
  gl.bindBuffer(gl.ARRAY_BUFFER, gfx.skyBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, skyGeometry, gl.DYNAMIC_DRAW);
  bindAttributes(gl, gfx.loc);
  gl.depthMask(false);
  gl.disable(gl.DEPTH_TEST);
  gl.drawArrays(gl.TRIANGLES, 0, Math.floor(skyGeometry.length / 10));
  gl.enable(gl.DEPTH_TEST);
  gl.depthMask(true);
  gl.uniform1f(gfx.loc.unlit, 0);
  gl.uniform1f(gfx.loc.fogEnabled, 1);

  if (gfx.staticCount > 0) {
    gl.bindBuffer(gl.ARRAY_BUFFER, gfx.staticBuffer);
    bindAttributes(gl, gfx.loc);
    gl.drawArrays(gl.TRIANGLES, 0, gfx.staticCount);
  }

  const dynamic = buildDynamicGeometry();
  if (dynamic.length >= 10) {
    gl.bindBuffer(gl.ARRAY_BUFFER, gfx.dynamicBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, dynamic, gl.DYNAMIC_DRAW);
    bindAttributes(gl, gfx.loc);
    gl.drawArrays(gl.TRIANGLES, 0, Math.floor(dynamic.length / 10));
  }
}

/* ---------------- NPC 头顶标签 ---------------- */

export function projectPoint(point) {
  const m = state.mvp;
  const x = point[0], y = point[1], z = point[2];
  const clipX = m[0] * x + m[4] * y + m[8] * z + m[12];
  const clipY = m[1] * x + m[5] * y + m[9] * z + m[13];
  const clipZ = m[2] * x + m[6] * y + m[10] * z + m[14];
  const clipW = m[3] * x + m[7] * y + m[11] * z + m[15];
  if (!(clipW > 0.001)) return null;
  const ndcX = clipX / clipW;
  const ndcY = clipY / clipW;
  const ndcZ = clipZ / clipW;
  if (ndcZ < -1 || ndcZ > 1) return null;
  return {
    x: (ndcX * 0.5 + 0.5) * window.innerWidth,
    y: (1 - (ndcY * 0.5 + 0.5)) * window.innerHeight
  };
}

// 宽松投影：不做近远平面剔除，只排除相机背后的点，
// 供任务信标等需要「屏幕边缘吸附」的指示器使用。
export function projectPointLoose(point) {
  const m = state.mvp;
  const x = point[0], y = point[1], z = point[2];
  const clipX = m[0] * x + m[4] * y + m[8] * z + m[12];
  const clipY = m[1] * x + m[5] * y + m[9] * z + m[13];
  const clipW = m[3] * x + m[7] * y + m[11] * z + m[15];
  if (!(clipW > 0.001)) return null;
  return {
    x: (clipX / clipW * 0.5 + 0.5) * window.innerWidth,
    y: (1 - (clipY / clipW * 0.5 + 0.5)) * window.innerHeight
  };
}

function createNpcTag(npc) {
  const tag = document.createElement('div');
  tag.className = 'npc-tag';
  const name = document.createElement('span');
  const activity = document.createElement('span');
  activity.className = 'npc-act';
  tag.appendChild(name);
  tag.appendChild(activity);
  els.npcTags.appendChild(tag);
  const record = { node: tag, name, activity, text: '', act: '', cls: 'npc-tag' };
  state.npcTags.set(str(npc.id, str(npc.name, 'npc')), record);
  return record;
}

export function updateNpcTags() {
  if (!els.npcTags) return;
  const npcs = list(state.view && state.view.npcs);
  const seen = new Set();
  for (const npc of npcs) {
    if (!npc || typeof npc !== 'object') continue;
    const id = str(npc.id, str(npc.name, 'npc'));
    if (seen.has(id)) continue;
    seen.add(id);
    const raw = vec3(npc.position_m);
    if (!raw) continue;
    let record = state.npcTags.get(id);
    if (!record) record = createNpcTag(npc);

    const name = str(npc.name, id);
    const activity = str(npc.activity, '');
    if (record.text !== name) { record.name.textContent = name; record.text = name; }
    if (record.act !== activity) { record.activity.textContent = activity; record.act = activity; }

    const motion = npcRenderPosition(npc);
    const p = [num(motion && motion.x, raw[0]), raw[1], num(motion && motion.z, raw[2])];
    const head = [p[0], npcFootY(npc, p) + 2.1, p[2]];
    const screen = projectPoint(head);
    const cam = state.cameraPos;
    const dist = Math.hypot(head[0] - cam[0], head[1] - cam[1], head[2] - cam[2]);
    if (!screen || dist > NPC_TAG_DISTANCE
      || screen.x < -140 || screen.x > window.innerWidth + 140
      || screen.y < -60 || screen.y > window.innerHeight + 60) {
      record.node.style.display = 'none';
      continue;
    }
    record.node.style.display = '';
    record.node.style.left = `${screen.x.toFixed(1)}px`;
    record.node.style.top = `${screen.y.toFixed(1)}px`;
    record.node.style.opacity = dist > NPC_TAG_DISTANCE * 0.8 ? '0.55' : '1';

    // 视觉反馈：可交互目标高亮、共舞起伏、被击打抖动。
    const now = performance.now();
    const classes = ['npc-tag'];
    if (state.target && state.target.kind === 'npc' && str(state.target.id) === id) classes.push('near');
    if (str(state.danceNpc) === id && now < state.danceUntil) classes.push('dancing');
    if (str(state.hitNpc) === id && now < state.hitUntil) classes.push('hit');
    const cls = classes.join(' ');
    if (record.cls !== cls) { record.node.className = cls; record.cls = cls; }
  }
  for (const [id, record] of state.npcTags) {
    if (!seen.has(id)) record.node.style.display = 'none';
  }
}

/* ---------------- 任务信标 ---------------- */

const questMarkers = new Map();

function createQuestMarker(quest) {
  const node = document.createElement('div');
  node.className = 'quest-marker';
  const diamond = document.createElement('span');
  diamond.className = 'diamond';
  const label = document.createElement('span');
  label.className = 'label';
  node.appendChild(diamond);
  node.appendChild(label);
  els.questMarkers.appendChild(node);
  const record = { node, label, text: '', cls: 'quest-marker' };
  questMarkers.set(str(quest && quest.id, 'quest'), record);
  return record;
}

// 未完成任务在目标场景上空投影一枚菱形信标，脉冲动画提示方向；
// 目标在屏幕外时吸附到屏幕边缘，像主流 3D 游戏的方位指示器一样。
export function updateQuestMarkers() {
  if (!els.questMarkers) return;
  const quests = list(state.view && state.view.quests).filter((quest) => quest && !quest.completed);
  const scenesById = {};
  for (const scene of state.scenes) scenesById[str(scene.id)] = scene;
  const seen = new Set();
  const perScene = new Map();
  const w = window.innerWidth;
  const h = window.innerHeight;
  for (const quest of quests) {
    const id = str(quest.id, 'quest');
    seen.add(id);
    const scene = scenesById[str(quest.scene_id)];
    if (!scene) continue;
    const center = vec3(scene.center_m);
    if (!center) continue;
    let record = questMarkers.get(id);
    if (!record) record = createQuestMarker(quest);

    // 同场景多个任务横向错开，避免信标重叠。
    const order = perScene.get(scene.id) || 0;
    perScene.set(scene.id, order + 1);
    const offset = (order % 3) * 9 - 9;
    const baseY = num(scene.bounds_m && scene.bounds_m.min && scene.bounds_m.min[1], center[1]);
    const anchor = [center[0] + offset, baseY + 26, center[2]];
    const cam = state.cameraPos;
    const dist = Math.hypot(anchor[0] - cam[0], anchor[2] - cam[2]);
    if (dist > 1600) {
      record.node.style.display = 'none';
      continue;
    }
    // 标签带距离，按 10 米取整避免每帧刷新文本。
    const text = `${str(quest.title, '任务')} · ${Math.round(dist / 10) * 10} m`;
    if (record.text !== text) { record.label.textContent = text; record.text = text; }
    const screen = projectPointLoose(anchor);
    let sx;
    let sy;
    let edge = false;
    const margin = 56;
    if (screen) {
      sx = screen.x;
      sy = screen.y;
      if (sx < margin || sx > w - margin || sy < margin || sy > h - margin) {
        const cx = w / 2;
        const cy = h / 2;
        const dx = sx - cx;
        const dy = sy - cy;
        const scale = Math.min(
          Math.abs((w / 2 - margin) / (Math.abs(dx) || 1)),
          Math.abs((h / 2 - margin) / (Math.abs(dy) || 1))
        );
        sx = clamp(cx + dx * scale, margin, w - margin);
        sy = clamp(cy + dy * scale, margin, h - margin);
        edge = true;
      }
    } else {
      // 目标在相机背后（如正上方的头顶）：把世界方向转到视角空间，
      // 反向映射到屏幕边缘，得到标准的方位指示。
      const dx = anchor[0] - cam[0];
      const dy = anchor[1] - cam[1];
      const dz = anchor[2] - cam[2];
      const v = viewMatrix;
      let vx = v[0] * dx + v[1] * dy + v[2] * dz;
      let vy = v[4] * dx + v[5] * dy + v[6] * dz;
      const vz = v[8] * dx + v[9] * dy + v[10] * dz;
      if (vz > 0) { vx = -vx; vy = -vy; }
      const angle = Math.atan2(vy, vx);
      const tx = Math.abs(Math.cos(angle)) > 1e-6 ? (w / 2 - margin) / Math.abs(Math.cos(angle)) : Infinity;
      const ty = Math.abs(Math.sin(angle)) > 1e-6 ? (h / 2 - margin) / Math.abs(Math.sin(angle)) : Infinity;
      const t = Math.min(tx, ty);
      sx = clamp(w / 2 + Math.cos(angle) * t, margin, w - margin);
      sy = clamp(h / 2 + Math.sin(angle) * t, margin, h - margin);
      edge = true;
    }
    record.node.style.display = '';
    record.node.style.left = `${sx.toFixed(1)}px`;
    record.node.style.top = `${sy.toFixed(1)}px`;
    record.node.style.opacity = dist > 900 ? '0.5' : '1';
    const cls = dist < 60
      ? 'quest-marker near'
      : edge ? 'quest-marker edge' : 'quest-marker';
    if (record.cls !== cls) { record.node.className = cls; record.cls = cls; }
  }
  for (const [id, record] of questMarkers) {
    if (!seen.has(id)) record.node.style.display = 'none';
  }
}
