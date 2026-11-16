# -*- coding: utf-8 -*-
"""一次性侦察：真实 Chromium 走 入口页 -> 勾同意 -> 点「预约」-> 观察排队页 DOM。
用法: python probe_flow.py [--headless] [--hold 30]
"""
import argparse
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
OUT = BASE / "probe"
OUT.mkdir(exist_ok=True)
ENTRY = "https://www.gov.hk/sc/apps/tddiabs.htm"


def snap(page, tag):
    ts = time.strftime("%H%M%S")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    try:
        html = page.content()
    except Exception:
        page.wait_for_timeout(1500)
        html = page.content()
    (OUT / f"{tag}_{ts}.html").write_text(html, encoding="utf-8")
    try:
        page.screenshot(path=str(OUT / f"{tag}_{ts}.png"), full_page=True)
    except Exception:
        pass
    body = ""
    try:
        body = re.sub(r"\s+", " ", page.inner_text("body"))[:800]
    except Exception:
        pass
    print(f"[{tag}] url={page.url}\n[{tag}] title={page.title()!r}\n[{tag}] text={body!r}")
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--hold", type=int, default=30)
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Hong_Kong",
                                 viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        reqs = []
        page.on("request", lambda r: reqs.append((time.strftime("%H:%M:%S"), r.method, r.url)) if "td.gov.hk" in r.url and "static" not in r.url else None)
        page.on("response", lambda r: print(f"  <- {r.status} {r.url[:130]}") if "td.gov.hk" in r.url and "static" not in r.url else None)

        page.goto(ENTRY, wait_until="load", timeout=60000)
        page.wait_for_timeout(4000)
        snap(page, "10_loaded")

        # 勾同意
        cb = page.query_selector('input[name="conditionsCheck"]')
        print("checkbox found:", bool(cb))
        if cb:
            cb.check(force=True)
            page.wait_for_timeout(500)
            snap(page, "11_checked")

        # 点「预约」
        btn = None
        for b in page.query_selector_all("button"):
            if b.inner_text().strip() == "预约":
                btn = b
                break
        print("button found:", bool(btn))
        if btn:
            btn.click()
            page.wait_for_timeout(6000)
            snap(page, "12_after_booking_click")

        # 观察排队页是否自动刷新
        for i in range(3):
            page.wait_for_timeout(max(3, a.hold // 3) * 1000)
            snap(page, f"13_poll{i+1}")

        print("\nrequests:")
        for t, m, u in reqs:
            print("  ", t, m, u[:150])
        ctx.close()
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
