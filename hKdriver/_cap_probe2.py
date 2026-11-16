# -*- coding: utf-8 -*-
"""_cap_probe2.py —— 真实业务页的峰值系数（比 about:blank 贵多少倍）

为什么需要：about:blank 量出来的是"空壳"成本；
abs3 实际页面是 SPA 壳 + 357KB 混淆挑战 JS + canvas/WebGL 指纹计算 + 轮询定时器。
本脚本用本地页面**复刻这三件事**（不发一个网络请求），量出真实倍数。

复刻内容：
  1) 解析并执行 ~350KB 的混淆 JS（同量级解析/编译成本）
  2) canvas 指纹：1920x1080 绘制 + toDataURL（WAF 就是这么采的）
  3) WebGL 参数读取（SwiftShader 软渲染，正是 headless 的真实路径）
  4) 3 秒一次的 setInterval 轮询（排队 status 的稳态成本）
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
CHROME = str(HERE / "cft" / "chrome-win64" / "chrome.exe")
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-gpu", "--disable-dev-shm-usage",
        "--disable-features=CalculateNativeWinOcclusion",
        "--blink-settings=imagesEnabled=false", "--disable-extensions",
        "--js-flags=--max-old-space-size=256"]

# 生成「类挑战页」：本地文件，无外链
PAGE = HERE / "runs" / "_cap_heavy.html"


def build_page() -> Path:
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    # 350KB 混淆风格 JS：大量字符串数组 + 循环索引，逼 V8 做同样的解析/编译工作
    blob = []
    blob.append("var A=[];")
    for i in range(3000):
        blob.append(f'A.push("{i:04d}"+String.fromCharCode({65 + i % 26}));')
    blob.append("""
function spin(){
  var s=0; for (var i=0;i<A.length;i++){ s+=A[i].charCodeAt(A[i].length-1); }
  return s;
}
var __sum = spin();

function fpCanvas(){
  var c=document.createElement('canvas'); c.width=1920; c.height=1080;
  var x=c.getContext('2d');
  x.fillStyle='#f60'; x.fillRect(0,0,1920,1080);
  x.font='24px Arial'; x.fillStyle='#069';
  for(var i=0;i<40;i++) x.fillText('ABS-DI-'+i, 20, 40+i*24);
  return c.toDataURL().length;
}
var __c = fpCanvas();

function fpGL(){
  var c=document.createElement('canvas'); c.width=512; c.height=512;
  var g=c.getContext('webgl')||c.getContext('experimental-webgl');
  if(!g) return 0;
  var d=g.getExtension('WEBGL_debug_renderer_info');
  g.getParameter(d?d.UNMASKED_RENDERER_WEBGL:g.RENDERER);
  return g.getParameter(g.MAX_TEXTURE_SIZE)||0;
}
var __g = fpGL();

setInterval(function(){ spin(); }, 3000);
window.__ready = 1;
""")
    PAGE.write_text("<!doctype html><meta charset=utf-8><script>%s</script>"
                    % "\n".join(blob), encoding="utf-8")
    return PAGE


def rss(exclude: set) -> tuple[float, int]:
    tot, n = 0.0, 0
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() != "chrome.exe" or p.info["pid"] in exclude:
            continue
        try:
            tot += p.memory_info().rss / 1024 / 1024
            n += 1
        except Exception:
            pass
    return tot, n


def main() -> int:
    from patchright.sync_api import sync_playwright
    page_file = build_page()
    print(f"复刻页：{page_file.name}  {page_file.stat().st_size/1024:.0f} KB\n")

    base = {p.pid for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() == "chrome.exe"}

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        urls = {"blank": "data:text/html,<h1>x</h1>", "heavy": page_file.as_uri()}

        res = {}
        for kind in ("blank", "heavy"):
            ctxs = []
            for i in range(5):
                c = b.new_context(ignore_https_errors=True,
                                  viewport={"width": 1280, "height": 800})
                pg = c.new_page()
                pg.goto(urls[kind], wait_until="load", timeout=30000)
                pg.wait_for_timeout(1200)
                ctxs.append((c, pg))
            time.sleep(3)
            mb_b, n_b = rss(base)
            # 采样 6 秒内的 CPU（含 canvas/WebGL 指纹与 setInterval 活动）
            t0 = time.time()
            snap0 = {p.pid: (p.cpu_times().user + p.cpu_times().system)
                     for p in psutil.process_iter(["pid", "name"])
                     if (p.info["name"] or "").lower() == "chrome.exe"
                     and p.info["pid"] not in base}
            time.sleep(6)
            snap1 = {}
            for p in psutil.process_iter(["pid", "name"]):
                if (p.info["name"] or "").lower() == "chrome.exe" and p.info["pid"] not in base:
                    try:
                        snap1[p.pid] = p.cpu_times().user + p.cpu_times().system
                    except Exception:
                        pass
            cpu = (sum(snap1.values()) - sum(snap0.values())) / (time.time() - t0) * 100
            res[kind] = {"mb": round(mb_b, 1), "procs": n_b,
                         "per_ctx": round(mb_b / 5, 1), "cpu_pct": round(cpu, 1),
                         "ready": [pg.evaluate("window.__ready||0") for _, pg in ctxs][:5]}
            print(f"  {kind:6} 5 context → {mb_b:7.1f} MB  进程 {n_b:3}  "
                  f"每 context {mb_b/5:6.1f} MB  CPU {cpu:5.1f}%")
            for _, pg in ctxs:
                pg.close()
            for c, _ in ctxs:
                c.close()
        b.close()

    factor = res["heavy"]["per_ctx"] / res["blank"]["per_ctx"]
    print(f"\n★ 真实页/空壳 峰值系数 = {factor:.2f}×")
    print(f"  空壳 {res['blank']['per_ctx']:.0f} MB/context"
          f"  →  业务页 {res['heavy']['per_ctx']:.0f} MB/context")

    vm = psutil.virtual_memory()
    print(f"\n=== 按真实业务页重算容量（物理内存 {vm.total/1024**3:.1f} GB，"
          f"当前可用 {vm.available/1024**3:.1f} GB）===")
    per = res["heavy"]["per_ctx"]
    for budget in (8, 12, 16, 20):
        print(f"  给排队集群 {budget:2} GB  →  约 {int(budget*1024/per):3} 个 context")

    out = HERE / "runs" / "cap_probe2.json"
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "res": res, "factor": round(factor, 2),
                               "mem_total_gb": round(vm.total / 1024**3, 1),
                               "mem_avail_gb": round(vm.available / 1024**3, 1)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
