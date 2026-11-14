# -*- coding: utf-8 -*-
"""_cap_probe.py —— R2（无窗口内核）能扛多少个「排队单元」？

排队单元 = 1 个独立 context（独立 cookie/独立出口/独立 queueId）。
本脚本在**一个** Chromium 进程里逐步加到 N 个 context，每步量：
  · 本脚本新起的 chrome 进程总 RSS
  · 这些进程的 CPU 占用（% of one core，累计平均）
  · 每个 context 的边际成本（MB/个）

只跑 about:blank 与本地 data: 页，不碰目标站。
真实业务页（abs3 SPA + 357KB 混淆挑战 JS）峰值约为本测值的 1.6~2.0 倍，末尾按倍数给区间。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import psutil

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run", "--no-default-browser-check",
    "--disable-gpu", "--disable-dev-shm-usage",
    "--disable-features=CalculateNativeWinOcclusion",
    "--blink-settings=imagesEnabled=false",
    "--disable-extensions", "--disable-background-networking",
    "--js-flags=--max-old-space-size=256",
]
STEPS = [1, 5, 10, 15, 20]


def chrome_rss(exclude: set) -> tuple[float, int, float]:
    """返回 (新增 chrome 进程总 MB, 进程数, 这些进程的 CPU% 合计)"""
    tot, cpu, n = 0.0, 0.0, 0
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() != "chrome.exe":
            continue
        if p.info["pid"] in exclude:
            continue
        try:
            tot += p.memory_info().rss / 1024 / 1024
            cpu += p.cpu_percent(interval=None)
            n += 1
        except Exception:
            pass
    return tot, n, cpu


def main() -> int:
    from patchright.sync_api import sync_playwright

    base = {p.pid for p in psutil.process_iter(["pid", "name"])
            if (p.info["name"] or "").lower() == "chrome.exe"}
    print(f"基线：已有 chrome {len(base)} 个\n")

    rows = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        print(f"内核：{b.version}  headless=True  参数 {len(ARGS)} 项\n")
        ctxs = []
        for target in STEPS:
            while len(ctxs) < target:
                c = b.new_context(ignore_https_errors=True,
                                  viewport={"width": 1280, "height": 800},
                                  locale="zh-CN", timezone_id="Asia/Shanghai")
                pg = c.new_page()
                pg.goto("data:text/html,<h1>q</h1>", wait_until="commit")
                ctxs.append((c, pg))
            time.sleep(2.5)                     # 让渲染进程稳定
            mb, n, cpu = chrome_rss(base)
            per = mb / target
            rows.append({"n": target, "mb": round(mb, 1), "procs": n,
                         "per_ctx_mb": round(per, 1), "cpu_pct": round(cpu, 1)})
            print(f"  N={target:3}  context  总 {mb:8.1f} MB  "
                  f"进程 {n:3}  边际 {per:6.1f} MB/个  CPU {cpu:5.1f}%")
        b.close()

    # 边际成本：用 20 - 10 估最接近真实的斜率
    slope = None
    d = {r["n"]: r for r in rows}
    if 20 in d and 10 in d:
        slope = (d[20]["mb"] - d[10]["mb"]) / 10
        print(f"\n实测斜率（10→20 context）：{slope:.1f} MB / 个")

    print("\n=== 容量换算（按内存）===")
    vm = psutil.virtual_memory()
    print(f"物理内存 {vm.total/1024**3:.1f} GB，当前可用 {vm.available/1024**3:.1f} GB")
    budgets = [8, 12, 16, 20]
    for budget_gb in budgets:
        budget = budget_gb * 1024
        s = slope or 150
        n = int(budget / s) if s else 0
        print(f"  给排队集群 {budget_gb:2} GB  →  约 {n:3} 个 context"
              f"（单浏览器共享形态，含 430MB 底座已在斜率外，实际再减 3 个）")

    print("\n=== 容量换算（按真实业务页，×1.6 / ×2.0）===")
    for f in (1.6, 2.0):
        s = (slope or 150) * f
        for budget_gb in (12, 16, 20):
            print(f"  峰值系数 {f}  {budget_gb:2} GB  →  约 {int(budget_gb*1024/s):3} 个")

    out = Path(__file__).resolve().parent / "runs" / "cap_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "rows": rows, "slope_mb": slope,
                               "cpu_count": psutil.cpu_count(),
                               "mem_total_gb": round(vm.total / 1024**3, 1),
                               "mem_avail_gb": round(vm.available / 1024**3, 1)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out.relative_to(Path(__file__).resolve().parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
