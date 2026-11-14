'use strict';
/* NPC 客户端寻路：把服务端下发的节点位置作为锚点，做平滑移动、
   建筑绕行与锚点附近的局部游走，避免 NPC 每 5 秒瞬移。 */

import { TAU, clamp, num, str, list, vec3, hashString, mulberry32, state } from './core.js';
import { obstacles, sceneAt } from './geometry.js';

const NPC_SPEED = 1.35;
const NPC_RADIUS = 0.55;
const ARRIVE_DISTANCE = 0.3;
const SNAP_DISTANCE = 30;
const WANDER_RADIUS = 3.2;

const motions = new Map();

function blocked(x, z, radius) {
  for (let i = 0; i < obstacles.length; i += 1) {
    const o = obstacles[i];
    const px = clamp(x, o.x0, o.x1);
    const pz = clamp(z, o.z0, o.z1);
    if (Math.hypot(x - px, z - pz) < radius) return true;
  }
  return false;
}

// 服务端锚点可能落在建筑体积内：向外螺旋查找最近的可行走点。
function resolveFree(sceneId, x, z) {
  const inside = (px, pz) => {
    const scene = sceneAt(px, pz);
    if (!scene) return false;
    if (sceneId && str(scene.scene.id) !== sceneId) return false;
    return !blocked(px, pz, NPC_RADIUS);
  };
  if (inside(x, z)) return [x, z];
  for (let radius = 0.8; radius <= 8; radius += 0.8) {
    for (let i = 0; i < 12; i += 1) {
      const angle = (i / 12) * TAU + radius;
      const nx = x + Math.cos(angle) * radius;
      const nz = z + Math.sin(angle) * radius;
      if (inside(nx, nz)) return [nx, nz];
    }
  }
  return [x, z];
}

function createMotion(npc, x, z, sceneId) {
  const id = str(npc && npc.id, str(npc && npc.name, 'npc'));
  const [ax, az] = resolveFree(sceneId, x, z);
  return {
    id,
    x: ax,
    z: az,
    anchorX: ax,
    anchorZ: az,
    targetX: ax,
    targetZ: az,
    serverX: x,
    serverZ: z,
    sceneId,
    heading: 0,
    walking: false,
    pausedUntil: 0,
    wanderRound: 0,
    seenAt: performance.now()
  };
}

function walkable(m, x, z, others) {
  const scene = sceneAt(x, z);
  if (!scene) return false;
  if (m.sceneId && str(scene.scene.id) !== m.sceneId) return false;
  if (blocked(x, z, NPC_RADIUS)) return false;
  // 不穿过玩家：把玩家当作移动的圆形障碍。
  const dx = x - state.player.x;
  const dz = z - state.player.z;
  if (Math.hypot(dx, dz) < NPC_RADIUS + 0.45) return false;
  for (let i = 0; i < others.length; i += 1) {
    const other = others[i];
    if (other.id === m.id) continue;
    if (Math.hypot(x - other.x, z - other.z) < NPC_RADIUS * 1.6) return false;
  }
  return true;
}

// 采样若干方向，优先选最接近目标方向且可通行的角度，形成轻量绕障。
function steer(m, tx, tz, dt, others) {
  const dx = tx - m.x;
  const dz = tz - m.z;
  const distance = Math.hypot(dx, dz);
  if (distance < ARRIVE_DISTANCE) return true;
  const desired = Math.atan2(dz, dx);
  const stepLength = Math.min(NPC_SPEED * dt, distance);
  const offsets = [0, 0.32, -0.32, 0.65, -0.65, 1.0, -1.0, 1.4, -1.4, 1.9, -1.9, 2.5, -2.5];
  for (const offset of offsets) {
    const angle = desired + offset;
    const nx = m.x + Math.cos(angle) * stepLength;
    const nz = m.z + Math.sin(angle) * stepLength;
    if (walkable(m, nx, nz, others)) {
      m.x = nx;
      m.z = nz;
      m.heading = angle;
      m.walking = true;
      return false;
    }
  }
  m.walking = false;
  return false;
}

