'use strict';
/* 静态几何：把场景数据烘焙成顶点批次，并维护场景包围盒与建筑碰撞足迹。 */

import {
  MAX_VERTEX_FLOATS, PROPS_PER_SCENE, TAU, FALLBACK_PALETTE,
  clamp, num, str, list, vec3, shade, mixColor, hexToRgb, hashString, mulberry32,
  state
} from './core.js';
import { TILE } from './texture.js';

/* ---------------- 几何批次 ---------------- */

function pushVertex(out, p, n, color, tile = 0) {
  out.push(p[0], p[1], p[2], n[0], n[1], n[2], color[0], color[1], color[2], tile);
}

export function pushTri(out, a, b, c, normal, color, tile = 0) {
  if (out.length > MAX_VERTEX_FLOATS) return;
  pushVertex(out, a, normal, color, tile);
  pushVertex(out, b, normal, color, tile);
  pushVertex(out, c, normal, color, tile);
}

export function pushQuad(out, a, b, c, d, normal, color, tile = 0) {
  pushTri(out, a, b, c, normal, color, tile);
  pushTri(out, a, c, d, normal, color, tile);
}

// 逐顶点渐变四边形，用于天空穹顶这类不能用单色表达的面。
export function pushGradientQuad(out, a, b, c, d, normal, colors, tile = 0) {
  if (out.length > MAX_VERTEX_FLOATS) return;
  pushVertex(out, a, normal, colors[0], tile);
  pushVertex(out, b, normal, colors[1], tile);
  pushVertex(out, c, normal, colors[2], tile);
  pushVertex(out, a, normal, colors[0], tile);
  pushVertex(out, c, normal, colors[2], tile);
  pushVertex(out, d, normal, colors[3], tile);
}

export function addBoxTo(out, cx, baseY, cz, sx, sy, sz, color, tile = 0) {
  const hx = Math.max(num(sx, 1), 0.02) / 2;
  const hz = Math.max(num(sz, 1), 0.02) / 2;
  const h = Math.max(num(sy, 1), 0.02);
  const x0 = cx - hx, x1 = cx + hx;
  const z0 = cz - hz, z1 = cz + hz;
  const y0 = baseY, y1 = baseY + h;

  pushQuad(out, [x0, y1, z0], [x0, y1, z1], [x1, y1, z1], [x1, y1, z0], [0, 1, 0], color, tile);
  pushQuad(out, [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], [0, 0, 1], color, tile);
  pushQuad(out, [x1, y0, z0], [x0, y0, z0], [x0, y1, z0], [x1, y1, z0], [0, 0, -1], color, tile);
  pushQuad(out, [x1, y0, z1], [x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [1, 0, 0], color, tile);
  pushQuad(out, [x0, y0, z0], [x0, y0, z1], [x0, y1, z1], [x0, y1, z0], [-1, 0, 0], color, tile);
}

function addPrismTo(out, cx, baseY, cz, radius, height, sides, color, capColor, tile = 0) {
  const n = Math.max(3, Math.round(num(sides, 6)));
  const r = Math.max(num(radius, 0.4), 0.03);
  const h = Math.max(num(height, 2), 0.03);
  const top = [];
  for (let i = 0; i < n; i += 1) {
    const a0 = (i / n) * TAU;
    const a1 = ((i + 1) / n) * TAU;
    const p0 = [cx + Math.cos(a0) * r, baseY, cz + Math.sin(a0) * r];
    const p1 = [cx + Math.cos(a1) * r, baseY, cz + Math.sin(a1) * r];
    const t0 = [p0[0], baseY + h, p0[2]];
    const t1 = [p1[0], baseY + h, p1[2]];
    const mid = (a0 + a1) / 2;
    pushQuad(out, p0, p1, t1, t0, [Math.cos(mid), 0, Math.sin(mid)], color, tile);
    top.push(t0);
  }
  const cap = capColor || color;
  for (let i = 0; i < n; i += 1) {
    pushTri(out, [cx, baseY + h, cz], top[i], top[(i + 1) % n], [0, 1, 0], cap, tile);
  }
}

function addConeTo(out, cx, baseY, cz, radius, height, sides, color, tile = 0) {
  const n = Math.max(3, Math.round(num(sides, 8)));
  const r = Math.max(num(radius, 1.5), 0.03);
  const h = Math.max(num(height, 3), 0.03);
  const apex = [cx, baseY + h, cz];
  for (let i = 0; i < n; i += 1) {
    const a0 = (i / n) * TAU;
    const a1 = ((i + 1) / n) * TAU;
    const p0 = [cx + Math.cos(a0) * r, baseY, cz + Math.sin(a0) * r];
    const p1 = [cx + Math.cos(a1) * r, baseY, cz + Math.sin(a1) * r];
    const mid = (a0 + a1) / 2;
    const len = Math.hypot(h, r) || 1;
    const normal = [(Math.cos(mid) * h) / len, r / len, (Math.sin(mid) * h) / len];
    pushTri(out, p0, p1, apex, normal, color, tile);
  }
}

function addPyramidTo(out, cx, baseY, cz, halfX, halfZ, height, color, tile = 0) {
  const hx = Math.max(num(halfX, 2), 0.1);
  const hz = Math.max(num(halfZ, 2), 0.1);
  const h = Math.max(num(height, 4), 0.1);
  const corners = [
    [cx - hx, baseY, cz - hz],
    [cx + hx, baseY, cz - hz],
    [cx + hx, baseY, cz + hz],
    [cx - hx, baseY, cz + hz]
  ];
  const apex = [cx, baseY + h, cz];
  for (let i = 0; i < 4; i += 1) {
    const p0 = corners[i];
    const p1 = corners[(i + 1) % 4];
    const e1 = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]];
    const e2 = [apex[0] - p0[0], apex[1] - p0[1], apex[2] - p0[2]];
    let nx = e1[1] * e2[2] - e1[2] * e2[1];
    let ny = e1[2] * e2[0] - e1[0] * e2[2];
    let nz = e1[0] * e2[1] - e1[1] * e2[0];
    const len = Math.hypot(nx, ny, nz) || 1;
    nx /= len; ny /= len; nz /= len;
    if (ny < 0) { nx = -nx; ny = -ny; nz = -nz; }
    pushTri(out, p0, p1, apex, [nx, ny, nz], color, tile);
  }
}

