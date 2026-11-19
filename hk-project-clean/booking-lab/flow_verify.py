#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flow_verify.py â€”â€” å• IP å…¨é“¾è·¯é€æ­¥éª¤è‡ªæ£€ï¼ˆä¸“é—¨ç•™ä¸€å°æœºå™¨éªŒè„šæœ¬ï¼Œä¸åé¢ç›‘æµ‹ï¼‰
===========================================================================
æŠŠæ•´æ¡é“¾è·¯æ‹†æˆ 7 ä¸ªå¯å•ç‹¬æ–­è¨€çš„ç‚¹ï¼Œæ¯ä¸€æ­¥éƒ½ç»™ PASS / FAIL / SKIP + è€—æ—¶ + è¯æ®ï¼š

  S1 WAF è½åœ°      abs3/?lang=sc                       æ–­è¨€ 200 ä¸”ä¸‹å‘ HWWAFSESID
  S2 æŽ’é˜Ÿå‘çŽ°      abs3/SelectCountry                  æ–­è¨€ 302 ä¸” Location å« c/e/t
  S3 å–å·          abs4/spa-api/queue/â€¦/enqueue        æ–­è¨€ 200 ä¸”è¿”å›ž queueId
  S4 æŽ’é˜ŸçŠ¶æ€      abs4/spa-api/queue/â€¦/{id}/status    æ–­è¨€ 200 ä¸”å« QueueState/forecastStatus
  S5 è¿›ä¸šåŠ¡        {t}                                 éœ€æ”¾è¡Œ / éœ€ä¼šè¯ï¼Œå¦åˆ™ SKIP
  S6 åˆ¤å·          AdminServlet_<lang>?cmd=cmdGetOfficeQuotaDIAction
                                                       æ–­è¨€ 200 ä¸”èƒ½åˆ¤å‡ºã€Œæœ‰åé¢/æ— åé¢ã€+ TrackId
  S7 åé¢æ¦‚è§ˆ      cmdGetOfficeBookingStatusAction     æ–­è¨€ 200 ä¸”è§£æžå‡ºåŠžäº‹å¤„ä¸Žæ—¶æ®µè¡Œ

ç”¨æ³•
----
    # åªéªŒ â‘ ~â‘£ï¼ˆä¸éœ€è¦ä¼šè¯ï¼Œéšæ—¶å¯è·‘ï¼‰
    python flow_verify.py

    # å¸¦ä¸Šæµè§ˆå™¨å¯¼å‡ºçš„ cookieï¼Œè¿ž â‘¤~â‘¦ ä¸€èµ·éªŒ
    python flow_verify.py --cookies runs/rev/browser_cookies.json

    # èµ°ä»£ç†éªŒï¼ˆè·Ÿæ­£å¼åé¢ç›‘æµ‹åŒä¸€æ¡é“¾è·¯ï¼‰
    python flow_verify.py --cookies runs/rev/browser_cookies.json --proxy http://ip:port

    # è¿žç»­éªŒï¼ˆæ¯ 60 ç§’ä¸€è½®ï¼Œæœ€å¤š 30 è½®ï¼›ä»»ä½•ä¸€æ­¥é€€åŒ–ç«‹åˆ»æŠ¥ï¼‰
    python flow_verify.py --cookies ... --loop 60 --rounds 30

é€€å‡ºç ï¼š0 å…¨é€š / 42 é“¾è·¯é€šä½†æœªæ”¾è¡Œï¼ˆS5~S7 SKIPï¼‰ / å…¶ä»– = ç¬¬ä¸€ä¸ªå¤±è´¥æ­¥éª¤çš„ç 
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_flow import Sniper, Log, STEP1_ENTRY  # noqa: E402

BASE = Path(__file__).resolve().parent
OUT = BASE / "runs" / "verify"
OUT.mkdir(parents=True, exist_ok=True)


def step(name: str, code: int, detail: str, sec: float) -> dict:
    status = "PASS" if code == 0 else ("SKIP" if code == -1 else "FAIL")
    icon = {"PASS": "âˆš", "FAIL": "Ã—", "SKIP": "-"}[status]
    print(f"  {icon} {name:14} {sec:5.2f}s  {detail}", flush=True)
    return {"step": name, "status": status, "sec": round(sec, 2),
            "code": code, "detail": detail}


