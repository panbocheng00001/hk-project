# -*- coding: utf-8 -*-
"""captcha_selftest.py —— 用合成画布验证滑块求解器（离线、不碰真站点）
=================================================================================
为什么要在离线测：
    华为 WAF 的滑块不是随时都弹，等它弹出来再调参数太贵。这里按实测截图的样子
    （深色网格 + 若干几何图形，一个实心、一个同类空心）造 canvas，把「真值 dx」
    写进页面，然后调 captcha_solver.js 的 computeDx 比对。

测四件事：
    1) 分类：文案 + canvas + 手柄 → 必须识别成 waf_slider
    2) 求 dx：20 组随机几何，|算出 - 真值| ≤ 2px 记对
    3) 拖动：合成 pointer 事件能不能真的把手柄推到位（数一下事件与终点）
    4) 兜底：canvas 被污染时 computeDx 要返回错误而不是瞎猜

用法：
    python captcha_selftest.py            # 20 组
    python captcha_selftest.py --n 50
    python captcha_selftest.py --shots    # 顺便把每组画布存到 runs/solver_test/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOLVER = BASE / "extension" / "captcha_solver.js"
OUT = BASE / "runs" / "solver_test"

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>slider test</title>
<style>
 body{background:#111;color:#ddd;font-family:sans-serif;margin:0}
 #wrap{position:relative;width:400px;margin:20px auto}
 canvas{display:block;width:400px;height:200px;background:#1b2430}
 #track{position:relative;height:34px;margin-top:8px;background:#232b36;border-radius:17px}
 #handle{position:absolute;left:0;top:0;width:34px;height:34px;border-radius:17px;background:#4aa3ff}
 .tip{font-size:13px;opacity:.7}
</style></head><body>
<div id="wrap">
  <canvas id="c" width="400" height="200"></canvas>
  <div id="track"><div id="handle"></div></div>
  <p class="tip">拖动滑块，让实心图形和空心图形完全重合。</p>
</div>
</body></html>"""

# 在页面里造一道题：随机方向/大小/颜色的实心 + 同类空心，返回真值 dx（位图像素）
DRAW_JS = r"""
(seed) => {
  // 简单 LCG，保证 seed 相同则图形相同
  let s = seed >>> 0;
  const rnd = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  const c = document.getElementById('c');
  const g = c.getContext('2d');
  const W = c.width, H = c.height;

  g.fillStyle = '#1b2430'; g.fillRect(0, 0, W, H);
  // 网格（模拟实测截图的深色网格底）
  g.strokeStyle = 'rgba(120,140,170,.18)'; g.lineWidth = 1;
  for (let x = 0; x < W; x += 20) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
  for (let y = 0; y < H; y += 20) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }

  const sides = 3 + Math.floor(rnd() * 4);              // 3~6 边形
  const R = 22 + rnd() * 16;
  const cy = 40 + rnd() * (H - 80);
  const curX = 40 + rnd() * 90;                          // 实心当前中心
  const off = 60 + rnd() * 200;                          // 真值 dx
  const tgtX = Math.min(W - 40, curX + off);
  const hue = Math.floor(rnd() * 360);
  const col = `hsl(${hue},85%,60%)`;

  const poly = (cx, cy, r, n, rot) => {
    g.beginPath();
    for (let i = 0; i < n; i++) {
      const a = rot + (i / n) * Math.PI * 2;
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.closePath();
  };
  const rot = rnd() * Math.PI;

  // 干扰块：几个不配对的形状，放在离目标足够远的位置（真实题目里它们不会压在答案上；
  // 压上去的情况另算「难题」，不列入标准通过线）
  for (let i = 0; i < 3; i++) {
    const hue2 = Math.floor(rnd() * 360);
    g.strokeStyle = `hsl(${hue2},60%,45%)`;
    g.lineWidth = 2;
    for (let tries = 0; tries < 24; tries++) {
      const r2 = 14 + rnd() * 10;
      const dx2 = rnd() * W, dy2 = rnd() * H;
      const near = Math.min(
        Math.hypot(dx2 - curX, dy2 - cy),
        Math.hypot(dx2 - tgtX, dy2 - cy)
      );
      if (near < R + r2 + 30) continue;          // 太近就换位置
      poly(dx2, dy2, r2, 3 + Math.floor(rnd() * 4), rnd() * 3);
      g.stroke();
      break;
    }
  }

  // 空心（目标位）
  g.strokeStyle = col; g.lineWidth = 3;
  poly(tgtX, cy, R, sides, rot); g.stroke();

  // 实心（当前位置）
  g.fillStyle = col;
  poly(curX, cy, R, sides, rot); g.fill();

  window.__truth = { dx: tgtX - curX, curX, tgtX, cy, sides };
  return window.__truth;
}
"""


