# -*- coding: utf-8 -*-
"""edge_trace_page.py —— 让页面自己跑，抓它发给 Queue-it 的真实请求

目的：enqueue 用 ctx.request 直打返回 400（缺 Queue-it-visitorsession / __cf_bm 不匹配）。
     看 SPA 自己怎么发：URL、headers、body、cookie 时序。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
C, E = "tdabsditest", "tdabsd261111kla"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/trace.json"
    print(f"proxy={proxy or '直连'}")
    from patchright.sync_api import sync_playwright

    reqs = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True,
                              args=["--disable-blink-features=AutomationControlled",
                                    "--no-first-run", "--no-default-browser-check"])
        ctx = b.new_context(
            proxy={"server": proxy} if proxy else None,
            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
            viewport={"width": 1366, "height": 768}, ignore_https_errors=True,
        )
        pg = ctx.new_page()

        def on_req(r):
            u = r.url
            if any(k in u for k in ("spa-api", "queue", "enqueue", "status")) and "static" not in u:
                try:
                    pd = r.post_data
                except Exception:
                    pd = None
                reqs.append({"t": round(time.time() - t0, 1), "method": r.method,
                             "url": u[:200], "post": (pd or "")[:200],
                             "headers": {k: v for k, v in r.headers.items()
                                         if k.lower() in ("content-type", "accept", "origin", "referer", "x-requested-with")}})

        def on_resp(r):
            u = r.url
            if any(k in u for k in ("spa-api", "enqueue")) and "static" not in u:
                reqs.append({"t": round(time.time() - t0, 1), "resp": r.status, "url": u[:200]})

        t0 = time.time()
        pg.on("request", on_req)
        pg.on("response", on_resp)
        url = f"https://abs3.example.test/?c={C}&e={E}&lang=sc"
        try:
            r = pg.goto(url, wait_until="domcontentloaded", timeout=90000)
            print(f"goto abs3/?c&e → {r.status if r else '?'}  {time.time()-t0:.1f}s")
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:120])

        # 等 SPA 自己跑（挑战 + Queue-it 初始化）
        for i in range(20):
            pg.wait_for_timeout(1000)
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            if any(k.startswith("Queue-it") or k == "Queue-it-visitorsession" for k in ck):
                print(f"  Queue-it cookie 出现 @ {time.time()-t0:.1f}s: {ck}")
                break
        print("final cookies:", {c["name"]: len(c["value"]) for c in ctx.cookies()})
        print("final url:", pg.url)

        print(f"\n=== 抓到的请求 {len(reqs)} 条 ===")
        for x in reqs[:25]:
            print(" ", json.dumps(x, ensure_ascii=False)[:260])

        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"proxy": proxy, "reqs": reqs,
                                         "cookies": ctx.cookies()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {out}")
        b.close()


if __name__ == "__main__":
    main()
