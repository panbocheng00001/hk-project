#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pool_multi.py —— 用同一个 CfT 开多个「带代理」的 context，各自进 abs4 排队池。

背景（16:35 实测）：
  · curl 走代理/直连 → 全 412（WAF 挑战页）
  · 浏览器（CfT 9223）→ 200，拿到 HIK0kat3rt9xO
  → 所以排队只能走浏览器。这里给每个代理配一个独立 context，
    互不干扰地把「同意 → 预约 → 不用智方便 → 内地 → 排队页」点完。

用法：
    python pool_multi.py 3          # 用 proxies.json 的前 3 个代理各开一条
    python pool_multi.py 3 --direct # 不开代理，用本机直连（对照）

成功判据：URL 变成 abs4.td.gov.hk（排队页）。成功后的 context 会一直留着，
脚本退出也不关浏览器（connect 模式，浏览器是外部的）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
PROXIES_FILE = BASE / "runs" / "fleet" / "proxies.json"
START = "https://abs3.td.gov.hk/?lang=sc"


def log(tag: str, msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [{tag}] {msg}", flush=True)


def enter(ctx, tag: str) -> bool:
    try:
        pg = ctx.new_page()
        r = pg.goto(START, wait_until="domcontentloaded", timeout=50000)
        log(tag, f"goto status={r.status if r else '?'}")
        pg.wait_for_selector('input[name="conditionsCheck"]', timeout=60000)
        log(tag, "入口页就绪（勾选框可见）")
        pg.check('input[name="conditionsCheck"]', force=True)
        time.sleep(1.2)

        clicked = False
        for b in pg.query_selector_all("button"):
            if (b.inner_text() or "").strip() in ("预约", "預約", "MAKE AN APPOINTMENT"):
                b.click(force=True)
                clicked = True
                log(tag, "点「预约」")
                break
        if not clicked:
            log(tag, "没找到「预约」按钮")

        pg.wait_for_selector('input[type="radio"][value="withOutIas"]', timeout=45000)
        pg.check('input[type="radio"][value="withOutIas"]', force=True)
        time.sleep(0.6)
        for b in pg.query_selector_all("button"):
            if (b.inner_text() or "").strip() in ("继续", "繼續", "CONTINUE", "Continue",
                                                 "NEXT", "Next"):
                b.click(force=True)
                log(tag, "选「不使用智方便」→ 继续")
                break

        t0 = time.time()
        picked = False
        while time.time() - t0 < 100:
            u = pg.url
            if "abs4.td.gov.hk" in u:
                log(tag, f"★ 进池成功 {u[:96]}")
                return True
            if "SelectCountry" in u and not picked:
                picked = True
                for r2 in pg.query_selector_all('input[type="radio"]'):
                    try:
                        lab = (r2.evaluate(
                            "e => (e.closest('label')?.innerText || '')") or "")
                    except Exception:
                        lab = ""
                    if "内地" in lab or "Mainland" in lab:
                        r2.check(force=True)
                        log(tag, "选「中国内地」")
                        break
                time.sleep(0.8)
                for b in pg.query_selector_all("button"):
                    if (b.inner_text() or "").strip() in ("继续", "繼續",
                                                          "CONTINUE", "Continue"):
                        b.click(force=True)
                        log(tag, "内地 → 继续")
                        break
            time.sleep(1.5)
        log(tag, f"超时，当前 {pg.url[:96]}")
        return False
    except Exception as e:
        log(tag, f"失败 {type(e).__name__}: {str(e)[:110]}")
        return False


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    direct = "--direct" in sys.argv
    proxies = json.loads(PROXIES_FILE.read_text(encoding="utf-8")) if PROXIES_FILE.exists() else []
    if direct:
        proxies = [""] * n
    if not proxies:
        print("没有可用代理清单（runs/fleet/proxies.json）")
        return 20
    print(f"准备开 {min(n, len(proxies))} 条：{proxies[:n]}")

    ok = 0
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        for i, proxy in enumerate(proxies[:n]):
            tag = f"M{i + 1}"
            kw = dict(locale="zh-CN", timezone_id="Asia/Shanghai", no_viewport=True)
            if proxy:
                kw["proxy"] = {"server": proxy}
            try:
                ctx = b.new_context(**kw)
            except Exception as e:
                log(tag, f"new_context 失败 {type(e).__name__}: {str(e)[:90]}")
                continue
            if enter(ctx, tag):
                ok += 1
            time.sleep(1.0)
        print(f"\n结果：{ok}/{min(n, len(proxies))} 条进池")
        # 不调 b.close()：浏览器是外部的，context 留着继续排队
    return 0 if ok else 42


if __name__ == "__main__":
    sys.exit(main())
