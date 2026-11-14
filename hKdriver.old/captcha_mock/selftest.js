// selftest.js —— 复刻服务自检：生成 N 道题 → 算法求解 → 与真值比对
// ============================================================================
// 判定标准：|算出 - 真值| ≤ TOL（默认 3 位图像素）。
// 这个数字直接决定「复刻题能不能当回归基准」—— 算法在自己生成的题上都算不准，
// 就没法拿它去证明线上能过。
'use strict';
const challenge = require('./challenge');
const analyze = require('./analyze');

const TOL = Number(process.env.TOL || 3);
const N = Number(process.env.N || 30);

let ok = 0, worst = 0, worstSeed = null;
const fails = [];

for (let i = 0; i < N; i++) {
  const seed = 1000 + i * 7919;
  const c = challenge.make(seed, 400, 330);
  const r = analyze.analyzePayload({
    dataUrl: c.dataUrl, bmpW: c.w, bmpH: c.h,
    cssW: 400, cssH: 330, dpr: 1, inFrame: false,
    trackW: 400, handleW: 34,
  });
  if (!r.ok) { fails.push(`seed=${seed} 求解失败: ${r.err}`); continue; }
  const err = Math.abs(r.dx - c.truth.dx);
  if (err > worst) { worst = err; worstSeed = seed; }
  if (err <= TOL) ok++; else fails.push(`seed=${seed} dx=${r.dx.toFixed(1)} 真值=${c.truth.dx} 差=${err.toFixed(1)}`);
  if (i === 0) {
    // 顺带验证滑块换算
    const range = 400 - 34;
    const want = (c.truth.dx / 400) * range;
    console.log(`样例 seed=${seed}: dx=${r.dx.toFixed(1)} 真值=${c.truth.dx} slider_dx=${r.slider_dx.toFixed(1)} 期望=${want.toFixed(1)}`);
  }
}

console.log('='.repeat(64));
console.log(`通过 ${ok}/${N}（容差 ±${TOL}px），最大误差 ${worst.toFixed(2)}px${worstSeed ? ` (seed=${worstSeed})` : ''}`);
if (fails.length) {
  console.log('失败：');
  fails.slice(0, 8).forEach((f) => console.log('  ✗', f));
}
console.log('='.repeat(64));
process.exit(ok === N ? 0 : 1);
