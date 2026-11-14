# -*- coding: utf-8 -*-
"""侦察排队页：进入等候室后持续观察，直到进入 abs3 SelectCountry 或超时。
用法: python probe_queue.py [--headless] [--max 180]
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
TARGET_HINT = "SelectCountry"


def txt(page):
    try:
        return re.sub(r"\s+", " ", page.inner_text("body"))[:500]
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max", type=int, default=180)
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Hong_Kong",
                                  viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()
        page.goto(ENTRY, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        page.wait_for_selector('input[name="conditionsCheck"]', timeout=45000)
        page.query_selector('input[name="conditionsCheck"]').check(force=True)
        for b in page.query_selector_all("button"):
            if b.inner_text().strip() == "预约":
                b.click()
                break
        page.wait_for_timeout(3500)
        page.query_selector('input[type="radio"][value="withOutIas"]').check(force=True)
        page.wait_for_timeout(300)
        for b in page.query_selector_all("button"):
            if b.inner_text().strip() == "继续":
                b.click()
                break

        t0 = time.time()
        last = ""
        entered = None
        while time.time() - t0 < a.max:
            page.wait_for_timeout(3000)
            url = page.url
            body = txt(page)
            state = "QUEUE" if "abs4" in url else ("ENTERED" if TARGET_HINT in url else "?")
            line = f"{int(time.time()-t0):3d}s {state} {url[:120]} :: {body[:220]}"
            if line.split(" :: ")[0] != last.split(" :: ")[0] or state != "QUEUE":
                print(line, flush=True)
            last = line
            if state == "ENTERED":
                entered = url
                page.wait_for_timeout(2000)
                (OUT / "queue_entered.json").write_text(
                    json.dumps({"url": page.url, "text": txt(page)}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                page.screenshot(path=str(OUT / "queue_entered.png"), full_page=True)
                break
        print("entered:", entered)
        ctx.close()
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
