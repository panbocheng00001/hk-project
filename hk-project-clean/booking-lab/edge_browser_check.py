# -*- coding: utf-8 -*-
"""edge_browser_check.py —— 浏览器到底能不能 200？抓主文档状态码 + 是否有挑战页痕迹"""
from __future__ import annotations
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
URL = "https://abs3.example.test/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
"""


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    headless = "--headful" not in sys.argv
    print(f"proxy={proxy or '直连'} headless={headless}")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=headless,
                              args=["--disable-blink-features=AutomationControlled",
                                    "--no-first-run", "--no-default-browser-check"])
        ctx = b.new_context(proxy={"server": proxy} if proxy else None,
                            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
                            viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
        ctx.add_init_script(STEALTH)
        pg = ctx.new_page()
        seen = []
        pg.on("response", lambda r: seen.append((r.status, r.url[:100])))
        t0 = time.time()
        try:
            resp = pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
            print(f"主文档: {resp.status if resp else '?'}  {time.time()-t0:.2f}s")
        except Exception as e:
            print("goto 异常:", type(e).__name__, str(e)[:150])
        pg.wait_for_timeout(4000)
        ck = {c["name"]: c["value"] for c in ctx.cookies()}
        print("cookies:", {k: len(v) for k, v in ck.items()})
        try:
            html = pg.content() or ""
            print(f"page.url={pg.url}")
            print(f"title={pg.title()[:60]!r}  body_len={len(html)}")
            print("挑战痕迹:", [m for m in ("$_ts", "nsd=", "HIK0kat3rt9xO") if m in html])
            print("正文片段:", html[html.find('<body'):html.find('<body')+200].replace('\n', ' ')[:200])
        except Exception as e:
            print("content 异常:", e)
        print("\n主要响应链:")
        for st, u in seen[:12]:
            print(f"  {st} {u}")
        b.close()


if __name__ == "__main__":
    main()
