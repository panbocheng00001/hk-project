// analyze.js —— 求解算法的 Node 版（与 extension/captcha_solver.js 同一套判据）
// ============================================================================
// 为什么两份实现：
//   复刻服务要能独立判定「脚本算出的 dx 对不对」，不能依赖浏览器。
//   判据必须与线上一致，否则复刻题通过 ≠ 真题通过：
//     1) 三档阈值排掉深色网格（否则形状全被网格连成一坨）
//     2) 连通域 → 按填充密度分「实心 / 空心」
//     3) 空心先「填满」，再和实心比 8×8 占用剖面（形状配对）
//     4) 打分 = 尺寸差 + 高度差 + 色相差 + 形状距离
'use strict';
const PNG = require('./png');

const LEVELS = [
  (mx, sat) => (sat > 90 && mx > 90) || (mx > 190 && sat < 80),
  (mx, sat) => (sat > 70 && mx > 70) || (mx > 175 && sat < 85),
  (mx, sat) => (sat > 50 && mx > 55) || (mx > 160 && sat < 95),
];

function hueOf(r, g, b) {
  const R = r / 255, G = g / 255, B = b / 255;
  const mx = Math.max(R, G, B), mn = Math.min(R, G, B), d = mx - mn;
  if (d < 0.02) return -1;
  let h;
  if (mx === R) h = ((G - B) / d) % 6;
  else if (mx === G) h = (B - R) / d + 2;
  else h = (R - G) / d + 4;
  h *= 60;
  return (h + 360) % 360;
}

function hueDist(a, b) {
  if (a < 0 || b < 0) return 0.15;
  let d = Math.abs(a - b);
  if (d > 180) d = 360 - d;
  return d / 180;
}

function buildMask(rgba, w, h, level, hueCenter) {
  const mask = new Uint8Array(w * h);
  const test = LEVELS[level];
  for (let p = 0, i = 0; p < w * h; p++, i += 4) {
    const r = rgba[i], g = rgba[i + 1], b = rgba[i + 2];
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    const sat = mx === 0 ? 0 : ((mx - mn) / mx) * 255;
    if (!test(mx, sat)) continue;
    if (hueCenter != null && hueCenter >= 0) {
      const hh = hueOf(r, g, b);
      if (hh < 0) continue;
      let d = Math.abs(hh - hueCenter);
      if (d > 180) d = 360 - d;
      if (d > 16) continue;
    }
    mask[p] = 1;
  }
  return mask;
}

function profile(mask, w, h, x0, y0, bw, bh, extra) {
  const prof = new Array(64).fill(0);
  for (let gy = 0; gy < 8; gy++) {
    const ya = y0 + Math.floor((bh * gy) / 8);
    const yb = y0 + Math.max(1, Math.floor((bh * (gy + 1)) / 8));
    for (let gx = 0; gx < 8; gx++) {
      const xa = x0 + Math.floor((bw * gx) / 8);
      const xb = x0 + Math.max(1, Math.floor((bw * (gx + 1)) / 8));
      let on = 0, tot = 0;
      for (let y = ya; y < yb && y < h; y++) {
        for (let x = xa; x < xb && x < w; x++) {
          tot++;
          if (mask[y * w + x] || (extra && extra[y * w + x])) on++;
        }
      }
      prof[gy * 8 + gx] = tot ? on / tot : 0;
    }
  }
  return prof;
}

function profDist(a, b) {
  let d = 0;
  for (let i = 0; i < 64; i++) d += Math.abs(a[i] - b[i]);
  return d / 64;
}

function fillHollow(mask, w, h, x0, y0, bw, bh) {
  const out = new Uint8Array(bw * bh);
  const idx = (x, y) => (y - y0) * bw + (x - x0);
  const q = [];
  for (let x = x0; x < x0 + bw; x++) {
    for (const y of [y0, y0 + bh - 1]) {
      if (y >= 0 && y < h && x >= 0 && x < w && !mask[y * w + x] && !out[idx(x, y)]) {
        out[idx(x, y)] = 1; q.push([x, y]);
      }
    }
  }
  for (let y = y0; y < y0 + bh; y++) {
    for (const x of [x0, x0 + bw - 1]) {
      if (y >= 0 && y < h && x >= 0 && x < w && !mask[y * w + x] && !out[idx(x, y)]) {
        out[idx(x, y)] = 1; q.push([x, y]);
      }
    }
  }
  while (q.length) {
    const [x, y] = q.pop();
    for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
      if (nx < x0 || ny < y0 || nx >= x0 + bw || ny >= y0 + bh) continue;
      if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
      if (mask[ny * w + nx] || out[idx(nx, ny)]) continue;
      out[idx(nx, ny)] = 1; q.push([nx, ny]);
    }
  }
  const filled = new Uint8Array(w * h);
  for (let y = y0; y < y0 + bh; y++) {
    for (let x = x0; x < x0 + bw; x++) {
      const inside = !mask[y * w + x] && !out[idx(x, y)];
      if (mask[y * w + x] || inside) filled[y * w + x] = 1;
    }
  }
  return filled;
}