function pickWanderTarget(m) {
  m.wanderRound += 1;
  const rng = mulberry32((hashString(m.id) ^ (m.wanderRound * 0x9e3779b1)) >>> 0);
  for (let i = 0; i < 10; i += 1) {
    const angle = rng() * Math.PI * 2;
    const radius = 0.7 + rng() * (WANDER_RADIUS - 0.7);
    const tx = m.anchorX + Math.cos(angle) * radius;
    const tz = m.anchorZ + Math.sin(angle) * radius;
    if (walkable(m, tx, tz, [])) {
      m.targetX = tx;
      m.targetZ = tz;
      return;
    }
  }
  m.targetX = m.anchorX;
  m.targetZ = m.anchorZ;
}

/* ---------------- 对外接口 ---------------- */

export function syncNpcMotion(npcs, dt) {
  const now = performance.now();
  const seen = new Set();
  const list_ = list(npcs);
  const active = [];
  for (const npc of list_) {
    const p = vec3(npc && npc.position_m);
    if (!p) continue;
    const id = str(npc.id, str(npc.name, 'npc'));
    let m = motions.get(id);
    if (!m) {
      m = createMotion(npc, p[0], p[2], str(npc.scene_id));
      motions.set(id, m);
      seen.add(id);
      active.push(m);
      continue;
    }
    seen.add(id);
    active.push(m);
    m.seenAt = now;

    // 服务端锚点变化（日程/迁移）：走向新位置；跨场景或过远则直接对齐。
    const sceneId = str(npc.scene_id, m.sceneId);
    const anchorMoved = Math.hypot(p[0] - m.serverX, p[2] - m.serverZ);
    if (anchorMoved > 0.35) {
      const [ax, az] = resolveFree(sceneId, p[0], p[2]);
      const jump = Math.hypot(ax - m.x, az - m.z);
      if (anchorMoved > SNAP_DISTANCE || jump > SNAP_DISTANCE || sceneId !== m.sceneId) {
        m.x = ax;
        m.z = az;
        m.walking = false;
      }
      m.anchorX = ax;
      m.anchorZ = az;
      m.targetX = ax;
      m.targetZ = az;
      m.serverX = p[0];
      m.serverZ = p[2];
      m.pausedUntil = 0;
      m.sceneId = sceneId;
    }
  }

  // 清掉长时间未出现的 NPC，避免切换场景后残留。
  for (const [id, m] of motions) {
    if (!seen.has(id) && now - m.seenAt > 15000) motions.delete(id);
  }

  const step = clamp(num(dt, 0), 0, 0.1);
  if (step <= 0) return;
  for (const m of active) {
    m.walking = false;
    if (now < m.pausedUntil) continue;
    const anchored = Math.hypot(m.x - m.anchorX, m.z - m.anchorZ);
    if (anchored > WANDER_RADIUS + 0.6) {
      // 偏离锚点过远（例如被建筑挤开），先回到锚点附近。
      m.targetX = m.anchorX;
      m.targetZ = m.anchorZ;
    }
    const arrived = steer(m, m.targetX, m.targetZ, step, active);
    if (arrived) {
      // 到达一个游走点后停顿片刻，再在锚点附近挑下一个点。
      pickWanderTarget(m);
      m.pausedUntil = now + 600 + (hashString(m.id + m.wanderRound) % 2200);
    }
  }
}

export function npcRenderPosition(npc) {
  if (!npc || typeof npc !== 'object') return null;
  const id = str(npc.id, str(npc.name, 'npc'));
  const m = motions.get(id);
  if (m) return { x: m.x, z: m.z, walking: m.walking, heading: m.heading };
  const p = vec3(npc.position_m);
  return p ? { x: p[0], z: p[2], walking: false, heading: 0 } : null;
}