export function addHumanoidTo(out, x, feetY, z, color, height, tile = 0) {
  const h = clamp(num(height, 1.75), 1.1, 2.6);
  const legH = h * 0.46;
  const torsoH = h * 0.33;
  const headH = h * 0.16;
  const base = num(feetY, 0);
  addBoxTo(out, x, base, z, h * 0.27, legH, h * 0.2, shade(color, 0.62));
  addBoxTo(out, x, base + legH, z, h * 0.34, torsoH, h * 0.25, color);
  addBoxTo(out, x, base + legH + torsoH, z, h * 0.23, headH, h * 0.23, shade(color, 1.22));
}

export function sceneBox(scene) {
  if (!scene || typeof scene !== 'object') return null;
  const low = vec3(scene.bounds_m && scene.bounds_m.min);
  const high = vec3(scene.bounds_m && scene.bounds_m.max);
  const center = vec3(scene.center_m, [0, 0, 0]);
  const size = vec3(scene.size_m, [1, 1, 1]);

  let x0, y0, z0, x1, y1, z1;
  if (low && high) {
    x0 = Math.min(low[0], high[0]);
    y0 = Math.min(low[1], high[1]);
    z0 = Math.min(low[2], high[2]);
    x1 = Math.max(low[0], high[0]);
    y1 = Math.max(low[1], high[1]);
    z1 = Math.max(low[2], high[2]);
  } else {
    x0 = center[0] - Math.abs(size[0]) / 2;
    y0 = center[1] - Math.abs(size[1]) / 2;
    z0 = center[2] - Math.abs(size[2]) / 2;
    x1 = center[0] + Math.abs(size[0]) / 2;
    y1 = center[1] + Math.abs(size[1]) / 2;
    z1 = center[2] + Math.abs(size[2]) / 2;
  }
  if (![x0, y0, z0, x1, y1, z1].every(Number.isFinite)) return null;
  if (x1 - x0 < 1) x1 = x0 + 1;
  if (z1 - z0 < 1) z1 = z0 + 1;
  if (y1 - y0 < 0.5) y1 = y0 + 0.5;
  return { x0, x1, y0, y1, z0, z1, w: x1 - x0, d: z1 - z0, h: y1 - y0 };
}

function gridLayout(width, depth, target) {
  const nx = clamp(Math.round(Math.sqrt(Math.max(1, target) * (width / Math.max(depth, 0.001)))), 1, 30);
  const nz = clamp(Math.ceil(target / nx), 1, 30);
  return { nx, nz, cellW: width / nx, cellD: depth / nz };
}

/* ---------------- 场景装饰生成 ---------------- */

// 地面切成细格并做轻微色差，贴图与顶点色相乘制造自然颗粒感。
function addGroundTiles(out, box, ground, tile) {
  const cells = clamp(Math.round(Math.max(box.w, box.d) / 14), 4, 26);
  const cw = box.w / cells;
  const cd = box.d / cells;
  const rng = mulberry32((hashString(`${box.x0.toFixed(1)}/${box.z0.toFixed(1)}`) ^ 0x51ed270b) >>> 0);
  const y = box.y0 + 0.012;
  for (let iz = 0; iz < cells; iz += 1) {
    for (let ix = 0; ix < cells; ix += 1) {
      const x0 = box.x0 + ix * cw;
      const z0 = box.z0 + iz * cd;
      const tone = 0.9 + rng() * 0.16;
      pushQuad(out,
        [x0, y, z0], [x0, y, z0 + cd], [x0 + cw, y, z0 + cd], [x0 + cw, y, z0],
        [0, 1, 0], shade(ground, tone), tile);
    }
  }
}

function buildPlaza(out, box, accent) {
  const cx = (box.x0 + box.x1) / 2;
  const cz = (box.z0 + box.z1) / 2;
  const radius = clamp(Math.min(box.w, box.d) * 0.16, 5, 34);
  const stone = shade(mixColor(accent, [0.62, 0.6, 0.55], 0.55), 0.92);
  addPrismTo(out, cx, box.y0 + 0.02, cz, radius, 0.16, 12, shade(stone, 0.9), stone, TILE.PLAZA);
  // 环形铺装：深浅交替的石板让广场地面有秩序感。
  const plates = 18;
  const y = box.y0 + 0.185;
  for (let i = 0; i < plates; i += 1) {
    const a0 = (i / plates) * TAU;
    const a1 = ((i + 1) / plates) * TAU;
    const inner = radius * 0.5;
    const outer = radius * 0.97;
    pushQuad(out,
      [cx + Math.cos(a0) * inner, y, cz + Math.sin(a0) * inner],
      [cx + Math.cos(a0) * outer, y, cz + Math.sin(a0) * outer],
      [cx + Math.cos(a1) * outer, y, cz + Math.sin(a1) * outer],
      [cx + Math.cos(a1) * inner, y, cz + Math.sin(a1) * inner],
      [0, 1, 0], shade(stone, i % 2 === 0 ? 1.0 : 0.88), TILE.PLAZA);
  }
  // 中心喷泉：石台 + 浅水盖，给出生点一个明确的视觉锚点。
  addPrismTo(out, cx, box.y0 + 0.18, cz, radius * 0.26, 0.8, 10, shade(stone, 0.78), stone, TILE.PLAZA);
  addPrismTo(out, cx, box.y0 + 0.98, cz, radius * 0.2, 0.06, 10, [0.3, 0.55, 0.68], [0.42, 0.68, 0.8], TILE.WATER);
  // 广场路灯：深色灯柱 + 暖色灯头。
  for (let i = 0; i < 6; i += 1) {
    const angle = (i / 6) * TAU + 0.3;
    const px = cx + Math.cos(angle) * radius * 0.82;
    const pz = cz + Math.sin(angle) * radius * 0.82;
    addPrismTo(out, px, box.y0 + 0.18, pz, 0.14, 3.4, 5, [0.22, 0.23, 0.25]);
    addBoxTo(out, px, box.y0 + 3.55, pz, 0.42, 0.52, 0.42, [1.0, 0.83, 0.48]);
  }
  // 花坛：给广场边缘一点绿意。
  const planter = mixColor(stone, [0.5, 0.44, 0.36], 0.35);
  for (let i = 0; i < 3; i += 1) {
    const angle = (i / 3) * TAU + 0.85;
    const px = cx + Math.cos(angle) * radius * 0.66;
    const pz = cz + Math.sin(angle) * radius * 0.66;
    addBoxTo(out, px, box.y0 + 0.18, pz, 1.6, 0.52, 1.6, planter, TILE.CONCRETE);
    addBoxTo(out, px, box.y0 + 0.7, pz, 1.2, 0.1, 1.2, [0.24, 0.17, 0.12], TILE.DIRT);
    addConeTo(out, px, box.y0 + 0.68, pz, 0.65, 1.5, 7, [0.31, 0.52, 0.28]);
  }
}