function components(mask, w, h, rgba) {
  const seen = new Uint8Array(w * h);
  const out = [];
  const stack = new Int32Array(w * h);
  for (let p0 = 0; p0 < w * h; p0++) {
    if (!mask[p0] || seen[p0]) continue;
    let sp = 0; stack[sp++] = p0; seen[p0] = 1;
    let minX = w, maxX = -1, minY = h, maxY = -1, count = 0, sr = 0, sg = 0, sb = 0;
    while (sp > 0) {
      const p = stack[--sp];
      const x = p % w, y = (p - x) / w;
      count++;
      if (rgba) { sr += rgba[p * 4]; sg += rgba[p * 4 + 1]; sb += rgba[p * 4 + 2]; }
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (x > 0 && mask[p - 1] && !seen[p - 1]) { seen[p - 1] = 1; stack[sp++] = p - 1; }
      if (x < w - 1 && mask[p + 1] && !seen[p + 1]) { seen[p + 1] = 1; stack[sp++] = p + 1; }
      if (y > 0 && mask[p - w] && !seen[p - w]) { seen[p - w] = 1; stack[sp++] = p - w; }
      if (y < h - 1 && mask[p + w] && !seen[p + w]) { seen[p + w] = 1; stack[sp++] = p + w; }
    }
    const bw = maxX - minX + 1, bh = maxY - minY + 1;
    if (count < 120 || bw < 12 || bh < 12 || count > w * h * 0.25) continue;
    const rgb = count ? [sr / count, sg / count, sb / count] : [0, 0, 0];
    const dens = count / (bw * bh);
    const blob = {
      cx: minX + bw / 2, cy: minY + bh / 2, w: bw, h: bh, count, density: dens,
      rgb, hue: hueOf(rgb[0], rgb[1], rgb[2]),
      x0: minX, y0: minY,
      prof: profile(mask, w, h, minX, minY, bw, bh, null),
    };
    if (dens < 0.45) {
      const filled = fillHollow(mask, w, h, minX, minY, bw, bh);
      blob.profFilled = profile(mask, w, h, minX, minY, bw, bh, filled);
    }
    out.push(blob);
  }
  return out;
}

function pairBest(list, h) {
  const solid = list.filter((b) => b.density >= 0.45);
  const hollow = list.filter((b) => b.density < 0.45);
  if (!solid.length || !hollow.length) {
    const byDen = list.slice().sort((a, b) => b.density - a.density);
    if (!solid.length && byDen[0]) solid.push(byDen[0]);
    if (!hollow.length) hollow.push(byDen[byDen.length - 1]);
  }
  let best = null;
  for (const s of solid) {
    for (const t of hollow) {
      if (s === t) continue;
      const dw = Math.abs(s.w - t.w) / Math.max(1, Math.max(s.w, t.w));
      const dh = Math.abs(s.h - t.h) / Math.max(1, Math.max(s.h, t.h));
      if (dw > 0.4 || dh > 0.4) continue;
      const dy = Math.abs(s.cy - t.cy) / Math.max(1, h);
      const shape = t.profFilled ? profDist(s.prof, t.profFilled) : 0.35;
      const score = dw + dh + dy * 0.6 + hueDist(s.hue, t.hue) * 0.6 + shape * 1.8;
      if (!best || score < best.score) best = { score, s, t };
    }
  }
  return best;
}

/** 核心：给 RGBA 求 dx */
function computeDx(rgba, w, h) {
  let blobs = [];
  let usedLevel = -1;
  for (let li = 0; li < LEVELS.length; li++) {
    const found = components(buildMask(rgba, w, h, li, null), w, h, rgba);
    if (found.length >= 2) { blobs = found; usedLevel = li; break; }
    if (li === LEVELS.length - 1) blobs = found;
  }
  if (blobs.length < 2) return { ok: false, err: '前景块不足', blobs: blobs.length };

  let best = pairBest(blobs, h);
  let refined = null;
  const hues = [...new Set(blobs.filter((b) => b.density >= 0.45 && b.hue >= 0)
    .map((b) => Math.round(b.hue / 12) * 12))].slice(0, 4);
  for (const hc of hues) {
    const sub = components(buildMask(rgba, w, h, Math.max(0, usedLevel), hc), w, h, rgba);
    if (sub.length < 2) continue;
    const cand = pairBest(sub, h);
    if (cand && (!best || cand.score < best.score - 0.02)) { best = cand; refined = hc; }
  }
  if (!best) return { ok: false, err: '没有可配对的实心/空心' };
  return {
    ok: true,
    dx: best.t.cx - best.s.cx,
    current_x: best.s.cx, target_x: best.t.cx,
    pairs: blobs.length, level: usedLevel, refined_hue: refined,
    solid_fill: +best.s.density.toFixed(2),
    hollow_fill: +best.t.density.toFixed(2),
    bmp: [w, h],
  };
}

/** 与线上 /captcha 完全同形的入参：{dataUrl, bmpW, bmpH, cssW, cssH, dpr, inFrame} */
function analyzePayload(payload) {
  const url = String(payload.dataUrl || '');
  const b64 = url.includes(',') ? url.split(',', 2)[1] : url;
  const buf = Buffer.from(b64, 'base64');
  const img = PNG.toRGBA(PNG.decode(buf));
  // 位图尺寸以实际解码为准；payload 里的 bmpW/bmpH 只做一致性校验
  const w = payload.bmpW || Math.sqrt(img.length / 4);
  const h = payload.bmpH || img.length / 4 / w;
  const res = computeDx(img, w, h);
  res.payload_bmp = [payload.bmpW, payload.bmpH];
  res.payload_css = [payload.cssW, payload.cssH];
  res.dpr = payload.dpr;
  res.in_frame = !!payload.inFrame;
  // 位图像素 → 滑块行程（与浏览器侧同一公式）
  if (res.ok && payload.trackW && payload.handleW) {
    const range = Math.max(10, payload.trackW - payload.handleW);
    res.slider_dx = res.dx * (range / w);
    res.range = range;
    res.scale = range / w;
  }
  return res;
}

module.exports = { computeDx, analyzePayload, hueOf, hueDist, LEVELS };
