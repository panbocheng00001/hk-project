// challenge.js —— 生成与线上同构的滑块题（位图 + 真值）
// ============================================================================
// 依据实测样本（runs/captcha_specimens/，22 个）：
//   位图 400×330（DPR=2 时 800×660）；深色网格底（#1b2430，网格线 rgba(120,140,170,.18)）；
//   一组干扰轮廓；一对「同形状同色」的实心 + 空心；真值 dx 通常 150–260 位图像素。
// 生成时把真值写进 challenge.truth.dx，供 /grade 判定。
'use strict';
const PNG = require('./png');

let seed = 1;
function rnd() {                       // 固定种子的 LCG，便于复现同一道题
  seed = (seed * 1664525 + 1013904223) >>> 0;
  return seed / 4294967296;
}

function pick(seedIn) { if (seedIn != null) seed = seedIn >>> 0; }

function draw(w, h, opts) {
  const o = Object.assign({
    sides: 3 + Math.floor(rnd() * 4),
    R: 22 + rnd() * 16,
    cy: 40 + rnd() * (h - 80),
    curX: 40 + rnd() * 90,
    off: 60 + rnd() * (w - 200),
    hue: Math.floor(rnd() * 360),
    decoys: 3,
    lineWidth: 3,
  }, opts || {});
  const tgtX = Math.min(w - 40, o.curX + o.off);
  const rgba = Buffer.alloc(w * h * 4);

  // 背景 + 网格
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      let r = 0x1b, g = 0x24, b = 0x30;
      if (x % 20 === 0 || y % 20 === 0) {        // 网格线按 18% 叠加
        r = Math.round(r * 0.82 + 120 * 0.18);
        g = Math.round(g * 0.82 + 140 * 0.18);
        b = Math.round(b * 0.82 + 170 * 0.18);
      }
      rgba[i] = r; rgba[i + 1] = g; rgba[i + 2] = b; rgba[i + 3] = 255;
    }
  }
  const setPx = (x, y, r, g, b, a) => {
    x = Math.round(x); y = Math.round(y);
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    const i = (y * w + x) * 4;
    const al = a == null ? 1 : a;
    rgba[i] = Math.round(rgba[i] * (1 - al) + r * al);
    rgba[i + 1] = Math.round(rgba[i + 1] * (1 - al) + g * al);
    rgba[i + 2] = Math.round(rgba[i + 2] * (1 - al) + b * al);
    rgba[i + 3] = 255;
  };
  const hsl2rgb = (hh, s, l) => {
    const c = (1 - Math.abs(2 * l - 1)) * s, hp = hh / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    let r = 0, g = 0, b = 0;
    if (hp < 1) [r, g, b] = [c, x, 0]; else if (hp < 2) [r, g, b] = [x, c, 0];
    else if (hp < 3) [r, g, b] = [0, c, x]; else if (hp < 4) [r, g, b] = [0, x, c];
    else if (hp < 5) [r, g, b] = [x, 0, c]; else [r, g, b] = [c, 0, x];
    const m = l - c / 2;
    return [(r + m) * 255, (g + m) * 255, (b + m) * 255];
  };

  const polyPts = (cx, cy, R, n, rot) => {
    const pts = [];
    for (let i = 0; i < n; i++) {
      const a = rot + (i / n) * Math.PI * 2;
      pts.push([cx + Math.cos(a) * R, cy + Math.sin(a) * R]);
    }
    return pts;
  };
  const strokePoly = (pts, rgb, lw) => {
    for (let i = 0; i < pts.length; i++) {
      const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
      const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1)) * 2;
      for (let s = 0; s <= steps; s++) {
        const t = s / steps;
        const px = x1 + (x2 - x1) * t, py = y1 + (y2 - y1) * t;
        for (let dy = -lw / 2; dy <= lw / 2; dy += 0.5) {
          for (let dx = -lw / 2; dx <= lw / 2; dx += 0.5) {
            if (dx * dx + dy * dy <= (lw / 2) * (lw / 2) + 0.3) setPx(px + dx, py + dy, ...rgb);
          }
        }
      }
    }
  };
  const fillPoly = (pts, rgb) => {
    let minY = Infinity, maxY = -Infinity;
    for (const [, y] of pts) { minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
    for (let y = Math.floor(minY); y <= Math.ceil(maxY); y++) {
      const xs = [];
      for (let i = 0; i < pts.length; i++) {
        const [x1, y1] = pts[i], [x2, y2] = pts[(i + 1) % pts.length];
        if ((y1 <= y && y2 > y) || (y2 <= y && y1 > y)) {
          xs.push(x1 + ((y - y1) / (y2 - y1)) * (x2 - x1));
        }
      }
      xs.sort((a, b) => a - b);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        for (let x = Math.ceil(xs[k]); x <= Math.floor(xs[k + 1]); x++) setPx(x, y, ...rgb);
      }
    }
  };

  // 干扰轮廓（离答案远一点，真实题面也不会压在答案上）
  for (let d = 0; d < o.decoys; d++) {
    const rgb = hsl2rgb(Math.floor(rnd() * 360), 0.6, 0.45);
    for (let tryN = 0; tryN < 24; tryN++) {
      const r2 = 14 + rnd() * 10, x2 = rnd() * w, y2 = rnd() * h;
      const near = Math.min(Math.hypot(x2 - o.curX, y2 - o.cy), Math.hypot(x2 - tgtX, y2 - o.cy));
      if (near < o.R + r2 + 30) continue;
      strokePoly(polyPts(x2, y2, r2, 3 + Math.floor(rnd() * 4), rnd() * 3), rgb, 2);
      break;
    }
  }

  const rgbMain = hsl2rgb(o.hue, 0.85, 0.6);
  const rot = rnd() * Math.PI;
  strokePoly(polyPts(tgtX, o.cy, o.R, o.sides, rot), rgbMain, o.lineWidth);  // 空心（目标位）
  fillPoly(polyPts(o.curX, o.cy, o.R, o.sides, rot), rgbMain);               // 实心（当前位置）

  return {
    rgba, w, h,
    truth: {
      dx: +(tgtX - o.curX).toFixed(2),
      curX: +o.curX.toFixed(2), tgtX: +tgtX.toFixed(2), cy: +o.cy.toFixed(2),
      sides: o.sides, R: +o.R.toFixed(2), hue: o.hue,
    },
  };
}

function make(seedIn, w, h, opts) {
  pick(seedIn);
  const c = draw(w || 400, h || 330, opts);
  c.png = PNG.encodeRGBA(c.rgba, c.w, c.h);
  c.dataUrl = 'data:image/png;base64,' + c.png.toString('base64');
  return c;
}

module.exports = { make, draw };