// 街区路网：沿建筑网格线铺沥青路面、人行道、路缘、虚线与人行横道，
// 让 built 场景像真正的城镇。
function buildStreets(out, box, ground, nx, nz, cellW, cellD, margin) {
  const roadW = clamp(Math.min(cellW, cellD) * 0.24, 1.6, 7);
  const asphalt = mixColor(shade(ground, 0.42), [0.15, 0.16, 0.18], 0.5);
  const curb = mixColor(asphalt, [0.68, 0.67, 0.63], 0.45);
  const sidewalk = shade(ground, 1.1);
  const dash = [0.74, 0.7, 0.54];
  const zebra = [0.86, 0.84, 0.78];
  // 路灯：沿人行道等距布点，灯柱 + 暖色灯头，夜间让街道有城市轮廓。
  const lampPost = [0.24, 0.25, 0.28];
  const lampHead = [1.0, 0.85, 0.5];
  const addLamp = (x, z) => {
    addPrismTo(out, x, y, z, 0.09, 3.4, 5, lampPost);
    addBoxTo(out, x, y + 3.38, z, 0.34, 0.28, 0.34, lampHead);
  };
  const x0 = box.x0 + margin;
  const z0 = box.z0 + margin;
  const totalW = nx * cellW;
  const totalD = nz * cellD;
  const y = box.y0 + 0.03;
  const half = roadW / 2;
  const curbW = 0.24;
  const walkW = 1.15;

  const roadZ = (cx) => {
    pushQuad(out, [cx - half, y, z0], [cx - half, y, z0 + totalD],
      [cx + half, y, z0 + totalD], [cx + half, y, z0], [0, 1, 0], asphalt, TILE.ASPHALT);
    pushQuad(out, [cx - half - curbW, y + 0.006, z0], [cx - half - curbW, y + 0.006, z0 + totalD],
      [cx - half, y + 0.006, z0 + totalD], [cx - half, y + 0.006, z0], [0, 1, 0], curb, TILE.SIDEWALK);
    pushQuad(out, [cx + half, y + 0.006, z0], [cx + half, y + 0.006, z0 + totalD],
      [cx + half + curbW, y + 0.006, z0 + totalD], [cx + half + curbW, y + 0.006, z0], [0, 1, 0], curb, TILE.SIDEWALK);
    // 路缘外侧人行道带：让建筑与马路之间有一圈完整的步行空间。
    pushQuad(out, [cx - half - curbW - walkW, y + 0.004, z0], [cx - half - curbW - walkW, y + 0.004, z0 + totalD],
      [cx - half - curbW, y + 0.004, z0 + totalD], [cx - half - curbW, y + 0.004, z0], [0, 1, 0], sidewalk, TILE.SIDEWALK);
    pushQuad(out, [cx + half + curbW, y + 0.004, z0], [cx + half + curbW, y + 0.004, z0 + totalD],
      [cx + half + curbW + walkW, y + 0.004, z0 + totalD], [cx + half + curbW + walkW, y + 0.004, z0], [0, 1, 0], sidewalk, TILE.SIDEWALK);
    for (let z = z0 + 2; z < z0 + totalD - 2; z += 6) {
      pushQuad(out, [cx, y + 0.009, z], [cx, y + 0.009, z + 2.4],
        [cx + 0.28, y + 0.009, z + 2.4], [cx + 0.28, y + 0.009, z], [0, 1, 0], dash);
    }
    let side = -1;
    for (let z = z0 + 10; z < z0 + totalD - 8; z += 26) {
      addLamp(cx + side * (half + curbW + walkW * 0.55), z);
      side = -side;
    }
  };
  const roadX = (cz) => {
    pushQuad(out, [x0, y, cz - half], [x0, y, cz + half],
      [x0 + totalW, y, cz + half], [x0 + totalW, y, cz - half], [0, 1, 0], asphalt, TILE.ASPHALT);
    pushQuad(out, [x0, y + 0.006, cz - half - curbW], [x0, y + 0.006, cz - half],
      [x0 + totalW, y + 0.006, cz - half], [x0 + totalW, y + 0.006, cz - half - curbW], [0, 1, 0], curb, TILE.SIDEWALK);
    pushQuad(out, [x0, y + 0.006, cz + half], [x0, y + 0.006, cz + half + curbW],
      [x0 + totalW, y + 0.006, cz + half + curbW], [x0 + totalW, y + 0.006, cz + half], [0, 1, 0], curb, TILE.SIDEWALK);
    pushQuad(out, [x0, y + 0.004, cz - half - curbW - walkW], [x0, y + 0.004, cz - half - curbW],
      [x0 + totalW, y + 0.004, cz - half - curbW], [x0 + totalW, y + 0.004, cz - half - curbW - walkW], [0, 1, 0], sidewalk, TILE.SIDEWALK);
    pushQuad(out, [x0, y + 0.004, cz + half + curbW], [x0, y + 0.004, cz + half + curbW + walkW],
      [x0 + totalW, y + 0.004, cz + half + curbW + walkW], [x0 + totalW, y + 0.004, cz + half + curbW], [0, 1, 0], sidewalk, TILE.SIDEWALK);
    for (let x = x0 + 2; x < x0 + totalW - 2; x += 6) {
      pushQuad(out, [x, y + 0.009, cz], [x, y + 0.009, cz + 0.28],
        [x + 2.4, y + 0.009, cz + 0.28], [x + 2.4, y + 0.009, cz], [0, 1, 0], dash);
    }
    let side = 1;
    for (let x = x0 + 16; x < x0 + totalW - 8; x += 26) {
      addLamp(x, cz + side * (half + curbW + walkW * 0.55));
      side = -side;
    }
  };
  // 人行横道：每个内部交叉口在两条路的入口侧各画一组斑马线。
  const crosswalk = (cx, cz) => {
    const stripe = 0.45;
    const gap = 0.42;
    const span = roadW - 0.8;
    const steps = 4;
    const offset = half + 0.8;
    for (let s = 0; s < steps; s += 1) {
      const d = -offset + s * (stripe + gap);
      // 横穿 z 向道路（条纹沿 x 向铺开）
      pushQuad(out, [cx - span / 2, y + 0.012, cz + d], [cx - span / 2, y + 0.012, cz + d + stripe],
        [cx + span / 2, y + 0.012, cz + d + stripe], [cx + span / 2, y + 0.012, cz + d], [0, 1, 0], zebra);
      // 横穿 x 向道路（条纹沿 z 向铺开）
      pushQuad(out, [cx + d, y + 0.012, cz - span / 2], [cx + d, y + 0.012, cz + span / 2],
        [cx + d + stripe, y + 0.012, cz + span / 2], [cx + d + stripe, y + 0.012, cz - span / 2], [0, 1, 0], zebra);
    }
  };

  for (let ix = 0; ix <= nx; ix += 1) roadZ(x0 + ix * cellW);
  for (let iz = 0; iz <= nz; iz += 1) roadX(z0 + iz * cellD);
  for (let iz = 1; iz < nz; iz += 1) {
    for (let ix = 1; ix < nx; ix += 1) {
      crosswalk(x0 + ix * cellW, z0 + iz * cellD);
    }
  }
}