def run_once(proxy: str, cookies: str, app: dict | None) -> tuple[int, list[dict]]:
    rows: list[dict] = []
    sp = Sniper(proxy=proxy, log=Log("verify", file=False))
    if cookies:
        sp.load_cookies(cookies)

    # ---------------- S1
    t0 = time.time()
    try:
        r = sp._get("https://abs3.example.test/?lang=sc", allow_redirects=True)
        ck = list(sp.s.cookies.keys())
        c = sp.classify(r)
        ok = (r.status_code == 200 and "HWWAFSESID" in ck)
        rows.append(step("S1 WAF", 0 if ok else (c or 20),
                         f"{r.status_code} len={len(r.content)} cookies={ck[:3]}",
                         time.time() - t0))
        if not ok:
            return c or 20, rows
    except Exception as e:
        rows.append(step("S1 WAF", 20, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t0))
        return 20, rows

    # ---------------- S2
    t0 = time.time()
    try:
        r = sp._get("https://abs3.example.test/SelectCountry?"
                    "lang=sc&actionType=apply&authMethod=withOutIas",
                    allow_redirects=False)
        loc = r.headers.get("Location", "")
        c = sp.classify(r)
        if r.status_code == 200:
            rows.append(step("S2 æŽ’é˜Ÿå‘çŽ°", 0, "200ï¼ˆå·²åœ¨æ”¾è¡Œæ€ï¼Œè·³è¿‡æŽ’é˜Ÿï¼‰", time.time() - t0))
            sp.ctx.target = "https://abs3.example.test/SelectCountry?" \
                            "lang=sc&actionType=apply&authMethod=withOutIas"
            released = True
        elif r.status_code in (301, 302, 303):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(loc).query)
            sp.ctx.customer = (q.get("c") or [""])[0]
            sp.ctx.event = (q.get("e") or [""])[0]
            sp.ctx.target = (q.get("t") or [""])[0]
            ok = bool(sp.ctx.customer and sp.ctx.event)
            rows.append(step("S2 æŽ’é˜Ÿå‘çŽ°", 0 if ok else 20,
                             f"302 c={sp.ctx.customer} e={sp.ctx.event}", time.time() - t0))
            released = False
            if not ok:
                return 20, rows
        else:
            rows.append(step("S2 æŽ’é˜Ÿå‘çŽ°", c or 20, f"{r.status_code}", time.time() - t0))
            return c or 20, rows
    except Exception as e:
        rows.append(step("S2 æŽ’é˜Ÿå‘çŽ°", 20, f"{type(e).__name__}", time.time() - t0))
        return 20, rows

    # ---------------- S3
    if not released:
        t0 = time.time()
        try:
            c = sp.enqueue()
            rows.append(step("S3 å–å·", c,
                             f"queueId={sp.ctx.queue_id or '-'}", time.time() - t0))
            if c:
                return c, rows
        except Exception as e:
            rows.append(step("S3 å–å·", 20, f"{type(e).__name__}", time.time() - t0))
            return 20, rows

        # ---------------- S4
        t0 = time.time()
        try:
            # 轮询最多 90 秒等放行（带 body+头缓存后，实测 5~6 次即放行）
            deadline = time.time() + 90
            poll = 0
            c, st = 0, None
            while time.time() < deadline:
                poll += 1
                c, st = sp.status(poll)
                if c:
                    break
                if st.released:
                    break
                time.sleep(2)
            ok = (c == 0 and (st.queue_state is not None or st.released))
            rows.append(step("S4 排队状态", 0 if ok else (c or 20),
                             f"poll={poll} state={st.queue_state} forecast={st.forecast} "
                             f"released={st.released}", time.time() - t0))
            if st.released:
                released = True
                if st.redirect_url:
                    sp.claim_release_ticket(st.redirect_url)
                    sp.ctx.target = sp._https_target()
            if not ok:
                return c or 20, rows
        except Exception as e:
            rows.append(step("S4 æŽ’é˜ŸçŠ¶æ€", 20, f"{type(e).__name__}", time.time() - t0))
            return 20, rows

    # ---------------- S5
    t0 = time.time()
    if not released:
        rows.append(step("S5 è¿›ä¸šåŠ¡", -1, "æœªæ”¾è¡Œï¼Œè·³è¿‡ï¼ˆç­‰ 17:00 æ´—ç‰ŒåŽå†éªŒï¼‰", time.time() - t0))
    else:
        try:
            c = sp.enter()
            rows.append(step("S5 è¿›ä¸šåŠ¡", c, "ä¸šåŠ¡é¡µå·²è½ç›˜", time.time() - t0))
        except Exception as e:
            rows.append(step("S5 è¿›ä¸šåŠ¡", 20, f"{type(e).__name__}", time.time() - t0))

    # ---------------- S4b æ”¾è¡Œç¥¨
    if released and hasattr(sp, "_qi_header") and sp.ctx.queue_id in sp._qi_header:
        pass  # ç¥¨åœ¨ watch/enter é“¾è·¯é‡Œå·²ç”± claim_release_ticket è½è¢‹

    # ---------------- S6
    t0 = time.time()
    if not cookies and not released:
        rows.append(step("S6 åˆ¤å·", -1, "æ²¡æœ‰ä¼šè¯ cookieï¼Œè·³è¿‡", time.time() - t0))
    else:
        try:
            c, body = sp.step1(app=app)
            track = ""
            import re
            m = re.search(r"TrackId:\s*([0-9\-a-f]+)", body or "")
            if m:
                track = m.group(1)
            rows.append(step("S6 åˆ¤å·", 0 if c in (0, 10) else c,
                             f"{'æœ‰åé¢' if c == 0 else ('æ— åé¢' if c == 10 else 'å¼‚å¸¸')} "
                             f"TrackId={track or '-'}", time.time() - t0))
        except Exception as e:
            rows.append(step("S6 åˆ¤å·", 20, f"{type(e).__name__}", time.time() - t0))

    # ---------------- S7
    t0 = time.time()
    if not cookies and not released:
        rows.append(step("S7 åé¢æ¦‚è§ˆ", -1, "æ²¡æœ‰ä¼šè¯ cookieï¼Œè·³è¿‡", time.time() - t0))
    else:
        try:
            st = sp.booking_status()
            err = st.get("error")
            ok = (not err) and (st.get("rows") or 0) > 0 and len(st.get("offices") or []) > 0
            rows.append(step("S7 åé¢æ¦‚è§ˆ", 0 if ok else (err or 20),
                             f"offices={len(st.get('offices') or [])} "
                             f"rows={st.get('rows')} free={len(st.get('free') or [])} "
                             f"æ›´æ–°äºŽ {st.get('updated') or '-'}", time.time() - t0))
        except Exception as e:
            rows.append(step("S7 åé¢æ¦‚è§ˆ", 20, f"{type(e).__name__}", time.time() - t0))

    fails = [r for r in rows if r["status"] == "FAIL"]
    if fails:
        return fails[0]["code"] or 20, rows
    if any(r["status"] == "SKIP" for r in rows):
        return 42, rows
    return 0, rows


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--cookies", default="")
    ap.add_argument("--worker-id", default="w1")
    ap.add_argument("--loop", type=float, default=0, help="æ¯è½®é—´éš”ç§’ï¼ˆ0=åªè·‘ä¸€æ¬¡ï¼‰")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg = {}
    p = BASE / "config.json"
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    app = next((x for x in cfg.get("applicants", []) if x.get("id") == a.worker_id), None)

    all_rows = []
    code = 0
    for i in range(1, max(1, a.rounds) + 1):
        print(f"\n===== ç¬¬ {i}/{a.rounds} è½®  {time.strftime('%H:%M:%S')} "
              f"proxy={a.proxy or 'ç›´è¿ž'} cookie={'æœ‰' if a.cookies else 'æ— '} =====")
        code, rows = run_once(a.proxy, a.cookies, app)
        all_rows.append({"round": i, "at": time.strftime("%H:%M:%S"),
                         "exit": code, "rows": rows})
        print(f"  â†’ æœ¬è½®ç»“è®ºï¼šexit={code}")
        if code not in (0, 42):
            break
        if i < a.rounds:
            time.sleep(a.loop)

    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "proxy": a.proxy,
              "cookies": bool(a.cookies), "exit": code, "rounds": all_rows}
    (OUT / f"verify-{time.strftime('%H%M%S')}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
