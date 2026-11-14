# -*- coding: utf-8 -*-
"""_mem6.py —— 6 个出口常驻到底占多少内存（实测，不发网络请求）

分两段量：
  A 纯 HTTP 段（sniper/align_client 的常驻形态）：6 个会话同时活着
  B 铸票段（mint_align 的瞬时形态）：串行 vs 并行 6 个 Chromium
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

import psutil

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
HERE = psutil.Process(os.getpid())


def mb() -> float:
    return HERE.memory_info().rss / 1024 / 1024


def chrome_mb() -> tuple[float, int]:
    tot, n = 0.0, 0
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() == "chrome.exe":
            try:
                tot += p.memory_info().rss / 1024 / 1024
                n += 1
            except Exception:
                pass
    return tot, n


print("=== A) 纯 HTTP 段：6 个出口常驻 ===")
print(f"  空 Python 进程          {mb():7.1f} MB")

from curl_cffi import requests as cr  # noqa: E402
print(f"  导入 curl_cffi 后        {mb():7.1f} MB")

from sniper import Sniper, Log  # noqa: E402

log = Log("mem6", file=False)
sessions = []
for i in range(6):
    sp = Sniper(proxy="", log=log, min_interval=0.01)
    sp.log = lambda *a, **k: None
    sessions.append(sp)
    print(f"  第 {i+1} 个 Sniper 会话后   {mb():7.1f} MB")

print(f"\n  6 个会话合计增量        {mb() - 0:7.1f} MB（含解释器）")

# 压测：每个会话塞 20 条 cookie（票的真实形态）
for sp in sessions:
    for k in range(20):
        try:
            sp.s.cookies.set(f"c{k}", "x" * 120, domain="abs3.td.gov.hk", path="/")
        except Exception:
            pass
print(f"  塞入 6×20 条 cookie 后   {mb():7.1f} MB")

print("\n=== B) 铸票段：Chromium 瞬时占用 ===")
base_ch, n_ch = chrome_mb()
print(f"  当前机器 chrome 总量     {base_ch:7.1f} MB / {n_ch} 进程（含你自己的浏览器）")

from patchright.sync_api import sync_playwright  # noqa: E402

CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")
ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-dev-shm-usage", "--disable-gpu",
        "--blink-settings=imagesEnabled=false", "--js-flags=--max-old-space-size=256"]

with sync_playwright() as pw:
    # 串行：一次只开一个（推荐的 6 IP 打法）
    for i in range(2):
        b = pw.chromium.launch(executable_path=CHROME, headless=False, args=ARGS,
                               ignore_default_args=["--enable-automation"])
        ctx = b.new_context(viewport={"width": 1366, "height": 768})
        pg = ctx.new_page()
        pg.goto("data:text/html,<h1>m</h1>", wait_until="commit")
        time.sleep(1.5)
        tot, n = chrome_mb()
        print(f"  串行第 {i+1} 个 Chromium    新增 {tot-base_ch:7.1f} MB / {n-n_ch} 进程"
              f"   Python {mb():6.1f} MB")
        b.close()
        time.sleep(1.0)
    tot, n = chrome_mb()
    print(f"  关闭后残留              新增 {tot-base_ch:7.1f} MB / {n-n_ch} 进程")

    # 并行 6 个（不推荐，但量给你看）
    brs = []
    for i in range(6):
        bb = pw.chromium.launch(executable_path=CHROME, headless=False, args=ARGS,
                                ignore_default_args=["--enable-automation"])
        cc = bb.new_context(viewport={"width": 1366, "height": 768})
        cc.new_page().goto("data:text/html,<h1>m</h1>", wait_until="commit")
        brs.append(bb)
    time.sleep(2.5)
    tot, n = chrome_mb()
    print(f"  并行 6 个 Chromium      新增 {tot-base_ch:7.1f} MB / {n-n_ch} 进程")
    for bb in brs:
        bb.close()
    time.sleep(1.5)
    tot, n = chrome_mb()
    print(f"  全部关闭后残留          新增 {tot-base_ch:7.1f} MB / {n-n_ch} 进程")

vm = psutil.virtual_memory()
print(f"\n机器共 {vm.total/1024**3:.1f} GB，当前可用 {vm.available/1024**3:.1f} GB")