function buildBuildings(out, box, ground, accent, rng, margin, innerW, innerD, area) {
  const target = clamp(Math.round(area / 900), 8, PROPS_PER_SCENE);
  const { nx, nz, cellW, cellD } = gridLayout(innerW, innerD, target);
  const centerX = (box.x0 + box.x1) / 2;
  const centerZ = (box.z0 + box.z1) / 2;
  const plaza = Math.min(box.w, box.d) * 0.16;
  const stone = mixColor(ground, [0.6, 0.6, 0.58], 0.5);
  if (cellW > 6 && cellD > 6) buildStreets(out, box, ground, nx, nz, cellW, cellD, margin);
  let placed = 0;
  for (let iz = 0; iz < nz && placed < PROPS_PER_SCENE; iz += 1) {
    for (let ix = 0; ix < nx && placed < PROPS_PER_SCENE; ix += 1) {
      if (rng() < 0.13) continue;
      const cx = box.x0 + margin + (ix + 0.5) * cellW + (rng() - 0.5) * cellW * 0.3;
      const cz = box.z0 + margin + (iz + 0.5) * cellD + (rng() - 0.5) * cellD * 0.3;
      // 场景中心留出广场，避免出生点被建筑包住。
      if (Math.hypot(cx - centerX, cz - centerZ) < plaza) continue;
      const bw = clamp(cellW * (0.48 + rng() * 0.32), 2.4, 22);
      const bd = clamp(cellD * (0.48 + rng() * 0.32), 2.4, 22);
      const tall = rng();
      const bh = 4 + tall * tall * 27 + rng() * 3;
      const tone = 0.72 + rng() * 0.4;
      const body = shade(mixColor(ground, accent, 0.07 + rng() * 0.13), tone);
      const baseY = box.y0 + 0.16;
      obstacles.push({
        x0: cx - bw / 2 - 0.5,
        x1: cx + bw / 2 + 0.5,
        z0: cz - bd / 2 - 0.5,
        z1: cz + bd / 2 + 0.5
      });
      // 台基：把建筑从地面抬起来，挡住墙脚与路面接缝。
      addBoxTo(out, cx, box.y0, cz, bw + 1.0, 0.16, bd + 1.0, shade(stone, 0.88), TILE.SIDEWALK);
      const glass = bh >= 14;
      addBoxTo(out, cx, baseY, cz, bw, bh, bd, body, glass ? TILE.GLASS : TILE.CONCRETE);
      // 墙裙与入口：底层加一圈砖石，门洞朝向 +z。
      addBoxTo(out, cx, baseY, cz, bw + 0.1, 2.2, bd + 0.1, shade(body, 0.82), TILE.BRICK);
      addBoxTo(out, cx + (rng() - 0.5) * bw * 0.35, baseY, cz + bd / 2 + 0.03,
        1.05, 2.25, 0.1, shade(mixColor(body, [0.16, 0.12, 0.09], 0.55), 0.85));
      if (rng() < 0.38) {
        addBoxTo(out, cx + (rng() - 0.5) * bw * 0.25, baseY + bh, cz,
          bw * 0.55, 1.2 + rng() * 4.5, bd * 0.55, shade(accent, 0.42 + rng() * 0.3), TILE.ROOF);
      }
      // 屋顶女儿墙与偶尔出现的水箱/天线，让天际线更有层次。
      if (bh > 7) {
        const parapet = shade(body, 0.82);
        addBoxTo(out, cx, baseY + bh, cz - bd / 2 + 0.14, bw, 0.34, 0.28, parapet, TILE.CONCRETE);
        addBoxTo(out, cx, baseY + bh, cz + bd / 2 - 0.14, bw, 0.34, 0.28, parapet, TILE.CONCRETE);
        addBoxTo(out, cx - bw / 2 + 0.14, baseY + bh, cz, 0.28, 0.34, bd, parapet, TILE.CONCRETE);
        addBoxTo(out, cx + bw / 2 - 0.14, baseY + bh, cz, 0.28, 0.34, bd, parapet, TILE.CONCRETE);
        if (tall > 0.72) {
          addBoxTo(out, cx + bw * 0.24, baseY + bh + 0.34, cz - bd * 0.2,
            1.1, 1.3, 1.1, shade(stone, 0.8), TILE.CONCRETE);
        }
        if (tall > 0.82) {
          // 通信天线：细杆 + 顶部横臂，强化高楼剪影。
          addPrismTo(out, cx - bw * 0.22, baseY + bh + 0.34, cz + bd * 0.18,
            0.08, 4 + rng() * 4, 4, [0.32, 0.33, 0.36]);
          addBoxTo(out, cx - bw * 0.22, baseY + bh + 6, cz + bd * 0.18,
            0.9, 0.06, 0.06, [0.45, 0.47, 0.5]);
        }
      }
      // 阳台：玻璃楼每两层挑出一圈薄板，打破平面玻璃墙的单调。
      if (glass && rng() < 0.55) {
        const floors = clamp(Math.floor((bh - 4) / 4.1), 1, 4);
        for (let f = 0; f < floors; f += 1) {
          const wy = baseY + 3.4 + f * 4.1;
          if (wy + 0.12 > baseY + bh - 0.5) break;
          addBoxTo(out, cx, wy, cz + bd / 2 + 0.06, bw * 0.8, 0.12, 0.36, shade(body, 0.9), TILE.CONCRETE);
          addBoxTo(out, cx, wy, cz - bd / 2 - 0.06, bw * 0.8, 0.12, 0.36, shade(body, 0.9), TILE.CONCRETE);
        }
      }
      // 窗户：玻璃楼由幕墙贴图承担；其余建筑沿两面外墙铺暖色小格。
      if (bh > 6.5 && !glass) {
        const cols = bw > 9 ? 3 : 2;
        const floors = clamp(Math.floor((bh - 2.4) / 4.1), 1, 5);
        const win = [0.96, 0.79, 0.48];
        for (let f = 0; f < floors; f += 1) {
          const wy = baseY + 2.1 + f * 4.1;
          if (wy + 1.5 > baseY + bh - 0.6) break;
          for (let c = 0; c < cols; c += 1) {
            const offset = (c - (cols - 1) / 2) * (bw / (cols + 0.8));
            addBoxTo(out, cx + offset, wy, cz + bd / 2 + 0.04, 0.92, 1.5, 0.08, win);
            addBoxTo(out, cx + bw / 2 + 0.04, wy, cz + offset * (bd / Math.max(bw, 0.1)), 0.08, 1.5, 0.92, win);
          }
        }
      }
      placed += 1;
    }
  }
}