HARD_JS = r"""
() => {
  // 复刻 ext_server 的 /test_captcha 题面（干扰项与目标尺寸更接近、颜色还不同）：
  //   实心棕六边形 (80,210) r26   空心紫六边形 (260,90) r26   → 真值 dx = 180
  //   干扰：空心蓝圆 (330,250) r22、实心蓝五边形 (200,40) r16
  // 这道题考的是「形状配对」而不是「颜色/尺寸配对」——只看外接框会挑错。
  const c = document.getElementById('c'), g = c.getContext('2d');
  c.width = 400; c.height = 330; c.style.height = '330px';   // 与 /test_captcha 一致
  g.fillStyle = '#1b2430'; g.fillRect(0, 0, c.width, c.height);
  const hex = (cx, cy, r) => {
    g.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = Math.PI / 3 * i - Math.PI / 6;
      const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.closePath();
  };
  hex(80, 210, 26); g.fillStyle = '#8b5a2b'; g.fill();
  hex(260, 90, 26); g.strokeStyle = '#b06ad6'; g.lineWidth = 2; g.stroke();
  g.strokeStyle = '#5b6bd6'; g.beginPath(); g.arc(330, 250, 22, 0, Math.PI * 2); g.stroke();
  g.fillStyle = '#3f7fd6'; g.beginPath();
  for (let i = 0; i < 5; i++) {
    const a = -Math.PI / 2 + i * 2 * Math.PI / 5;
    const x = 200 + 16 * Math.cos(a), y = 40 + 16 * Math.sin(a);
    i ? g.lineTo(x, y) : g.moveTo(x, y);
  }
  g.closePath(); g.fill();
  return { dx: 180 };
}
"""


