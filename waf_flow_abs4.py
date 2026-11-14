# -*- coding: utf-8 -*-
"""waf_flow_abs4.py —— 在 abs4 自己的页面里过挑战，再同源 fetch Queue-it API

为什么：abs3 页面里 fetch abs4 会被 CORS 拦（Failed to fetch）。
       同源（abs4 页面 fetch abs4 API）就没有 CORS 问题，且请求带真浏览器指纹。
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
C, E = "tdabsditest", "tdabsd261111kla"
# UA 必须和实际 Chromium 版本一致（实测 UA-CH 报 156.0.8078.80，写 131 会自相矛盾）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# 补掉 headless 的四个静态漏点（实测：patchright 默认 userAgentData=null、
# chrome.runtime 缺失、screen 与窗口全相等、deviceMemory=null）
STEALTH = r"""
(() => {
  const defG = (o, k, v) => { try { Object.defineProperty(o, k, {get: () => v, configurable: true}); } catch (e) {} };
  // 1) chrome.runtime（window.chrome 是 writable data property，直接挂）
  try {
    window.chrome = window.chrome || {};
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        id: undefined,
        connect: () => ({onMessage: {addListener(){}, removeListener(){}}, postMessage(){}, disconnect(){}}),
        sendMessage: () => {},
        onConnect: {addListener(){}, removeListener(){}},
        onMessage: {addListener(){}, removeListener(){}},
      };
    }
  } catch (e) {}
  // 2) screen（改 Screen.prototype 的 getter，实测可改）
  try {
    Object.defineProperty(Screen.prototype, 'width',       {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'height',      {get: () => 1080, configurable: true});
    Object.defineProperty(Screen.prototype, 'availWidth',  {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'availHeight', {get: () => 1040, configurable: true});
    Object.defineProperty(Screen.prototype, 'availLeft',   {get: () => 0,    configurable: true});
    Object.defineProperty(Screen.prototype, 'availTop',    {get: () => 0,    configurable: true});
  } catch (e) {}
  // 3) window 外框（真浏览器 outer > inner）
  defG(window, 'outerWidth', 1383); defG(window, 'outerHeight', 801);
  defG(window, 'screenX', 8); defG(window, 'screenY', 8);
  // 4) deviceMemory（在 Navigator.prototype 上）
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 8, configurable: true}); } catch (e) {}
  return {rt: !!(window.chrome && window.chrome.runtime), dm: navigator.deviceMemory,
          sw: [screen.width, screen.height, window.outerWidth, window.innerWidth]};
})();
"""

JS = """
async (args) => {
  const [method, url, body] = args;
  try {
    const r = await fetch(url, {
      method, headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: body || null, credentials: 'include',
    });
    const t = await r.text();
    return {status: r.status, len: t.length, body: t.slice(0, 800)};
  } catch (err) { return {error: String(err).slice(0, 200)}; }
}
"""


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    out = sys.argv[2] if len(sys.argv) > 2 else "runs/tokens/abs4.json"
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
        ctx.add_init_script(STEALTH)
        pg = ctx.new_page()
        base = "https://abs4.td.gov.hk"
        t0 = time.time()
        st = None
        try:
            r = pg.goto(base + "/", wait_until="domcontentloaded", timeout=90000)
            st = r.status if r else None
            print(f"goto {base}/  → {st}  {time.time()-t0:.1f}s")
        except Exception as e:
            print("goto:", type(e).__name__, str(e)[:130])

        # 等挑战
        for _ in range(40):
            ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
            if any(k.startswith("HIK0kat3rt9xP") for k in ck):
                break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        # 挑战通过后再补指纹（patchright 屏蔽了 add_init_script / CDP 注入，只能用 evaluate）
        try:
            st = pg.evaluate(STEALTH)
            print(f"stealth 注入: {json.dumps(st, ensure_ascii=False)}")
        except Exception as e:
            print("stealth 注入失败:", type(e).__name__, str(e)[:120])
        ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
        print(f"cookies: {ck}  url={pg.url}")

        # enqueue（同源）
        enq = f"{base}/spa-api/queue/{C}/{E}/enqueue"
        r1 = pg.evaluate(JS, ["POST", enq, "{}"])
        print(f"\n[enqueue] {json.dumps(r1, ensure_ascii=False)[:600]}")

        qid = ""
        try:
            j = json.loads(r1.get("body") or "{}")
            qid = j.get("queueId") or ""
        except Exception:
            j = {}
        if qid:
            print(f"  ★ queueId={qid}  challengeRequired={j.get('challengeRequired')}  busy={j.get('serverIsBusy')}")

        # status
        res = []
        if qid:
            for i in range(6):
                pg.wait_for_timeout(2000)
                r = pg.evaluate(JS, ["POST", f"{base}/spa-api/queue/{C}/{E}/{qid}/status", "{}"])
                res.append(r)
                s = ""
                try:
                    jj = json.loads(r.get("body") or "{}")
                    s = (f"state={jj.get('QueueState')} page={jj.get('pageClass')} "
                         f"forecast={jj.get('forecastStatus')} "
                         f"ahead={(jj.get('ticket') or {}).get('usersInLineAheadOfYou')} "
                         f"redirect={str(jj.get('redirectUrl'))[:60]}")
                except Exception:
                    s = (r.get("body") or "")[:120]
                print(f"  [{i+1}] {r.get('status')}  {s}")

        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
            "goto_status": st, "enqueue": r1, "queueId": qid, "status": res,
            "cookies": ctx.cookies(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ {out}")
        b.close()


if __name__ == "__main__":
    main()