function buildSceneProps(out, scene, box, ground, accent) {
  const seed = (num(scene.prop_seed, 7) ^ hashString(scene.id)) >>> 0;
  const rng = mulberry32(seed);
  const category = str(scene.category, 'natural');
  const id = str(scene.id, '');
  const biome = str(scene.biome_ref, '').toLowerCase();
  const margin = clamp(Math.min(box.w, box.d) * 0.055, 3, 16);
  const innerW = Math.max(2, box.w - margin * 2);
  const innerD = Math.max(2, box.d - margin * 2);
  const area = box.w * box.d;

  const coastish = id === 'coast' || id === 'harbor' || id === 'fishing_village' || biome.includes('coast');
  if (coastish) {
    buildCoast(out, box, ground, accent, rng, margin, innerW, innerD, area);
    return;
  }
  if (category === 'built') {
    buildPlaza(out, box, accent);
    buildBuildings(out, box, ground, accent, rng, margin, innerW, innerD, area);
    return;
  }
  if (biome.includes('forest') || biome.includes('grove') || id === 'forest' || id === 'rainforest') {
    buildForest(out, box, ground, accent, rng, margin, innerW, innerD, area, biome);
    return;
  }
  if (id.includes('mountain') || id.includes('peak') || id.includes('plateau') || biome.includes('mountain')) {
    buildMountains(out, box, ground, accent, rng, margin, innerW, innerD, area);
    return;
  }
  buildScatter(out, box, ground, accent, rng, margin, innerW, innerD, area, category);
}

function buildForest(out, box, ground, accent, rng, margin, innerW, innerD, area, biome) {
  const target = clamp(Math.round(area / 1400), 12, PROPS_PER_SCENE);
  const { nx, nz, cellW, cellD } = gridLayout(innerW, innerD, target);
  const leafBase = biome.includes('grove')
    ? mixColor([0.35, 0.29, 0.62], ground, 0.22)
    : mixColor([0.15, 0.4, 0.2], accent, 0.18);
  const trunk = [0.3, 0.21, 0.13];
  let placed = 0;
  for (let iz = 0; iz < nz && placed < PROPS_PER_SCENE; iz += 1) {
    for (let ix = 0; ix < nx && placed < PROPS_PER_SCENE; ix += 1) {
      const cx = box.x0 + margin + (ix + 0.5) * cellW + (rng() - 0.5) * cellW * 0.6;
      const cz = box.z0 + margin + (iz + 0.5) * cellD + (rng() - 0.5) * cellD * 0.6;
      const height = 4.5 + rng() * 6;
      const trunkH = height * 0.32;
      const radius = clamp(Math.min(cellW, cellD) * 0.24, 0.35, 2.4) * (0.7 + rng() * 0.6);
      addPrismTo(out, cx, box.y0, cz, 0.22 + rng() * 0.18, trunkH, 5, trunk, null, TILE.SIDING);
      addConeTo(out, cx, box.y0 + trunkH * 0.85, cz, radius, height * 0.62, 7,
        shade(leafBase, 0.8 + rng() * 0.4));
      if (rng() < 0.55) {
        addConeTo(out, cx, box.y0 + trunkH * 0.85 + height * 0.42, cz, radius * 0.62, height * 0.4, 6,
          shade(leafBase, 0.9 + rng() * 0.35));
      }
      placed += 1;
    }
  }
}

