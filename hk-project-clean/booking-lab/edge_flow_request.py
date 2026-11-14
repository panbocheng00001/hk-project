# -*- coding: utf-8 -*-
"""edge_flow_request.py —— 过挑战后用 context.request 打 API（绕 CORS，带 context cookie）

为什么：abs3 页面里 fetch abs4 被 CORS 拦；abs4 首页现在 404，同源页面也拿不到。
       context.request 走浏览器网络栈、复用 cookie、不受 CORS 限制。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
C, E = "tdabsditest", "tdabsd261111kla"
BASE = "https://abs4.example.test"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/req.json"
    print(f"proxy={proxy or '直连'}")
    from patchright.sync_api import sync_playwright

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
        t0 = time.time()
        # 1) 过 abs3 挑战
        try:
            r = pg.goto("https://abs3.example.test/?lang=sc", wait_until="domcontentloaded", timeout=90000)
            print(f"abs3 → {r.status if r else '?'}  {time.time()-t0:.1f}s")
        except Exception as e:
            print("goto abs3:", type(e).__name__, str(e)[:120])
        for _ in range(45):
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            if any(k.startswith("HIK0kat3rt9xP") for k in ck):
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        print("cookies:", {c["name"]: len(c["value"]) for c in ctx.cookies()})

        # 2) context.request 打 enqueue（带 cookie，不走 CORS）
        hdr = {"Content-Type": "application/json", "Accept": "application/json",
               "Origin": "https://abs3.example.test", "Referer": "https://abs3.example.test/"}
        try:
            resp = ctx.request.post(f"{BASE}/spa-api/queue/{C}/{E}/enqueue",
                                    data="{}", headers=hdr, timeout=30000)
            txt = resp.text()
            print(f"\n[enqueue] {resp.status}  len={len(txt)}")
            print("  body:", txt[:500])
            qid = ""
            try:
                j = json.loads(txt)
                qid = j.get("queueId") or ""
            except Exception:
                pass
            if qid:
                print(f"  ★ queueId={qid}")
                for i in range(5):
                    pg.wait_for_timeout(2000)
                    r2 = ctx.request.post(f"{BASE}/spa-api/queue/{C}/{E}/{qid}/status",
                                          data="{}", headers=hdr, timeout=30000)
                    t2 = r2.text()
                    print(f"  [{i+1}] {r2.status}  {t2[:220]}")
        except Exception as e:
            print("request 异常:", type(e).__name__, str(e)[:200])

        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
            "cookies": ctx.cookies(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        b.close()


if __name__ == "__main__":
    main()
