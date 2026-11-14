#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_flow.py —— 单 IP 全链路逐步骤自检（专门留一台机器验脚本，不抢号）
===========================================================================
把整条链路拆成 7 个可单独断言的点，每一步都给 PASS / FAIL / SKIP + 耗时 + 证据：

  S1 WAF 落地      abs3/?lang=sc                       断言 200 且下发 HWWAFSESID
  S2 排队发现      abs3/SelectCountry                  断言 302 且 Location 含 c/e/t
  S3 取号          abs4/spa-api/queue/…/enqueue        断言 200 且返回 queueId
  S4 排队状态      abs4/spa-api/queue/…/{id}/status    断言 200 且含 QueueState/forecastStatus
  S5 进业务        {t}                                 需放行 / 需会话，否则 SKIP
  S6 判号          AdminServlet_<lang>?cmd=cmdGetOfficeQuotaDIAction
                                                       断言 200 且能判出「有名额/无名额」+ TrackId
  S7 名额概览      cmdGetOfficeBookingStatusAction     断言 200 且解析出办事处与时段行

用法
----
    # 只验 ①~④（不需要会话，随时可跑）
    python verify_flow.py

    # 带上浏览器导出的 cookie，连 ⑤~⑦ 一起验
    python verify_flow.py --cookies runs/rev/browser_cookies.json

    # 走代理验（跟正式抢号同一条链路）
    python verify_flow.py --cookies runs/rev/browser_cookies.json --proxy http://ip:port

    # 连续验（每 60 秒一轮，最多 30 轮；任何一步退化立刻报）
    python verify_flow.py --cookies ... --loop 60 --rounds 30

退出码：0 全通 / 42 链路通但未放行（S5~S7 SKIP） / 其他 = 第一个失败步骤的码
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sniper import Sniper, Log, STEP1_ENTRY  # noqa: E402

BASE = Path(__file__).resolve().parent
OUT = BASE / "runs" / "verify"
OUT.mkdir(parents=True, exist_ok=True)


def step(name: str, code: int, detail: str, sec: float) -> dict:
    status = "PASS" if code == 0 else ("SKIP" if code == -1 else "FAIL")
    icon = {"PASS": "√", "FAIL": "×", "SKIP": "-"}[status]
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
        r = sp._get("https://abs3.td.gov.hk/?lang=sc", allow_redirects=True)
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
        r = sp._get("https://abs3.td.gov.hk/SelectCountry?"
                    "lang=sc&actionType=apply&authMethod=withOutIas",
                    allow_redirects=False)
        loc = r.headers.get("Location", "")
        c = sp.classify(r)
        if r.status_code == 200:
            rows.append(step("S2 排队发现", 0, "200（已在放行态，跳过排队）", time.time() - t0))
            sp.ctx.target = "https://abs3.td.gov.hk/SelectCountry?" \
                            "lang=sc&actionType=apply&authMethod=withOutIas"
            released = True
        elif r.status_code in (301, 302, 303):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(loc).query)
            sp.ctx.customer = (q.get("c") or [""])[0]
            sp.ctx.event = (q.get("e") or [""])[0]
            sp.ctx.target = (q.get("t") or [""])[0]
            ok = bool(sp.ctx.customer and sp.ctx.event)
            rows.append(step("S2 排队发现", 0 if ok else 20,
                             f"302 c={sp.ctx.customer} e={sp.ctx.event}", time.time() - t0))
            released = False
            if not ok:
                return 20, rows
        else:
            rows.append(step("S2 排队发现", c or 20, f"{r.status_code}", time.time() - t0))
            return c or 20, rows
    except Exception as e:
        rows.append(step("S2 排队发现", 20, f"{type(e).__name__}", time.time() - t0))
        return 20, rows

    # ---------------- S3
    if not released:
        t0 = time.time()
        try:
            c = sp.enqueue()
            rows.append(step("S3 取号", c,
                             f"queueId={sp.ctx.queue_id or '-'}", time.time() - t0))
            if c:
                return c, rows
        except Exception as e:
            rows.append(step("S3 取号", 20, f"{type(e).__name__}", time.time() - t0))
            return 20, rows

        # ---------------- S4
        t0 = time.time()
        try:
            c, st = sp.status(1)
            ok = (c == 0 and st.queue_state is not None)
            rows.append(step("S4 排队状态", 0 if ok else (c or 20),
                             f"state={st.queue_state} forecast={st.forecast} "
                             f"eta={st.expected_utc}", time.time() - t0))
            if st.released:
                released = True
            if not ok:
                return c or 20, rows
        except Exception as e:
            rows.append(step("S4 排队状态", 20, f"{type(e).__name__}", time.time() - t0))
            return 20, rows

    # ---------------- S5
    t0 = time.time()
    if not released:
        rows.append(step("S5 进业务", -1, "未放行，跳过（等 17:00 洗牌后再验）", time.time() - t0))
    else:
        try:
            c = sp.enter()
            rows.append(step("S5 进业务", c, "业务页已落盘", time.time() - t0))
        except Exception as e:
            rows.append(step("S5 进业务", 20, f"{type(e).__name__}", time.time() - t0))

    # ---------------- S6
    t0 = time.time()
    if not cookies and not released:
        rows.append(step("S6 判号", -1, "没有会话 cookie，跳过", time.time() - t0))
    else:
        try:
            c, body = sp.step1(app=app)
            track = ""
            import re
            m = re.search(r"TrackId:\s*([0-9\-a-f]+)", body or "")
            if m:
                track = m.group(1)
            rows.append(step("S6 判号", 0 if c in (0, 10) else c,
                             f"{'有名额' if c == 0 else ('无名额' if c == 10 else '异常')} "
                             f"TrackId={track or '-'}", time.time() - t0))
        except Exception as e:
            rows.append(step("S6 判号", 20, f"{type(e).__name__}", time.time() - t0))

    # ---------------- S7
    t0 = time.time()
    if not cookies and not released:
        rows.append(step("S7 名额概览", -1, "没有会话 cookie，跳过", time.time() - t0))
    else:
        try:
            st = sp.booking_status()
            err = st.get("error")
            ok = (not err) and (st.get("rows") or 0) > 0 and len(st.get("offices") or []) > 0
            rows.append(step("S7 名额概览", 0 if ok else (err or 20),
                             f"offices={len(st.get('offices') or [])} "
                             f"rows={st.get('rows')} free={len(st.get('free') or [])} "
                             f"更新于 {st.get('updated') or '-'}", time.time() - t0))
        except Exception as e:
            rows.append(step("S7 名额概览", 20, f"{type(e).__name__}", time.time() - t0))

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
    ap.add_argument("--loop", type=float, default=0, help="每轮间隔秒（0=只跑一次）")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg = {}
    p = BASE / "config.json"
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8"))
    app = next((x for x in cfg.get("applicants", []) if x.get("id") == a.worker_id), None)

    all_rows = []
    code = 0
    for i in range(1, max(1, a.rounds) + 1):
        print(f"\n===== 第 {i}/{a.rounds} 轮  {time.strftime('%H:%M:%S')} "
              f"proxy={a.proxy or '直连'} cookie={'有' if a.cookies else '无'} =====")
        code, rows = run_once(a.proxy, a.cookies, app)
        all_rows.append({"round": i, "at": time.strftime("%H:%M:%S"),
                         "exit": code, "rows": rows})
        print(f"  → 本轮结论：exit={code}")
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