function buildMountains(out, box, ground, accent, rng, margin, innerW, innerD, area) {
  const target = clamp(Math.round(area / 3200), 6, 70);
  const { nx, nz, cellW, cellD } = gridLayout(innerW, innerD, target);
  const rockBase = mixColor(ground, [0.47, 0.46, 0.44], 0.4);
  let placed = 0;
  for (let iz = 0; iz < nz && placed < PROPS_PER_SCENE; iz += 1) {
    for (let ix = 0; ix < nx && placed < PROPS_PER_SCENE; ix += 1) {
      const cx = box.x0 + margin + (ix + 0.5) * cellW + (rng() - 0.5) * cellW * 0.5;
      const cz = box.z0 + margin + (iz + 0.5) * cellD + (rng() - 0.5) * cellD * 0.5;
      const half = clamp(Math.min(cellW, cellD) * (0.55 + rng() * 0.4), 2, 20);
      const height = half * (1.4 + rng() * 1.6) + 4;
      addPyramidTo(out, cx, box.y0, cz, half, half * (0.75 + rng() * 0.5), height,
        shade(mixColor(rockBase, accent, 0.06), 0.76 + rng() * 0.4), TILE.ROCK);
      if (rng() < 0.42) {
        addPyramidTo(out, cx + (rng() - 0.5) * cellW * 0.5, box.y0, cz + (rng() - 0.5) * cellD * 0.5,
          half * 0.35, half * 0.35, height * 0.3, shade(rockBase, 0.8 + rng() * 0.3), TILE.ROCK);
      }
      placed += 1;
    }
  }
}

function buildCoast(out, box, ground, accent, rng, margin, innerW, innerD, area) {
  // 蓝色水带沿 z 轴低端铺开，陆地物件放在水带之外。
  const waterDepth = clamp(box.d * 0.3, 8, 64);
  const water = [0.16, 0.38, 0.56];
  addBoxTo(out, box.x0 + box.w / 2, box.y0 + 0.01, box.z0 + waterDepth / 2,
    Math.max(box.w - 1.5, 1), 0.22, Math.max(waterDepth - 1.2, 1), water, TILE.WATER);
  addBoxTo(out, box.x0 + box.w / 2, box.y0 + 0.02, box.z0 + waterDepth,
    Math.max(box.w - 1.5, 1), 0.3, 0.9, mixColor(water, [0.55, 0.7, 0.8], 0.35), TILE.WATER);
  // 水陆之间的沙滩带。
  addBoxTo(out, box.x0 + box.w / 2, box.y0 + 0.015, box.z0 + waterDepth + 1.1,
    Math.max(box.w - 2, 1), 0.06, 2.2, [0.79, 0.7, 0.49], TILE.SAND);

  const landZ0 = box.z0 + waterDepth + 1.5;
  const landD = Math.max(box.z1 - margin - landZ0, 2);
  const target = clamp(Math.round(area / 2600), 6, 40);
  const { nx, nz, cellW, cellD } = gridLayout(innerW, landD, target);
  const rock = mixColor(ground, [0.4, 0.39, 0.36], 0.45);
  let placed = 0;
  for (let iz = 0; iz < nz && placed < PROPS_PER_SCENE; iz += 1) {
    for (let ix = 0; ix < nx && placed < PROPS_PER_SCENE; ix += 1) {
      const cx = box.x0 + margin + (ix + 0.5) * cellW + (rng() - 0.5) * cellW * 0.4;
      const cz = landZ0 + (iz + 0.5) * cellD + (rng() - 0.5) * cellD * 0.4;
      if (cz > box.z1 - margin) continue;
      if (rng() < 0.5) {
        addBoxTo(out, cx, box.y0, cz, 1.5 + rng() * 4, 2 + rng() * 5.5, 1.5 + rng() * 4,
          shade(mixColor(ground, accent, 0.1), 0.72 + rng() * 0.4), TILE.SIDING);
      } else {
        addPyramidTo(out, cx, box.y0, cz, 1 + rng() * 3, 1 + rng() * 3, 1.2 + rng() * 4,
          shade(rock, 0.8 + rng() * 0.35), TILE.ROCK);
      }
      placed += 1;
    }
  }
}

function buildScatter(out, box, ground, accent, rng, margin, innerW, innerD, area, category) {
  const target = clamp(Math.round(area / 2600), 7, 60);
  const { nx, nz, cellW, cellD } = gridLayout(innerW, innerD, target);
  const green = category === 'fantasy'
    ? mixColor([0.32, 0.3, 0.6], accent, 0.25)
    : mixColor([0.2, 0.44, 0.26], accent, 0.12);
  const rock = mixColor(ground, [0.44, 0.44, 0.43], 0.5);
  let placed = 0;
  for (let iz = 0; iz < nz && placed < PROPS_PER_SCENE; iz += 1) {
    for (let ix = 0; ix < nx && placed < PROPS_PER_SCENE; ix += 1) {
      const cx = box.x0 + margin + (ix + 0.5) * cellW + (rng() - 0.5) * cellW * 0.65;
      const cz = box.z0 + margin + (iz + 0.5) * cellD + (rng() - 0.5) * cellD * 0.65;
      if (rng() < 0.45) {
        const half = 1 + rng() * 3;
        addPyramidTo(out, cx, box.y0, cz, half, half * (0.7 + rng() * 0.5), 0.8 + rng() * 3.6,
          shade(rock, 0.78 + rng() * 0.4), TILE.ROCK);
      } else {
        const size = 0.8 + rng() * 1.6;
        addBoxTo(out, cx, box.y0, cz, size, 0.5 + rng() * 1.2, size, shade(green, 0.8 + rng() * 0.4));
        addConeTo(out, cx, box.y0 + 0.5 + rng() * 1.2, cz, size * 1.2, 1.2 + rng() * 2.4, 6,
          shade(green, 0.85 + rng() * 0.4));
      }
      placed += 1;
    }
  }
}

