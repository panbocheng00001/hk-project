# -*- coding: utf-8 -*-
"""run_guard.py —— 明天的主脚本：2 浏览器 × 3 context，守 event，排队，拿票，转业务

用户给 IP 后的完整流程：
  ① 起 2 个 Chromium（156 + 131，报 2 种 JA4）
  ② 6 个 context，每个绑一个代理 IP，全部过 abs3 WAF 挑战
  ③ 每 60s 轮换探 enqueue（保活 + 探 event 是否激活）
  ④ 激活 → 6 个同时 enqueue → 轮询 status → 谁出 redirectUrl 谁赢
  ⑤ winner 落 cookie → 调 http_flow.py --step1 判号 → 抓第 2 步页面
  ⑥ 每一步都落盘：日志、截图、HTML、JSON

用法：
  python run_guard.py --proxies runs/pool_runner/ips.txt --n 6 --interval 60
  python run_guard.py --proxies ... --on-win "python http_flow.py --cookies {cookies} --step1"

产物目录：
  runs/guard/<YYYYMMDD-HHMM>/
    guard.log                时间线（人看）
    steps.jsonl              结构化步骤（机器分析）
    ctx01/challenge.html     挑战页原文
    ctx01/after.html         过挑战后的页面
    ctx01/enqueue.json       enqueue 响应
    winner/cookies.json      winner 的完整 cookie
    winner/step1.html        判号响应
    winner/step2.html        有名额时的第 2 步页面
    shots/*.png              关键截图
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
C, E = "tdabsditest", "tdabsd261111kla"
ABS3 = "https://abs3.example.test"
ABS4 = "https://abs4.example.test"
ABS1 = "https://abs1.example.test"
UA156 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
UA131 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

EXES = [
    str(HERE / "cft" / "chrome-win64" / "chrome.exe"),          # 156 → JA4 A
    str(HERE / "cft" / "chrome-131" / "chrome-win64" / "chrome.exe"),  # 131 → JA4 B
]

STEALTH = r"""
(() => {
  const defG = (o, k, v) => { try { Object.defineProperty(o, k, {get: () => v, configurable: true}); } catch (e) {} };
  try {
    window.chrome = window.chrome || {};
    if (!window.chrome.runtime) window.chrome.runtime = {
      id: undefined,
      connect: () => ({onMessage: {addListener(){}, removeListener(){}}, postMessage(){}, disconnect(){}}),
      sendMessage: () => {}, onConnect: {addListener(){}, removeListener(){}},
      onMessage: {addListener(){}, removeListener(){}},
    };
  } catch (e) {}
  try {
    Object.defineProperty(Screen.prototype, 'width',       {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'height',      {get: () => 1080, configurable: true});
    Object.defineProperty(Screen.prototype, 'availWidth',  {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'availHeight', {get: () => 1040, configurable: true});
  } catch (e) {}
  defG(window, 'outerWidth', 1383); defG(window, 'outerHeight', 801);
  defG(window, 'screenX', 8); defG(window, 'screenY', 8);
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 8, configurable: true}); } catch (e) {}
  return {rt: !!(window.chrome && window.chrome.runtime), dm: navigator.deviceMemory,
          sw: [screen.width, screen.height, window.outerWidth, window.innerWidth]};
})();
"""


class Run:
    def __init__(self, outdir: Path):
        self.dir = outdir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logf = self.dir / "guard.log"
        self.steps = self.dir / "steps.jsonl"
        self.shots = self.dir / "shots"
        self.shots.mkdir(exist_ok=True)

    def log(self, msg: str, **kw):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with self.logf.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if kw:
            self.step(msg, **kw)

    def step(self, msg: str, **fields):
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "msg": msg, **fields}
        with self.steps.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def shot(self, page, name: str):
        try:
            p = self.shots / f"{time.strftime('%H%M%S')}_{name}.png"
            page.screenshot(path=str(p), full_page=False)
            return str(p)
        except Exception:
            return ""


def make_ctx(browser, proxy: str, idx: int, ua: str, run: Run):
    ctx = browser.new_context(
        proxy={"server": proxy} if proxy else None,
        user_agent=ua, locale="zh-HK", timezone_id="Asia/Hong_Kong",
        viewport={"width": 1366, "height": 768}, ignore_https_errors=True,
    )
    pg = ctx.new_page()
    d = run.dir / f"ctx{idx:02d}"
    d.mkdir(exist_ok=True)
    t0 = time.time()
    try:
        r = pg.goto(f"{ABS3}/?lang=sc", wait_until="domcontentloaded", timeout=60000)
        st = r.status if r else 0
        body = pg.content() or ""
        (d / "challenge.html").write_text(body, encoding="utf-8")
        run.shot(pg, f"ctx{idx}_challenge")
    except Exception as e:
        run.log(f"  [ctx{idx}] goto 失败 {type(e).__name__}: {str(e)[:80]}",
                action="ctx_goto_fail", ctx=idx, proxy=proxy, err=str(e)[:200])
        return None, "goto-fail"

    for _ in range(40):
        ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
        if any(k.startswith("HIK0kat3rt9xP") for k in ck):
            break
        pg.wait_for_timeout(800)
    pg.wait_for_timeout(1200)
    try:
        stl = pg.evaluate(STEALTH)
    except Exception:
        stl = None
    ck = {c["name"]: len(c["value"]) for c in ctx.cookies()}
    tok = any(k.startswith("HIK0kat3rt9xP") for k in ck)
    after = pg.content() or ""
    (d / "after.html").write_text(after, encoding="utf-8")
    run.shot(pg, f"ctx{idx}_after")
    run.log(f"  [ctx{idx}] {proxy or '直连':26} goto={st} 挑战={'OK' if tok else '未完成'} "
            f"{time.time()-t0:.1f}s env_consistency={json.dumps(stl, ensure_ascii=False) if stl else '-'}",
            action="ctx_ready", ctx=idx, proxy=proxy, goto=st, token=tok,
            sec=round(time.time() - t0, 1), env_consistency=stl, cookies=ck)
    return {"ctx": ctx, "pg": pg, "proxy": proxy, "idx": idx}, ("OK" if tok else "no-token")


def discover_event(ctx) -> tuple[str, str]:
    """GET SelectCountry → 302 Location 里取 c/e（event 每天变，不能写死）"""
    import urllib.parse
    try:
        r = ctx.request.get(f"{ABS3}/SelectCountry?lang=sc&actionType=apply&authMethod=withOutIas",
                            allow_redirects=False, timeout=25000)
        loc = r.headers.get("location", "") or r.headers.get("Location", "") or ""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        return (q.get("c") or [""])[0], (q.get("e") or [""])[0]
    except Exception:
        return "", ""


def probe_enqueue(ctx, event: str):
    hdr = {"Content-Type": "application/json", "Accept": "application/json",
           "Origin": ABS3, "Referer": ABS3 + "/"}
    try:
        r = ctx.request.post(f"{ABS4}/spa-api/queue/{C}/{event}/enqueue",
                             data="{}", headers=hdr, timeout=25000)
        t = r.text()
    except Exception as e:
        return "ERR", 0, f"{type(e).__name__}: {str(e)[:80]}"
    if r.status == 412 or "$_ts" in t[:2000] or "nsd=" in t[:2000]:
        return "CHALLENGE", r.status, t[:160]
    if r.status == 200:
        try:
            j = json.loads(t)
            if j.get("queueId"):
                return "ACTIVE", r.status, json.dumps(j)
        except Exception:
            pass
        return "HTML", r.status, t[:160]
    if r.status == 400 and "traceId" in t:
        return "INACTIVE", r.status, "queue-it 400（缺 visitorsession / event 未开）"
    return f"HTTP{r.status}", r.status, t[:160]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxies", default="", help="代理清单，一行一个（ip:port 或 http://ip:port）")
    ap.add_argument("--n", type=int, default=6, help="context 数，默认 6（2 浏览器 × 3）")
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--event", default=E)
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--on-win", default="", help="放行后执行，{cookies} 会替换成 cookie 路径")
    ap.add_argument("--outdir", default="")
    a = ap.parse_args()

    outdir = Path(a.outdir) if a.outdir else Path("runs/guard") / time.strftime("%Y%m%d-%H%M")
    run = Run(outdir)
    run.log(f"启动 outdir={outdir}", action="start", event=a.event, n=a.n, interval=a.interval)

    proxies = [""]
    if a.proxies and Path(a.proxies).exists():
        proxies = [l.strip() for l in Path(a.proxies).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not any("://" in p for p in proxies if p):
        proxies = [p if (not p or "://" in p) else "http://" + p for p in proxies]
    proxies = proxies[: a.n]
    run.log(f"代理 {len(proxies)} 个: {proxies}")

    from patchright.sync_api import sync_playwright
    ARGS = ["--disable-blink-features=AutomationControlled",
            "--disable-gpu", "--disable-dev-shm-usage",
            "--blink-settings=imagesEnabled=false",
            "--no-first-run", "--no-default-browser-check"]

    with sync_playwright() as p:
        exes = [e for e in EXES if Path(e).exists()]
        browsers = [p.chromium.launch(executable_path=e, headless=True, args=ARGS) for e in exes]
        run.log(f"浏览器 {len(browsers)} 个: {[Path(e).parent.parent.name for e in exes]}",
                action="browsers", count=len(browsers))

        live = []
        for i, px in enumerate(proxies, 1):
            br = browsers[(i - 1) % len(browsers)]
            ua = UA156 if (i - 1) % 2 == 0 else UA131
            got, info = make_ctx(br, px, i, ua, run)
            if got:
                got["br"] = br
                live.append(got)
        run.log(f"就绪 {len(live)} 个会话", action="sessions_ready", n=len(live))
        if not live:
            run.log("没有可用会话，退出", action="abort")
            for bb in browsers:
                bb.close()
            return 1

        # 动态发现 event（每天变：tdabsd<yymmdd><suffix>）
        global C
        cust, ev = discover_event(live[0]["ctx"])
        if ev:
            C = cust or C
            a.event = ev
            run.log(f"发现 event: c={C} e={a.event}", action="event_discovered", customer=C, event=ev)
        else:
            run.log(f"event 发现失败，沿用 {a.event}", action="event_discover_fail")

        # 守 event
        turn = 0
        rnd = 0
        while True:
            rnd += 1
            s = live[turn % len(live)]
            turn += 1
            kind, st, body = probe_enqueue(s["ctx"], a.event)
            run.log(f"  #{rnd:03d} [{s['proxy'] or '直连':26}] → {kind} ({st}) {body[:100]}",
                    action="probe", round=rnd, proxy=s["proxy"], kind=kind, status=st, body=body[:400])
            if kind == "ACTIVE":
                run.log("★★★ event 已激活", action="activated")
                break
            if kind == "CHALLENGE":
                run.log("  挑战又起，重建该 context", action="ctx_rebuild", proxy=s["proxy"])
                try:
                    s["ctx"].close()
                except Exception:
                    pass
                got, _ = make_ctx(s["br"], s["proxy"], s["idx"], UA156, run)
                if got:
                    s.update(got)
            if a.rounds and rnd >= a.rounds:
                run.log("轮次用完", action="rounds_done")
                for bb in browsers:
                    bb.close()
                return 0
            time.sleep(a.interval)

        # 全部 enqueue
        results = []
        for s in live:
            kind, st, body = probe_enqueue(s["ctx"], a.event)
            qid = ""
            try:
                qid = json.loads(body).get("queueId", "")
            except Exception:
                pass
            run.log(f"  enqueue[{s['idx']}] {s['proxy']} → {kind} queueId={qid}",
                    action="enqueue", ctx=s["idx"], proxy=s["proxy"], kind=kind, queueId=qid, body=body[:400])
            results.append({"idx": s["idx"], "proxy": s["proxy"], "queueId": qid, "kind": kind})
            time.sleep(0.6)

        # 轮询 status
        winner = None
        for r_i in range(80):
            for i, s in enumerate(live):
                qid = results[i].get("queueId")
                if not qid:
                    continue
                try:
                    r = s["ctx"].request.post(
                        f"{ABS4}/spa-api/queue/{C}/{a.event}/{qid}/status",
                        data="{}", headers={"Content-Type": "application/json",
                                            "Accept": "application/json"}, timeout=20000)
                    t = r.text()
                    j = json.loads(t)
                except Exception as e:
                    run.step("status_err", ctx=s["idx"], err=str(e)[:120])
                    continue
                tk = j.get("ticket") or {}
                ru = j.get("redirectUrl")
                run.log(f"  status[{s['idx']}] state={j.get('QueueState')} "
                        f"forecast={j.get('forecastStatus')} ahead={tk.get('usersInLineAheadOfYou')} "
                        f"redirect={str(ru)[:70]}",
                        action="status", ctx=s["idx"], state=j.get("QueueState"),
                        forecast=j.get("forecastStatus"), ahead=tk.get("usersInLineAheadOfYou"),
                        redirect=ru, raw=j)
                if ru:
                    winner = {"idx": s["idx"], "proxy": s["proxy"], "queueId": qid,
                              "redirectUrl": ru, "cookies": s["ctx"].cookies()}
                    run.log(f"★★★ 放行！ctx{s['idx']} {s['proxy']}", action="winner",
                            ctx=s["idx"], proxy=s["proxy"], redirectUrl=ru)
                    break
            if winner:
                break
            time.sleep(5)

        # 落 winner
        wdir = run.dir / "winner"
        wdir.mkdir(exist_ok=True)
        ckfile = wdir / "cookies.json"
        if winner:
            ckfile.write_text(json.dumps(winner["cookies"], ensure_ascii=False, indent=2), encoding="utf-8")
            (wdir / "winner.json").write_text(json.dumps(winner, ensure_ascii=False, indent=2), encoding="utf-8")
            run.log(f"winner cookie → {ckfile}", action="winner_saved", path=str(ckfile))
        else:
            run.log("没有 winner（event 未放行）", action="no_winner")

        # 转业务判号
        if winner and a.on_win:
            cmd = a.on_win.replace("{cookies}", str(ckfile))
            run.log(f"执行 on-win: {cmd}", action="on_win", cmd=cmd)
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=600)
                out = (r.stdout or "")[-3000:]
                (wdir / "onwin.log").write_text((r.stdout or "") + "\n---STDERR---\n" + (r.stderr or ""),
                                                encoding="utf-8")
                run.log(f"on-win 退出码 {r.returncode}", action="on_win_done", code=r.returncode, tail=out[-800:])
            except Exception as e:
                run.log(f"on-win 异常: {e}", action="on_win_err", err=str(e)[:200])

        for bb in browsers:
            bb.close()
    run.log("收工", action="end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
