#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pool_goto.py —— 明天的进池器（单出口 + 多 context + 只走 goto）
===========================================================================
今天（2026-11-11）验证出的三条硬结论，全部体现在这里：

1. **代理段被判死**：10 个代理 curl 全 412；换浏览器走同一代理 → 412 + 39 字节空壳 +
   `HIK0kat3rt9xO`/`xP` 并存。所以这里**不用代理**，只用本机出口。
2. **点击后导航必空壳**：abs3 里点「继续」→ SelectCountry 返回 <html><head></head><body></body></html>。
   所以这里**只用 goto**：先 goto abs3 过 WAF，再 goto abs4 排队 URL。
3. **家宽会被限速**：疯狂重试会让出口从 200 → 412 → 000。所以这里**context 之间强制间隔**，
   且第一次失败就退避，不连环打。

用法
----
    # 开 3 条（同一出口，独立 cookie），各自挂到 Queue-it 排队页
    python pool_goto.py 3

    # 只看一条能不能过（不挂队列）
    python pool_goto.py 1 --check-only

产物
----
    runs/fleet/pool_state.json   每条 context 的状态（过没过 WAF / 停在哪个 URL / cookie 数）
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
OUT = BASE / "runs" / "fleet"
OUT.mkdir(parents=True, exist_ok=True)

CDP = "http://127.0.0.1:9223"
ABS3 = "https://abs3.td.gov.hk/?lang=sc"
EVENT = "tdabsd261110kla"          # 每天会变，正式跑前用 sniper --probe 重新发现
QUEUE = ("https://abs4.td.gov.hk/?c=tdabsditest&e=" + EVENT +
         "&ver=v3-lua-3.7.0&kupver=nginx-1029002&cver=148"
         "&man=PRD_TDABS3_DI_SC&cid=zh-CN"
         "&t=http%3A%2F%2Fabs3.td.gov.hk%2FSelectCountry"
         "%3Flang%3Dsc%26actionType%3Dapply%26authMethod%3DwithOutIas")

STATE: list[dict] = []


def log(tag: str, msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [{tag}] {msg}", flush=True)


def save_state() -> None:
    (OUT / "pool_state.json").write_text(
        json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")


def warm_waf(ctx, tag: str, wait_ms: int = 9000) -> bool:
    """① goto abs3 过 WAF。判据：200 且页面里有 conditionsCheck（不是 $_ts 挑战页）。"""
    pg = ctx.new_page()
    t0 = time.time()
    try:
        r = pg.goto(ABS3, wait_until="commit", timeout=45000)
        st = r.status if r else "?"
    except Exception as e:
        log(tag, f"abs3 goto 异常 {type(e).__name__}: {str(e)[:80]}")
        return False
    pg.wait_for_timeout(wait_ms)
    h = pg.content()
    ok = (st == 200) and ("conditionsCheck" in h) and ("$_ts" not in h[:4000])
    log(tag, f"abs3 status={st} {time.time()-t0:.1f}s len={len(h)} "
             f"conditionsCheck={h.count('conditionsCheck')} → {'过' if ok else '没过'}")
    return ok, pg


def to_queue(ctx, pg, tag: str) -> bool:
    """② goto abs4 排队 URL（绝不点击）。"""
    try:
        r = pg.goto(QUEUE, wait_until="commit", timeout=45000)
        st = r.status if r else "?"
    except Exception as e:
        log(tag, f"abs4 goto 异常 {type(e).__name__}: {str(e)[:80]}")
        return False
    pg.wait_for_timeout(9000)
    h = pg.content()
    good = (st == 200) and ("queue" in h.lower()) and ("$_ts" not in h[:4000])
    log(tag, f"abs4 status={st} len={len(h)} url={pg.url[:90]} → "
             f"{'★ 挂上排队页' if good else '没挂上'}")
    return good


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=3)
    ap.add_argument("--cdp", default=CDP)
    ap.add_argument("--gap", type=float, default=8.0, help="context 之间的间隔秒")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(a.cdp)
        ok = 0
        for i in range(a.n):
            tag = f"C{i+1}"
            log(tag, "开 context（同一出口，独立 cookie）")
            ctx = b.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                no_viewport=True)
            res = warm_waf(ctx, tag)
            if not res:
                STATE.append({"tag": tag, "waf": False})
                save_state()
                time.sleep(a.gap)
                continue
            waf_ok, pg = res
            row = {"tag": tag, "waf": True, "queue": False}
            if not a.check_only:
                row["queue"] = to_queue(ctx, pg, tag)
                if row["queue"]:
                    ok += 1
            STATE.append(row)
            save_state()
            time.sleep(a.gap)
        print(f"\n结果：{ok}/{a.n} 条挂上排队页；状态见 runs/fleet/pool_state.json")
        # 不调 b.close()：浏览器是外部的，context 留着继续排队
    return 0 if ok else 42


if __name__ == "__main__":
    raise SystemExit(main())