// 建筑足迹（世界坐标矩形），供移动碰撞与 NPC 寻路使用；每次重建静态几何时刷新。
export const obstacles = [];

function buildWorldGeometry(scenes) {
  const out = [];
  obstacles.length = 0;
  for (const scene of scenes) {
    const box = sceneBox(scene);
    if (!box) continue;
    const palette = FALLBACK_PALETTE[str(scene.category, 'natural')] || FALLBACK_PALETTE.natural;
    const ground = hexToRgb(scene.ground_color, hexToRgb(palette[0]));
    const accent = hexToRgb(scene.accent_color, hexToRgb(palette[1]));
    // 地面贴图按类别分配：城市铺装、荒野草地；海岸场景的水/沙由 buildCoast 覆盖。
    const groundTile = scene.category === 'built' ? TILE.PAVEMENT : TILE.GRASS;

    // 地面：底层实体用于边界断面，表层细格提供色彩纹理。
    addBoxTo(out, (box.x0 + box.x1) / 2, box.y0 - 1.6, (box.z0 + box.z1) / 2, box.w, 1.6, box.d, shade(ground, 0.72), groundTile);
    addGroundTiles(out, box, ground, groundTile);

    // 边框线：强调分区边界，同时不遮挡地面。
    const border = mixColor(shade(accent, 0.55), ground, 0.2);
    const bw = 0.55;
    addBoxTo(out, (box.x0 + box.x1) / 2, box.y0 + 0.015, box.z0 + bw / 2, box.w, 0.06, bw, border, TILE.SIDEWALK);
    addBoxTo(out, (box.x0 + box.x1) / 2, box.y0 + 0.015, box.z1 - bw / 2, box.w, 0.06, bw, border, TILE.SIDEWALK);
    addBoxTo(out, box.x0 + bw / 2, box.y0 + 0.015, (box.z0 + box.z1) / 2, bw, 0.06, box.d, border, TILE.SIDEWALK);
    addBoxTo(out, box.x1 - bw / 2, box.y0 + 0.015, (box.z0 + box.z1) / 2, bw, 0.06, box.d, border, TILE.SIDEWALK);

    buildSceneProps(out, scene, box, ground, accent);
    buildInteractProps(out, scene, box, ground, accent);
  }
  return new Float32Array(out);
}

export { buildWorldGeometry };

// 服务端下发的可交互物件：用低模几何体表现，并在地面加一圈暗影。
function buildInteractProps(out, scene, box, ground, accent) {
  const carriedId = str(state.carried && state.carried.id);
  const props = list(scene.props).filter((prop) => !carriedId || str(prop.id) !== carriedId);
  if (!props.length) return;
  const shadow = shade(ground, 0.58);
  const wood = [0.44, 0.32, 0.21];
  const stone = mixColor(ground, [0.58, 0.58, 0.56], 0.6);
  const cloth = mixColor(accent, wood, 0.35);
  for (const prop of props) {
    const pos = list(prop.position_m);
    const x = num(pos[0], box.x0 + box.w / 2);
    const z = num(pos[2], box.z0 + box.d / 2);
    const y = box.y0;
    const kind = str(prop.kind);
    const ring = kind === 'boat' ? 2.4 : 1.4;
    pushQuad(out,
      [x - ring, y + 0.02, z - ring], [x - ring, y + 0.02, z + ring],
      [x + ring, y + 0.02, z + ring], [x + ring, y + 0.02, z - ring],
      [0, 1, 0], shadow);

    if (kind === 'well') {
      addPrismTo(out, x, y, z, 1.15, 1.0, 10, stone, null, TILE.ROCK);
      addPrismTo(out, x, y + 1.0, z, 0.72, 0.06, 10, [0.3, 0.55, 0.68], null, TILE.WATER);
      addBoxTo(out, x - 0.95, y + 1.0, z, 0.14, 1.9, 0.14, wood, TILE.WOOD);
      addBoxTo(out, x + 0.95, y + 1.0, z, 0.14, 1.9, 0.14, wood, TILE.WOOD);
      addPyramidTo(out, x, y + 2.85, z, 1.3, 1.3, 0.6, shade(wood, 0.8), TILE.ROOF);
    } else if (kind === 'bench') {
      addBoxTo(out, x, y + 0.42, z, 2.3, 0.16, 0.62, wood, TILE.WOOD);
      addBoxTo(out, x, y + 0.58, z - 0.26, 2.3, 0.5, 0.1, shade(wood, 0.9), TILE.WOOD);
      addBoxTo(out, x - 0.9, y, z, 0.14, 0.45, 0.5, shade(wood, 0.7), TILE.WOOD);
      addBoxTo(out, x + 0.9, y, z, 0.14, 0.45, 0.5, shade(wood, 0.7), TILE.WOOD);
    } else if (kind === 'stall') {
      addBoxTo(out, x, y + 0.9, z, 2.4, 0.16, 1.2, wood, TILE.WOOD);
      addBoxTo(out, x - 1.0, y, z - 0.45, 0.12, 1.9, 0.12, wood, TILE.WOOD);
      addBoxTo(out, x + 1.0, y, z - 0.45, 0.12, 1.9, 0.12, wood, TILE.WOOD);
      addPyramidTo(out, x, y + 2.05, z - 0.1, 1.5, 0.85, 0.55, cloth);
    } else if (kind === 'notice') {
      addBoxTo(out, x - 0.7, y, z, 0.12, 2.1, 0.12, wood, TILE.WOOD);
      addBoxTo(out, x + 0.7, y, z, 0.12, 2.1, 0.12, wood, TILE.WOOD);
      addBoxTo(out, x, y + 1.1, z, 1.7, 0.95, 0.09, [0.72, 0.62, 0.45]);
    } else if (kind === 'crate') {
      addBoxTo(out, x, y, z, 1.0, 0.92, 1.0, wood, TILE.WOOD);
      addBoxTo(out, x, y + 0.92, z, 1.04, 0.08, 1.04, shade(wood, 0.75), TILE.WOOD);
    } else if (kind === 'boat') {
      addBoxTo(out, x, y + 0.18, z, 3.6, 0.4, 1.15, wood, TILE.WOOD);
      addBoxTo(out, x, y + 0.58, z - 0.5, 3.6, 0.35, 0.1, shade(wood, 0.85), TILE.WOOD);
      addBoxTo(out, x, y + 0.58, z + 0.5, 3.6, 0.35, 0.1, shade(wood, 0.85), TILE.WOOD);
    } else if (kind === 'net') {
      addBoxTo(out, x - 1.0, y, z, 0.12, 1.8, 0.12, wood, TILE.WOOD);
      addBoxTo(out, x + 1.0, y, z, 0.12, 1.8, 0.12, wood, TILE.WOOD);
      addBoxTo(out, x, y + 0.65, z, 2.0, 1.05, 0.05, [0.55, 0.58, 0.55]);
    } else if (kind === 'driftwood' || kind === 'log') {
      addBoxTo(out, x, y + 0.12, z, kind === 'log' ? 3.2 : 2.5, 0.38, 0.42, shade(wood, 1.05), TILE.WOOD);
      addBoxTo(out, x + 0.7, y + 0.5, z + 0.05, 0.9, 0.12, 0.16, shade(wood, 0.8), TILE.WOOD);
    } else if (kind === 'campfire') {
      for (let i = 0; i < 6; i += 1) {
        const a = (i / 6) * TAU;
        addBoxTo(out, x + Math.cos(a) * 1.0, y, z + Math.sin(a) * 1.0, 0.34, 0.3, 0.34, stone, TILE.ROCK);
      }
      addBoxTo(out, x - 0.3, y + 0.1, z, 0.22, 0.22, 1.3, shade(wood, 0.75), TILE.WOOD);
      addBoxTo(out, x + 0.3, y + 0.1, z, 0.22, 0.22, 1.3, shade(wood, 0.75), TILE.WOOD);
      addBoxTo(out, x, y + 0.3, z, 0.5, 0.42, 0.5, [1.0, 0.52, 0.18]);
    } else if (kind === 'tree') {
      addPrismTo(out, x, y, z, 0.5, 4.6, 7, shade(wood, 0.85), null, TILE.SIDING);
      addConeTo(out, x, y + 3.2, z, 2.6, 4.2, 9, shade(mixColor(accent, wood, 0.25), 0.9));
      addConeTo(out, x, y + 4.6, z, 1.8, 3.4, 8, shade(mixColor(accent, wood, 0.15), 1.0));
    } else if (kind === 'rock') {
      addPyramidTo(out, x, y, z, 1.9, 1.5, 1.6, shade(stone, 0.92), TILE.ROCK);
      addPyramidTo(out, x + 1.0, y, z - 0.6, 0.9, 0.8, 0.7, shade(stone, 0.8), TILE.ROCK);
    } else if (kind === 'berry') {
      for (let i = 0; i < 3; i += 1) {
        const bx = x + (i - 1) * 0.75;
        addConeTo(out, bx, y + 0.2, z, 0.7, 1.25, 7, shade(accent, 0.72));
        addBoxTo(out, bx + 0.2, y + 0.55, z + 0.35, 0.16, 0.16, 0.16, [0.65, 0.16, 0.28]);
      }
    }
  }
}