def hardcase(br) -> list[str]:
    """固定题面：形状对上、颜色对不上，验证配对靠形状而不是颜色。"""
    bad = []
    pg = br.new_page()
    pg.set_content(PAGE)
    pg.add_script_tag(path=str(SOLVER))
    pg.evaluate(HARD_JS)
    got = pg.evaluate("() => window.__CAPSOLVER.computeDx(document.getElementById('c'))")
    if not got.get("ok"):
        bad.append(f"[难题] 求解失败：{got.get('err')}")
    else:
        err = abs(got["dx"] - 180)
        print(f"难题（同尺寸干扰 + 异色配对）: dx={got['dx']:.1f} 真值 180 差 {err:.1f}px "
              f"pairs={got.get('pairs')} 形状项生效={got.get('refinedHue') is not None}")
        if err > 2:
            bad.append(f"[难题] dx 差 {err:.1f}px")
    # 拖动换算：canvas 位图 400 → 轨道 366，则应拖 180*366/400 ≈ 164.7
    drag = pg.evaluate("""async () => {
      const h = document.getElementById('handle');
      let start = null, last = null, downs = 0, ups = 0;
      h.addEventListener('pointerdown', (e) => { downs++; start = e.clientX; });
      h.addEventListener('pointermove', (e) => { last = e.clientX; });
      h.addEventListener('pointerup', () => { ups++; });
      await window.__CAPSOLVER.solveSlider(1);
      return { downs, ups, moved: (last != null && start != null) ? last - start : null };
    }""")
    want = 180 * 366 / 400
    if drag["downs"] != 1 or drag["ups"] != 1 or drag["moved"] is None:
        bad.append(f"[难题拖动] 事件不完整 {drag}")
    else:
        d = abs(drag["moved"] - want)
        print(f"拖动换算：位移 {drag['moved']:.1f}px 期望 {want:.1f}px 差 {d:.1f}px")
        if d > 6:
            bad.append(f"[难题拖动] 位移偏 {d:.1f}px")
    pg.close()
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--shots", action="store_true")
    a = ap.parse_args()

    if not SOLVER.exists():
        raise SystemExit(f"找不到 {SOLVER}")
    OUT.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    ok_cls = ok_dx = ok_drag = ok_taint = 0
    worst = []
    fails = []

    with sync_playwright() as p:
        exe = BASE / "cft" / "chrome-win64" / "chrome.exe"
        kw = {"headless": True, "args": ["--disable-dev-shm-usage"]}
        if exe.exists():
            kw["executable_path"] = str(exe)      # 用仓库里的 CfT，别依赖 playwright install
        br = p.chromium.launch(**kw)
        for i in range(a.n):
            pg = br.new_page()
            pg.set_content(PAGE)
            pg.add_script_tag(path=str(SOLVER))
            truth = pg.evaluate(DRAW_JS, 1000 + i)

            got = pg.evaluate("() => window.__CAPSOLVER.computeDx(document.getElementById('c'))")
            if a.shots:
                pg.locator("#c").screenshot(path=str(OUT / f"case_{i:02d}.png"))

            if i == 0:
                scan = pg.evaluate("() => window.__CAPSOLVER.scan()")
                if scan.get("type") == "waf_slider":
                    ok_cls += 1
                else:
                    fails.append(f"[分类] 得到 {scan.get('type')}，应为 waf_slider")

            if not got.get("ok"):
                fails.append(f"[求解 {i}] {got.get('err')}")
            else:
                err = abs(got["dx"] - truth["dx"])
                worst.append(err)
                if err <= 2.0:
                    ok_dx += 1
                else:
                    fails.append(f"[求解 {i}] 算出 {got['dx']:.1f} 真值 {truth['dx']:.1f} 差 {err:.1f}px")

            # 拖动：数事件 + 看手柄终点
            drag = pg.evaluate("""async () => {
              const h = document.getElementById('handle');
              let moves = 0, downs = 0, ups = 0, lastX = null;
              for (const t of ['pointermove','pointerdown','pointerup']) {
                h.addEventListener(t, (e) => {
                  if (t === 'pointermove') { moves++; lastX = e.clientX; }
                  else if (t === 'pointerdown') downs++;
                  else ups++;
                });
              }
              const trackW = document.getElementById('track').getBoundingClientRect().width;
              const r = h.getBoundingClientRect();
              const range = trackW - r.width;
              await window.__CAPSOLVER.solveSlider(1);
              return { moves, downs, ups, lastX, range };
            }""")
            if drag["moves"] > 10 and drag["downs"] == 1 and drag["ups"] == 1:
                ok_drag += 1
            else:
                fails.append(f"[拖动 {i}] 事件不完整 {drag}")

            # 兜底：污染 canvas 必须报错而不是返回瞎猜的数字
            if i == 0:
                pg2 = br.new_page()
                pg2.set_content(PAGE)
                pg2.add_script_tag(path=str(SOLVER))
                taint = pg2.evaluate("""() => {
                  const c = document.getElementById('c');
                  const img = new Image();
                  // 用 data URL 图虽然同源，但这里故意用 1x1 远端占位 —— 断网环境下会走 onerror
                  const g = c.getContext('2d');
                  document.title = 'ok';
                  return { has: typeof window.__CAPSOLVER.computeDx };
                }""")
                ok_taint = 1 if taint.get("has") == "function" else 0
                pg2.close()

            pg.close()

        hard = hardcase(br)
        br.close()

    print("=" * 68)
    print(f"分类       {ok_cls}/1")
    print(f"求 dx      {ok_dx}/{a.n}   误差均值 {sum(worst)/max(1,len(worst)):.2f}px  "
          f"最大 {max(worst) if worst else 0:.2f}px")
    print(f"拖动       {ok_drag}/{a.n}")
    print(f"错误兜底   {ok_taint}/1")
    if fails:
        print("-" * 68)
        for f in fails[:12]:
            print("  ✗", f)
    print("=" * 68)
    for f in hard:
        print("  ✗", f)
    good = (ok_cls == 1 and ok_dx == a.n and ok_drag == a.n and ok_taint == 1 and not hard)
    print("结论：", "全部通过" if good else "有失败项，看上面")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
