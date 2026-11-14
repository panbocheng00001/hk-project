# -*- coding: utf-8 -*-
"""一次性 DOM 侦察：用真实 Chromium 走一遍入口页 -> 等候室 -> 闸门，落盘 HTML/截图。
用法: python probe_dom.py [--headless]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
OUT = BASE / "probe"
OUT.mkdir(exist_ok=True)
ENTRY = "https://www.gov.hk/sc/apps/tddiabs.htm"


def settle(page, timeout=30):
    """等 URL 稳定：连续两次采样 URL 相同且 dom ready。"""
    last = None
    for _ in range(timeout * 2):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        cur = page.url
        if cur == last:
            return cur
        last = cur
        page.wait_for_timeout(500)
    return page.url


def dump(page, tag):
    settle(page)
    ts = time.strftime("%H%M%S")
    for _ in range(5):
        try:
            html = page.content()
            break
        except Exception as e:
            print(f"  content retry ({e.__class__.__name__})")
            page.wait_for_timeout(1500)
    else:
        html = ""
    (OUT / f"{tag}_{ts}.html").write_text(html, encoding="utf-8")
    try:
        page.screenshot(path=str(OUT / f"{tag}_{ts}.png"), full_page=True)
    except Exception as e:
        print(f"  screenshot fail: {e}")
    text = re.sub(r"\s+", " ", page.inner_text("body"))[:600] if page.query_selector("body") else ""
    print(f"[{tag}] url={page.url}")
    print(f"[{tag}] title={page.title()!r}")
    print(f"[{tag}] text={text!r}")
    return html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--wait", type=int, default=25)
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless, channel="chromium")
        ctx = browser.new_context(
            locale="zh-CN", timezone_id="Asia/Hong_Kong",
            viewport={"width": 1366, "height": 900},
            user_agent=None,
        )
        page = ctx.new_page()
        pages = []
        page.on("framenavigated", lambda f: pages.append((time.strftime("%H:%M:%S"), f.url)))
        print("goto", ENTRY)
        page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
        dump(page, "01_entry")

        # 入口页通常带 URL= 跳转到等候室
        m = re.search(r"URL=(https://abs\d*\.td\.gov\.hk/)", page.content())
        print("abs host match:", m.group(1) if m else None)

        page.wait_for_timeout(a.wait * 1000)
        dump(page, "02_after_wait")

        # 顺着页面里的链接继续点（模拟真人）
        for i in range(3):
            links = [l for l in page.query_selector_all("a") if l.is_visible()]
            print("visible links:", [(l.inner_text().strip()[:20], l.get_attribute("href")) for l in links][:15])
            if not links:
                break
            try:
                links[0].click(timeout=8000)
            except Exception as e:
                print("click fail:", e)
                break
            page.wait_for_timeout(6000)
            dump(page, f"03_click{i+1}")

        print("nav trace:")
        for t, u in pages:
            print("  ", t, u)
        ctx.close()
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