export function sceneSignature(scenes) {
  const safe = (value) => {
    try { return JSON.stringify(value === undefined ? null : value); } catch (error) { return ''; }
  };
  return scenes.map((scene) => [
    str(scene.id),
    safe(scene.bounds_m),
    safe(scene.center_m),
    safe(scene.size_m),
    str(scene.ground_color),
    str(scene.accent_color),
    num(scene.prop_seed, 0),
    str(scene.category),
    str(scene.biome_ref),
    safe(scene.props)
  ].join('~')).join('|');
}

/* ---------------- 场景 / 坐标辅助 ---------------- */

export function rebuildSceneCache(scenes) {
  state.scenes = scenes;
  state.sceneEntries = scenes.map((scene) => ({ scene, box: sceneBox(scene) })).filter((item) => item.box);
}

export function sceneAt(x, z) {
  for (let i = state.sceneEntries.length - 1; i >= 0; i -= 1) {
    const item = state.sceneEntries[i];
    const b = item.box;
    if (x >= b.x0 && x <= b.x1 && z >= b.z0 && z <= b.z1) return item;
  }
  return null;
}

export function floorAt(x, z) {
  const item = sceneAt(x, z);
  return item ? item.box.y0 : state.player.y;
}

export function floorForSceneId(sceneId, fallback) {
  const id = str(sceneId);
  for (const item of state.sceneEntries) {
    if (str(item.scene.id) === id) return item.box.y0;
  }
  return num(fallback, 0);
}

export function npcFootY(npc, position) {
  const floor = floorForSceneId(npc && npc.scene_id, position[1]);
  const y = num(position[1], floor);
  // 若后端给的是场景体积中心而非脚底，贴近地面，避免角色悬浮。
  return Math.abs(y - floor) > 3 ? floor : y;
}

export function setPlayerFeet(feet) {
  const p = vec3(feet);
  if (!p) return;
  state.player.x = p[0];
  state.player.z = p[2];
  state.player.y = floorAt(p[0], p[2]);
  if (!Number.isFinite(state.player.y)) state.player.y = p[1];
}

export function syncServerFeet(view) {
  const player = view && view.player ? view.player : {};
  const feet = vec3(player.feet_m, vec3(player.position_m));
  if (!feet) return;
  if (!state.spawned) {
    setPlayerFeet(feet);
    state.spawned = true;
    state.serverFeet = feet.slice();
    return;
  }
  if (state.serverFeet) {
    const moved = Math.hypot(
      feet[0] - state.serverFeet[0],
      feet[1] - state.serverFeet[1],
      feet[2] - state.serverFeet[2]
    );
    // 只有服务端位置发生超过 2m 的变化（例如任务旅行、重置）才同步本地。
    if (moved > 2) setPlayerFeet(feet);
  } else {
    setPlayerFeet(feet);
  }
  state.serverFeet = feet.slice();
}
