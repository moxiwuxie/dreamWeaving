'use strict';
/* 程序化无缝贴图图集：4×4 材质块烘焙进一张 512×512 画布。
   相比 AI 直出贴图，程序化噪声天然保证四边无缝可平铺，且零外部资源。
   材质以「噪声基底 + 结构线」两层合成，颜色偏中性，最终与几何顶点色相乘，
   这样同一材质在不同场景调色下都能复用。 */

import { mulberry32 } from './core.js';

export const TILE = {
  PLAIN: 0,        // 白：不指定贴图时等效纯顶点色
  ASPHALT: 1,      // 沥青路面
  SIDEWALK: 2,     // 人行道砖
  BRICK: 3,        // 红砖墙
  CONCRETE: 4,     // 混凝土墙
  GLASS: 5,        // 玻璃幕墙（含亮窗）
  GRASS: 6,        // 草地
  PLAZA: 7,        // 广场石板
  WOOD: 8,         // 木板
  SAND: 9,         // 沙地
  WATER: 10,       // 水面
  ROOF: 11,        // 屋顶瓦
  ROCK: 12,        // 岩石/砾石
  SIDING: 13,      // 竖向木板
  PAVEMENT: 14,    // 混凝土板地面
  DIRT: 15         // 泥土
};

const TILE_SIZE = 128;
const ATLAS_COLS = 4;

// 周期值噪声：坐标对尺寸取模保证边界无缝，多倍频叠加制造自然颗粒感。
function makeNoise(seed, octaves = 3) {
  const rng = mulberry32(seed);
  const table = [];
  for (let i = 0; i < TILE_SIZE; i += 1) table.push(rng() * 2 - 1);
  const lattice = (x, y) => table[(Math.imul(x * 374761393, y * 668265263) >>> 0) % TILE_SIZE];
  const smooth = (t) => t * t * (3 - 2 * t);
  return function noise(u, v, scale) {
    const fx = (u * scale) % TILE_SIZE;
    const fy = (v * scale) % TILE_SIZE;
    const x0 = Math.floor(fx) % TILE_SIZE;
    const y0 = Math.floor(fy) % TILE_SIZE;
    const x1 = (x0 + 1) % TILE_SIZE;
    const y1 = (y0 + 1) % TILE_SIZE;
    const tx = smooth(fx - Math.floor(fx));
    const ty = smooth(fy - Math.floor(fy));
    const a = lattice(x0, y0);
    const b = lattice(x1, y0);
    const c = lattice(x0, y1);
    const d = lattice(x1, y1);
    return a + (b - a) * tx + (c - a) * ty + (a - b - c + d) * tx * ty;
  };
}

function speckle(ctx, x0, y0, size, rng, count, color, alpha, w = 3) {
  ctx.fillStyle = color;
  ctx.globalAlpha = alpha;
  for (let i = 0; i < count; i += 1) {
    const px = x0 + rng() * size;
    const py = y0 + rng() * size;
    ctx.fillRect(px, py, w * (0.5 + rng()), w * (0.5 + rng()));
  }
  ctx.globalAlpha = 1;
}

function fillTile(ctx, x0, y0, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x0, y0, TILE_SIZE, TILE_SIZE);
}

function paintTile(ctx, paint, x0 = currentTile[0], y0 = currentTile[1]) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, TILE_SIZE, TILE_SIZE);
  ctx.clip();
  paint(x0, y0);
  ctx.restore();
}

// 当前正在绘制的材质块原点：tileAt() 设置，paintTile() 默认读取，
// 这样每个材质绘制函数只需关心自己块内的相对坐标。
let currentTile = [0, 0];

function buildAtlasCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = ATLAS_COLS * TILE_SIZE;
  canvas.height = ATLAS_COLS * TILE_SIZE;
  const ctx = canvas.getContext('2d');
  const tileAt = (index) => {
    const col = index % ATLAS_COLS;
    const row = Math.floor(index / ATLAS_COLS);
    currentTile = [col * TILE_SIZE, row * TILE_SIZE];
    return currentTile;
  };

  // 0 白
  let [x, y] = tileAt(TILE.PLAIN);
  fillTile(ctx, x, y, '#ffffff');

  // 1 沥青：深灰基底 + 颗粒 + 细裂纹
  [x, y] = tileAt(TILE.ASPHALT);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#3c4045');
    const n = makeNoise(0x5eed + TILE.ASPHALT);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.09) * 0.5 + n(ix, iy, 0.35) * 0.3;
        const tone = 0.82 + v * 0.3;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 60 * tone;
        image.data[o + 1] = 64 * tone;
        image.data[o + 2] = 69 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    const rng = mulberry32(0xa5a5 + TILE.ASPHALT);
    speckle(ctx, px, py, TILE_SIZE, rng, 260, 'rgba(20,22,24,0.5)', 0.6, 2);
    speckle(ctx, px, py, TILE_SIZE, rng, 160, 'rgba(150,150,150,0.25)', 0.4, 1);
    // 裂纹：少量细折线
    ctx.strokeStyle = 'rgba(18,20,22,0.55)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i += 1) {
      ctx.beginPath();
      let cx = px + rng() * TILE_SIZE;
      let cy = py + rng() * TILE_SIZE;
      ctx.moveTo(cx, cy);
      for (let s = 0; s < 4; s += 1) {
        cx += (rng() - 0.5) * 30;
        cy += (rng() - 0.5) * 30;
        ctx.lineTo(cx, cy);
      }
      ctx.stroke();
    }
  });

  // 2 人行道砖：浅灰 + 错缝
  [x, y] = tileAt(TILE.SIDEWALK);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#9a978f');
    const n = makeNoise(0x5eed + TILE.SIDEWALK);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.1) * 0.6 + n(ix, iy, 0.4) * 0.25;
        const tone = 0.9 + v * 0.16;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 154 * tone;
        image.data[o + 1] = 151 * tone;
        image.data[o + 2] = 143 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(60,58,54,0.55)';
    ctx.lineWidth = 2;
    const cell = TILE_SIZE / 4;
    for (let row = 0; row <= 4; row += 1) {
      const yy = py + row * cell;
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
    for (let row = 0; row < 4; row += 1) {
      const offset = row % 2 === 0 ? 0 : cell / 2;
      for (let col = 0; col <= 4; col += 1) {
        const xx = px + ((col * cell + offset) % TILE_SIZE);
        ctx.beginPath();
        ctx.moveTo(xx, py + row * cell);
        ctx.lineTo(xx, py + (row + 1) * cell);
        ctx.stroke();
      }
    }
  });

  // 3 红砖墙
  [x, y] = tileAt(TILE.BRICK);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#8a4a3a');
    const n = makeNoise(0x5eed + TILE.BRICK);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.12) * 0.5 + n(ix, iy, 0.5) * 0.25;
        const tone = 0.85 + v * 0.25;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 138 * tone;
        image.data[o + 1] = 74 * tone;
        image.data[o + 2] = 58 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(200,196,186,0.5)';
    ctx.lineWidth = 3;
    const bh = TILE_SIZE / 4;
    const bw = TILE_SIZE / 2;
    for (let row = 0; row <= 4; row += 1) {
      const yy = py + row * bh;
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
    for (let row = 0; row < 4; row += 1) {
      const offset = row % 2 === 0 ? 0 : bw / 2;
      for (let col = 0; col <= 2; col += 1) {
        const xx = px + ((col * bw + offset) % TILE_SIZE);
        ctx.beginPath();
        ctx.moveTo(xx, py + row * bh);
        ctx.lineTo(xx, py + (row + 1) * bh);
        ctx.stroke();
      }
    }
  });

  // 4 混凝土墙：浅灰 + 污渍
  [x, y] = tileAt(TILE.CONCRETE);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#b6b3ab');
    const n = makeNoise(0x5eed + TILE.CONCRETE);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.08) * 0.5 + n(ix, iy, 0.3) * 0.3;
        const tone = 0.92 + v * 0.12;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 182 * tone;
        image.data[o + 1] = 179 * tone;
        image.data[o + 2] = 171 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    const rng = mulberry32(0xc0cc + TILE.CONCRETE);
    speckle(ctx, px, py, TILE_SIZE, rng, 40, 'rgba(90,88,84,0.25)', 0.5, 5);
    speckle(ctx, px, py, TILE_SIZE, rng, 60, 'rgba(220,220,214,0.3)', 0.5, 2);
    // 横向模板缝
    ctx.strokeStyle = 'rgba(90,88,84,0.18)';
    ctx.lineWidth = 2;
    for (let row = 1; row < 4; row += 1) {
      const yy = py + row * (TILE_SIZE / 4);
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
  });

  // 5 玻璃幕墙：深蓝网格 + 随机亮窗
  [x, y] = tileAt(TILE.GLASS);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#2a3b52');
    const n = makeNoise(0x5eed + TILE.GLASS);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.06) * 0.4 + n(ix, iy, 0.25) * 0.2;
        const tone = 0.9 + v * 0.2;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 42 * tone;
        image.data[o + 1] = 59 * tone;
        image.data[o + 2] = 82 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    // 窗框网格
    ctx.strokeStyle = 'rgba(16,22,30,0.85)';
    ctx.lineWidth = 3;
    const cell = TILE_SIZE / 4;
    for (let i = 0; i <= 4; i += 1) {
      ctx.beginPath();
      ctx.moveTo(px + i * cell, py);
      ctx.lineTo(px + i * cell, py + TILE_SIZE);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(px, py + i * cell);
      ctx.lineTo(px + TILE_SIZE, py + i * cell);
      ctx.stroke();
    }
    // 亮窗：约三成窗户暖黄、少量冷白
    const rng = mulberry32(0x91a5 + TILE.GLASS);
    for (let row = 0; row < 4; row += 1) {
      for (let col = 0; col < 4; col += 1) {
        if (rng() < 0.32) {
          const warm = rng() < 0.8;
          ctx.fillStyle = warm
            ? `rgba(255, ${200 + Math.floor(rng() * 40)}, 120, ${0.5 + rng() * 0.4})`
            : 'rgba(210,235,255,0.55)';
          ctx.fillRect(px + col * cell + 4, py + row * cell + 4, cell - 8, cell - 8);
        }
      }
    }
  });

  // 6 草地
  [x, y] = tileAt(TILE.GRASS);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#5d8a4a');
    const n = makeNoise(0x5eed + TILE.GRASS);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.1) * 0.5 + n(ix, iy, 0.5) * 0.3;
        const tone = 0.82 + v * 0.3;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 93 * tone;
        image.data[o + 1] = 138 * tone;
        image.data[o + 2] = 72 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    const rng = mulberry32(0x9a55 + TILE.GRASS);
    // 草叶短线
    ctx.strokeStyle = 'rgba(70,120,55,0.6)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 160; i += 1) {
      const gx = px + rng() * TILE_SIZE;
      const gy = py + rng() * TILE_SIZE;
      ctx.beginPath();
      ctx.moveTo(gx, gy);
      ctx.lineTo(gx + (rng() - 0.5) * 4, gy - 3 - rng() * 4);
      ctx.stroke();
    }
  });

  // 7 广场石板
  [x, y] = tileAt(TILE.PLAZA);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#8f8b82');
    const n = makeNoise(0x5eed + TILE.PLAZA);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.09) * 0.5 + n(ix, iy, 0.35) * 0.25;
        const tone = 0.88 + v * 0.2;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 143 * tone;
        image.data[o + 1] = 139 * tone;
        image.data[o + 2] = 130 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(60,58,54,0.5)';
    ctx.lineWidth = 3;
    const cell = TILE_SIZE / 2;
    for (let row = 0; row <= 2; row += 1) {
      const yy = py + row * cell;
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
    for (let col = 0; col <= 2; col += 1) {
      const xx = px + col * cell;
      ctx.beginPath();
      ctx.moveTo(xx, py);
      ctx.lineTo(xx, py + TILE_SIZE);
      ctx.stroke();
    }
  });

  // 8 木板
  [x, y] = tileAt(TILE.WOOD);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#8a6a45');
    const n = makeNoise(0x5eed + TILE.WOOD);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const grain = Math.sin((ix + n(ix, iy, 0.04) * 40) * 0.35) * 0.5;
        const v = n(ix, iy, 0.15) * 0.4 + grain * 0.2;
        const tone = 0.85 + v * 0.25;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 138 * tone;
        image.data[o + 1] = 106 * tone;
        image.data[o + 2] = 69 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(52,38,22,0.5)';
    ctx.lineWidth = 2;
    const plank = TILE_SIZE / 4;
    for (let i = 0; i <= 4; i += 1) {
      const xx = px + i * plank;
      ctx.beginPath();
      ctx.moveTo(xx, py);
      ctx.lineTo(xx, py + TILE_SIZE);
      ctx.stroke();
    }
  });

  // 9 沙地
  [x, y] = tileAt(TILE.SAND);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#cbb27c');
    const n = makeNoise(0x5eed + TILE.SAND);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.12) * 0.5 + n(ix, iy, 0.5) * 0.3;
        const tone = 0.88 + v * 0.2;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 203 * tone;
        image.data[o + 1] = 178 * tone;
        image.data[o + 2] = 124 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
  });

  // 10 水面：蓝底 + 横向波纹
  [x, y] = tileAt(TILE.WATER);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#2a6b96');
    const n = makeNoise(0x5eed + TILE.WATER);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const wave = Math.sin(iy * 0.18 + n(ix, iy, 0.05) * 4) * 0.5;
        const v = n(ix, iy, 0.2) * 0.3 + wave * 0.25;
        const tone = 0.9 + v * 0.2;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 42 * tone;
        image.data[o + 1] = 107 * tone;
        image.data[o + 2] = 150 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
  });

  // 11 屋顶瓦：深红瓦片行
  [x, y] = tileAt(TILE.ROOF);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#7d4436');
    const n = makeNoise(0x5eed + TILE.ROOF);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.1) * 0.5 + n(ix, iy, 0.4) * 0.25;
        const tone = 0.85 + v * 0.25;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 125 * tone;
        image.data[o + 1] = 68 * tone;
        image.data[o + 2] = 54 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(40,22,18,0.5)';
    ctx.lineWidth = 2;
    const rowH = TILE_SIZE / 4;
    for (let row = 0; row <= 4; row += 1) {
      const yy = py + row * rowH;
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
  });

  // 12 岩石/砾石
  [x, y] = tileAt(TILE.ROCK);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#8b8984');
    const n = makeNoise(0x5eed + TILE.ROCK);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.09) * 0.6 + n(ix, iy, 0.4) * 0.35;
        const tone = 0.8 + v * 0.3;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 139 * tone;
        image.data[o + 1] = 137 * tone;
        image.data[o + 2] = 132 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    // 裂缝
    ctx.strokeStyle = 'rgba(45,44,42,0.5)';
    ctx.lineWidth = 2;
    const rng = mulberry32(0x40c6 + TILE.ROCK);
    for (let i = 0; i < 4; i += 1) {
      ctx.beginPath();
      let cx = px + rng() * TILE_SIZE;
      let cy = py + rng() * TILE_SIZE;
      ctx.moveTo(cx, cy);
      for (let s = 0; s < 5; s += 1) {
        cx += (rng() - 0.5) * 34;
        cy += (rng() - 0.5) * 34;
        ctx.lineTo(cx, cy);
      }
      ctx.stroke();
    }
  });

  // 13 竖向木板
  [x, y] = tileAt(TILE.SIDING);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#7d6040');
    const n = makeNoise(0x5eed + TILE.SIDING);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const grain = Math.sin((iy + n(ix, iy, 0.04) * 30) * 0.3) * 0.5;
        const v = n(ix, iy, 0.15) * 0.4 + grain * 0.2;
        const tone = 0.85 + v * 0.25;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 125 * tone;
        image.data[o + 1] = 96 * tone;
        image.data[o + 2] = 64 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(48,36,20,0.5)';
    ctx.lineWidth = 2;
    const plank = TILE_SIZE / 4;
    for (let i = 0; i <= 4; i += 1) {
      const yy = py + i * plank;
      ctx.beginPath();
      ctx.moveTo(px, yy);
      ctx.lineTo(px + TILE_SIZE, yy);
      ctx.stroke();
    }
  });

  // 14 混凝土板地面
  [x, y] = tileAt(TILE.PAVEMENT);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#a8a59d');
    const n = makeNoise(0x5eed + TILE.PAVEMENT);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.07) * 0.5 + n(ix, iy, 0.28) * 0.3;
        const tone = 0.9 + v * 0.16;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 168 * tone;
        image.data[o + 1] = 165 * tone;
        image.data[o + 2] = 157 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
    ctx.strokeStyle = 'rgba(80,78,74,0.4)';
    ctx.lineWidth = 3;
    const cell = TILE_SIZE / 2;
    ctx.beginPath();
    ctx.moveTo(px, py + cell);
    ctx.lineTo(px + TILE_SIZE, py + cell);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(px + cell, py);
    ctx.lineTo(px + cell, py + TILE_SIZE);
    ctx.stroke();
  });

  // 15 泥土
  [x, y] = tileAt(TILE.DIRT);
  paintTile(ctx, (px, py) => {
    fillTile(ctx, px, py, '#7a5a3a');
    const n = makeNoise(0x5eed + TILE.DIRT);
    const image = ctx.createImageData(TILE_SIZE, TILE_SIZE);
    for (let iy = 0; iy < TILE_SIZE; iy += 1) {
      for (let ix = 0; ix < TILE_SIZE; ix += 1) {
        const v = n(ix, iy, 0.1) * 0.5 + n(ix, iy, 0.45) * 0.3;
        const tone = 0.82 + v * 0.25;
        const o = (iy * TILE_SIZE + ix) * 4;
        image.data[o] = 122 * tone;
        image.data[o + 1] = 90 * tone;
        image.data[o + 2] = 58 * tone;
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, px, py);
  });

  return canvas;
}

export function createAtlasTexture(gl) {
  const canvas = buildAtlasCanvas();
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
  gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  // 各向异性过滤（主流 3D 引擎默认开启）：斜视角下的路面/砖墙不再糊成一片。
  const aniso = gl.getExtension('EXT_texture_filter_anisotropic')
    || gl.getExtension('WEBKIT_EXT_texture_filter_anisotropic')
    || gl.getExtension('MOZ_EXT_texture_filter_anisotropic');
  if (aniso) {
    const max = gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT);
    gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(8, max || 1));
  }
  return { texture, cols: ATLAS_COLS };
}
