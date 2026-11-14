# -*- coding: utf-8 -*-
"""edge_flow_probe.py —— 在 abs4 页面里试各种 API 路径/事件，找哪个返回 JSON

背景：过了 WAF 之后 POST /spa-api/queue/tdabsditest/tdabsd261111kla/enqueue
      返回 104KB HTML（SPA fallback），不是 JSON —— 说明路径或 event 不对。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

JS = """
async (args) => {
  const [method, url, body] = args;
  try {
    const r = await fetch(url, {
      method, headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: body || null, credentials: 'include',
    });
    const ct = r.headers.get('content-type') || '';
    const t = await r.text();
    return {status: r.status, ct, len: t.length, head: t.slice(0, 160).replace(/\\s+/g,' ')};
  } catch (err) { return {error: String(err).slice(0, 160)}; }
}
"""

CASES = [
    ("POST enq 261111", "POST", "/spa-api/queue/tdabsditest/tdabsd261111kla/enqueue", "{}"),
    ("POST enq 261110", "POST", "/spa-api/queue/tdabsditest/tdabsd261110kla/enqueue", "{}"),
    ("GET  queue 261111", "GET", "/spa-api/queue/tdabsditest/tdabsd261111kla/status", None),
    ("GET  spa-api 根", "GET", "/spa-api/", None),
    ("GET  spa-api/queue", "GET", "/spa-api/queue", None),
    ("OPTIONS enq", "OPTIONS", "/spa-api/queue/tdabsditest/tdabsd261111kla/enqueue", None),
]


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"proxy={proxy or '直连'}")
    from patchright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled",
                                    "--no-first-run", "--no-default-browser-check"])
        ctx = b.new_context(proxy={"server": proxy} if proxy else None,
                            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
                            viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
        pg = ctx.new_page()
        base = "https://abs4.example.test"
        t0 = time.time()
        try:
            r = pg.goto(base + "/", wait_until="domcontentloaded", timeout=90000)
            print(f"goto → {r.status if r else '?'}  {time.time()-t0:.1f}s")
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:120])
        for _ in range(40):
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            if any(k.startswith("HIK0kat3rt9xP") for k in ck):
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1200)
        print("cookies:", {c["name"]: len(c["value"]) for c in ctx.cookies()})

        print("\n=== API 探测 ===")
        for name, m, path, body in CASES:
            r = pg.evaluate(JS, [m, base + path, body])
            print(f"[{name:18}] {json.dumps(r, ensure_ascii=False)[:260]}")
            pg.wait_for_timeout(500)

        # 顺便看页面里有没有 API 前缀线索
        try:
            html = pg.content()
            for k in ("spa-api", "queue/", "tdabsd", "customer"):
                i = html.find(k)
                if i > 0:
                    print(f"\n页面线索 [{k}]: …{html[max(0,i-80):i+120]}…".replace("\n", " ")[:260])
        except Exception:
            pass
        b.close()


if __name__ == "__main__":
    main()
