# -*- coding: utf-8 -*-
"""edge_enter.py —— 一次性走完 WAF 挑战：412 → 挑战JS → token(xP) → 自动 reload → 200

实测线索（18:52，干净出口 183.131.59.59）：
  412 主文档 → 挑战 JS /RGOj2….js 200 → blob worker 200 → 写入 HIK0kat3rt9xP(492字符)
  → 随后主文档 400（大概率是 JS 还没算完就自动 reload 了）

本脚本：干净出口 + 有头 Chrome + 等到 xP 出现 + 再等自动 reload + 汇报最终状态。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
URL = "https://abs3.example.test/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ENV_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-HK','zh','en']});
window.chrome = window.chrome || {runtime: {}};
"""


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/enter.json"
    headless = "--headful" not in sys.argv
    max_wait = 70.0
    print(f"proxy={proxy or '直连'} headless={headless}")

    docs, jsseen, tokens = [], [], {}
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=headless,
                              args=["--disable-blink-features=AutomationControlled",
                                    "--no-first-run", "--no-default-browser-check"])
        ctx = b.new_context(proxy={"server": proxy} if proxy else None,
                            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
                            viewport={"width": 1366, "height": 768}, ignore_https_errors=True)
        ctx.add_init_script(ENV_INIT)
        pg = ctx.new_page()

        def on_resp(r):
            u = r.url
            if "spa-api" in u:
                return
            if u.endswith(".js"):
                jsseen.append((r.status, u[-40:]))
            elif "abs" in u or "td.example.test" in u:
                docs.append((round(time.time() - t0, 1), r.status, u[:70]))

        t0 = time.time()
        pg.on("response", on_resp)
        try:
            pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:120])

        # 等 token（xP 是挑战通过后写入的 492 字符令牌）
        deadline = time.time() + max_wait
        got = False
        while time.time() < deadline:
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            tokens = ck
            if any(k.startswith("HIK0kat3rt9x") and v > 300 for k, v in ck.items()):
                got = True
                print(f"  ★ token 出现 @ {time.time()-t0:.1f}s: {ck}")
                break
            try:
                pg.wait_for_timeout(1000)
            except Exception:
                break

        # 再等自动 reload 落定
        for _ in range(15):
            try:
                pg.wait_for_timeout(1000)
            except Exception:
                break
            if docs and docs[-1][1] == 200:
                break

        print(f"\n主文档序列: {docs}")
        print(f"JS 序列: {jsseen[:6]}")
        print(f"cookies(长度): {tokens}")
        try:
            html = pg.content() or ""
            print(f"final url={pg.url}  len={len(html)}  title={pg.title()[:50]!r}")
            print("挑战痕迹:", [m for m in ("$_ts", "nsd=") if m in html])
            ok = len(html) > 2000 and "$_ts" not in html
            print("判定:", "★ 已进站(200)" if ok else "仍被挑战/空页")
        except Exception as e:
            print("content:", e)

        try:
            ck = ctx.cookies()
        except Exception:
            ck = []
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
               "docs": docs, "cookies": ck,
               "got_token": got, "final_url": (pg.url if 'pg' in dir() else "")}
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {out}")
        b.close()


if __name__ == "__main__":
    main()
