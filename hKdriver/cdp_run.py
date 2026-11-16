# -*- coding: utf-8 -*-
"""cdp_run.py —— 接管用户已打开的 Chrome（CDP），不依赖代理池

用法：
  1) 用户启动 Chrome（带调试端口）：
     & "cft\chrome-win64\chrome.exe" --remote-debugging-port=9222 --user-data-dir=runs\_cdp --no-first-run
  2) 在这个 Chrome 里手动打开 https://abs3.td.gov.hk/?lang=sc ，过掉挑战，停在页面上别关
  3) 跑：
     python cdp_run.py --port 9222 --interval 20

它会：
  · 连接你的 Chrome，复用你已过挑战的会话（不新建浏览器、不碰代理）
  · 动态发现当天 event（SelectCountry 302）
  · enqueue → 轮询 status → 出 redirectUrl 就落盘 cookie
  · 可选 --on-win 执行判号命令
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from pathlib import Path

ABS3 = "https://abs3.td.gov.hk"
ABS4 = "https://abs4.td.gov.hk"


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--interval", type=float, default=20)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--on-win", default="")
    ap.add_argument("--out", default="runs/fleet/cdp_winner.json")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    ep = f"http://127.0.0.1:{a.port}"
    log(f"连接 CDP {ep}")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(ep)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        pages = [pg for pg in ctx.pages if "td.gov.hk" in (pg.url or "")]
        pg = pages[0] if pages else (ctx.pages[0] if ctx.pages else ctx.new_page())
        log(f"当前页面: {pg.url}")

        if "abs3.td.gov.hk" not in (pg.url or ""):
            log("不在 abs3，导航过去")
            try:
                pg.goto(f"{ABS3}/?lang=sc", wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"goto: {type(e).__name__}: {str(e)[:100]}")
        # 等挑战落定
        for _ in range(30):
            try:
                txt = pg.content() or ""
            except Exception:
                txt = ""
            if "$_ts" not in txt and len(txt) > 200:
                break
            pg.wait_for_timeout(1000)
        log(f"页面: {pg.url}  len={len(pg.content() or '')}")

        # 发现 event
        ev = ""
        try:
            r = ctx.request.get(f"{ABS3}/SelectCountry?lang=sc&actionType=apply&authMethod=withOutIas",
                                max_redirects=0, timeout=25000)
            loc = r.headers.get("location", "") or r.headers.get("Location", "") or ""
            import urllib.parse
            q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            cust = (q.get("c") or ["tdabsditest"])[0]
            ev = (q.get("e") or [""])[0]
            log(f"event: c={cust} e={ev}")
        except Exception as e:
            log(f"发现 event 失败: {type(e).__name__}: {str(e)[:120]}")
            cust, ev = "tdabsditest", ""
        if not ev:
            log("拿不到 event，退出")
            return 1

        # 首次 enqueue
        qid = ""
        hdr = {"Content-Type": "application/json", "Accept": "application/json",
               "Origin": ABS3, "Referer": ABS3 + "/"}
        for rnd in range(1, (a.rounds or 999) + 1):
            try:
                r = ctx.request.post(f"{ABS4}/spa-api/queue/{cust}/{ev}/enqueue",
                                     data="{}", headers=hdr, timeout=25000)
                t = r.text()
            except Exception as e:
                log(f"#{rnd} enqueue 异常 {type(e).__name__}: {str(e)[:90]}")
                time.sleep(a.interval)
                continue
            if r.status == 200:
                try:
                    j = json.loads(t)
                    qid = j.get("queueId", "")
                except Exception:
                    j = {}
                log(f"#{rnd} enqueue → 200 queueId={qid} redirect={j.get('redirectUrl')}")
                if qid:
                    break
            else:
                log(f"#{rnd} enqueue → {r.status} {t[:120]}")
            time.sleep(a.interval)

        if not qid:
            log("没拿到 queueId，退出")
            return 1

        # 轮询 status
        winner = None
        for i in range(200):
            try:
                r = ctx.request.post(f"{ABS4}/spa-api/queue/{cust}/{ev}/{qid}/status",
                                     data="{}", headers=hdr, timeout=20000)
                j = json.loads(r.text())
            except Exception as e:
                log(f"  status 异常 {type(e).__name__}: {str(e)[:90]}")
                time.sleep(5)
                continue
            tk = j.get("ticket") or {}
            ru = j.get("redirectUrl")
            log(f"  status #{i+1} state={j.get('QueueState')} forecast={j.get('forecastStatus')} "
                f"ahead={tk.get('usersInLineAheadOfYou')} progress={tk.get('progress')} redirect={str(ru)[:70]}")
            if ru:
                winner = {"queueId": qid, "redirectUrl": ru, "event": ev,
                          "cookies": ctx.cookies()}
                break
            time.sleep(5)

        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(winner or {"queueId": qid, "redirectUrl": None,
                                             "cookies": ctx.cookies()},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"→ {out}")
        if winner and a.on_win:
            log(f"执行 on-win: {a.on_win}")
            subprocess.run(a.on_win, shell=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
