# -*- coding: utf-8 -*-
"""mem_probe.py —— 实测 patchright/Chromium 的内存占用，回答"10 个 IP 会不会打满"

测三种形态：
  A. 1 browser + 1 context（最省）
  B. 1 browser + 5 context（共享浏览器，每个 context 独立代理/cookie）
  C. 5 个独立 browser（每 IP 一个 Chromium，最费）
只统计本脚本新起的 chrome.exe 进程。
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
import psutil

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
ARGS = ["--disable-blink-features=AutomationControlled",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--disable-dev-shm-usage",
        "--disable-features=CalculateNativeWinOcclusion",
        "--blink-settings=imagesEnabled=false"]


def procs() -> dict[int, float]:
    out = {}
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            if (p.info["name"] or "").lower() == "chrome.exe":
                out[p.info["pid"]] = p.info["memory_info"].rss / 1024 / 1024
        except Exception:
            pass
    return out


def snap(tag: str, before: dict[int, float]) -> tuple[float, int]:
    now = procs()
    new = {pid: mb for pid, mb in now.items() if pid not in before}
    tot = sum(new.values())
    print(f"  {tag:34} 进程 {len(new):2} 个   合计 {tot:7.1f} MB   单进程均值 {tot/max(1,len(new)):6.1f} MB")
    return tot, len(new)


def main():
    from patchright.sync_api import sync_playwright
    base = procs()
    print(f"基线已有 chrome 进程: {len(base)} 个，{sum(base.values()):.0f} MB\n")

    with sync_playwright() as p:
        # A. 1 browser + 1 context
        b = p.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
        ctx = b.new_context(ignore_https_errors=True)
        pg = ctx.new_page()
        pg.goto("about:blank")
        time.sleep(2)
        a_tot, a_n = snap("A: 1 browser + 1 context", base)

        # B. 同 browser 再加 4 个 context
        extra = []
        for i in range(4):
            c = b.new_context(ignore_https_errors=True)
            pp = c.new_page()
            pp.goto("about:blank")
            extra.append(c)
        time.sleep(2)
        b_tot, b_n = snap("B: 1 browser + 5 context", base)
        print(f"      → 每多一个 context 约 {(b_tot-a_tot)/4:.0f} MB")

        b.close()
        time.sleep(2)
        after = procs()
        left = {pid: mb for pid, mb in after.items() if pid not in base}
        print(f"  关闭后残留: {len(left)} 个，{sum(left.values()):.0f} MB")

        # C. 5 个独立 browser
        base2 = procs()
        brs = []
        for i in range(5):
            bb = p.chromium.launch(executable_path=CHROME, headless=True, args=ARGS)
            cc = bb.new_context(ignore_https_errors=True)
            pp = cc.new_page()
            pp.goto("about:blank")
            brs.append(bb)
        time.sleep(3)
        c_tot, c_n = snap("C: 5 个独立 browser", base2)
        for bb in brs:
            bb.close()
        time.sleep(2)
        after2 = procs()
        left2 = {pid: mb for pid, mb in after2.items() if pid not in base2}
        print(f"  关闭后残留: {len(left2)} 个，{sum(left2.values()):.0f} MB")

    print("\n=== 换算 ===")
    print(f"  10 个独立 Chromium  ≈ {a_tot*10/1024:.1f} GB")
    print(f"  1 + 10 context       ≈ {(a_tot + (b_tot-a_tot)/4*9)/1024:.1f} GB")
    print(f"  串行（一次一个，用完关）≈ {a_tot/1024:.1f} GB")


if __name__ == "__main__":
    main()
