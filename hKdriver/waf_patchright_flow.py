# -*- coding: utf-8 -*-
"""waf_patchright_flow.py —— patchright 过挑战后，直接用页面内 fetch 打 Queue-it API

实测（20:5x）：patchright headless 一次过挑战
  412 (0.8s) → 挑战JS 200 → xP(343) → 主文档 200 (1.9s)，title=ABSDI，无挑战痕迹

本脚本：同一 context 里过挑战 → 页面内 fetch enqueue → 拿 queueId → 轮询 status。
这样请求带着真浏览器指纹，WAF 不会 400。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
URL = "https://abs3.td.gov.hk/?lang=sc"
C, E = "tdabsditest", "tdabsd261111kla"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ENQ_JS = """
async (args) => {
  const [c, e] = args;
  const url = `https://abs4.td.gov.hk/spa-api/queue/${c}/${e}/enqueue`;
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: '{}',
      credentials: 'include',
      mode: 'cors',
    });
    const t = await r.text();
    return {status: r.status, len: t.length, body: t.slice(0, 600)};
  } catch (err) {
    return {error: String(err).slice(0, 200)};
  }
}
"""

STATUS_JS = """
async (args) => {
  const [c, e, qid] = args;
  const url = `https://abs4.td.gov.hk/spa-api/queue/${c}/${e}/${qid}/status`;
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: '{}', credentials: 'include', mode: 'cors',
    });
    const t = await r.text();
    return {status: r.status, body: t.slice(0, 800)};
  } catch (err) { return {error: String(err).slice(0, 200)}; }
}
"""


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/flow.json"
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
        try:
            pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:120])

        # 等过挑战（主文档 200 + xP）
        ok = False
        for _ in range(60):
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            if any(k.startswith("HIK0kat3rt9xP") for k in ck):
                ok = True
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        print(f"挑战: {'通过' if ok else '未完成'}  {time.time()-t0:.1f}s  cookies={ck}")

        # 1) enqueue
        r1 = pg.evaluate(ENQ_JS, [C, E])
        print(f"\n[enqueue] {json.dumps(r1, ensure_ascii=False)[:500]}")

        qid = ""
        if isinstance(r1, dict) and r1.get("body"):
            try:
                j = json.loads(r1["body"])
                qid = j.get("queueId") or ""
                print(f"  queueId={qid}  challengeRequired={j.get('challengeRequired')}  busy={j.get('serverIsBusy')}")
            except Exception:
                pass

        # 2) status 轮询
        results = []
        if qid:
            for i in range(5):
                pg.wait_for_timeout(2000)
                r = pg.evaluate(STATUS_JS, [C, E, qid])
                results.append(r)
                s = "?"
                if isinstance(r, dict) and r.get("body"):
                    try:
                        j = json.loads(r["body"])
                        s = (f"state={j.get('QueueState')} page={j.get('pageClass')} "
                             f"forecast={j.get('forecastStatus')} redirect={str(j.get('redirectUrl'))[:50]}")
                    except Exception:
                        s = r["body"][:120]
                print(f"  [{i+1}] {r.get('status')}  {s}")

        # 3) 落盘
        ckall = []
        try:
            ckall = ctx.cookies()
        except Exception:
            pass
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
            "challenge": ok, "enqueue": r1, "queueId": qid, "status": results,
            "cookies": ckall,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {out}")
        b.close()


if __name__ == "__main__":
    main()
