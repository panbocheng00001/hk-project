# -*- coding: utf-8 -*-
"""waf_patchright.py —— 用 patchright（反检测 playwright）跑 abs3，看能不能过 412

对比基线（原版 playwright，今天实测）：
  headless → 412，挑战 JS 400
  headful  → 412，挑战 JS 200，写 xP(492)，随后 reload 400

patchright 会关掉 Runtime.enable / Console 注入 / CDP 泄漏，理论上更接近真人。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
URL = "https://abs3.td.gov.hk/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/patchright.json"
    headless = "--headful" not in sys.argv
    print(f"proxy={proxy or '直连'} headless={headless}")

    from patchright.sync_api import sync_playwright

    docs, js, ck_hist = [], [], []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=headless,
                              args=["--disable-blink-features=AutomationControlled",
                                    "--no-first-run", "--no-default-browser-check"])
        ctx = b.new_context(
            proxy={"server": proxy} if proxy else None,
            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
            viewport={"width": 1366, "height": 768}, ignore_https_errors=True,
        )
        pg = ctx.new_page()
        t0 = time.time()

        def on_resp(r):
            u = r.url
            if u.endswith(".js"):
                js.append((r.status, u[-38:]))
            elif "td.gov.hk" in u and "queue-it" not in u:
                docs.append((round(time.time() - t0, 1), r.status, u[:70]))

        pg.on("response", on_resp)
        try:
            resp = pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
            print(f"主文档: {resp.status if resp else '?'}  {time.time()-t0:.2f}s")
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:140])

        # 等挑战算完
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            except Exception:
                break
            ck_hist = ck
            if any(k.startswith("HIK0kat3rt9x") and v > 300 for k, v in ck.items()):
                print(f"  ★ xP token @ {time.time()-t0:.1f}s: {ck}")
                break
            try:
                pg.wait_for_timeout(1000)
            except Exception:
                break

        # 再等自动 reload 落定
        for _ in range(20):
            try:
                pg.wait_for_timeout(1000)
            except Exception:
                break
            if docs and docs[-1][1] == 200:
                break

        print(f"\n主文档序列: {docs}")
        print(f"JS 序列: {js[:8]}")
        print(f"cookies: {ck_hist}")
        try:
            html = pg.content() or ""
            print(f"final={pg.url}  len={len(html)}  title={pg.title()[:50]!r}")
            print("挑战痕迹:", [m for m in ("$_ts", "nsd=") if m in html])
            ok = len(html) > 2000 and "$_ts" not in html
            print("判定:", "★ 过挑战，进站了" if ok else "仍在挑战/空页")
        except Exception as e:
            print("content:", e)

        try:
            ckfull = ctx.cookies()
        except Exception:
            ckfull = []
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
            "engine": "patchright", "docs": docs, "js": js, "cookies": ckfull,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {out}")
        b.close()


if __name__ == "__main__":
    main()
