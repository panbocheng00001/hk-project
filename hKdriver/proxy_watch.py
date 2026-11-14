# -*- coding: utf-8 -*-
"""proxy_watch.py —— 代理长跑监视：出口 IP 会不会变、会不会掉
=================================================================================
先说清楚「有效期」能被观察到的是什么：
    代理服务商口中的"有效期/时长"通常指**这条通道还给你用多久**，我们看不到它的后台计时。
    能测的、也是真正影响抢号的是这两件事——
      ① 出口 IP 是否一直不变（WAF 票绑出口 IP，一变票就废）
      ② 通道是否一直可用（掉线 / 407 / 超时都算失效）
    所以这里按固定间隔取样，记录 egress / 延迟 / 成败，用「首次变 IP 的时刻」和
    「连续可用时长」来回答"是不是能用一小时"。

用法：
    python proxy_watch.py --proxy http://222.216.120.212:45792 --minutes 75
    python proxy_watch.py --proxy http://... --interval 60 --minutes 90 --out runs/proxy_watch.jsonl

    # 随时看进度（另一个窗口跑）
    python proxy_watch.py --report

产出：
    runs/proxy_watch.jsonl      一行一次取样
    runs/proxy_watch.log        人读时间线
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "runs" / "proxy_watch.jsonl"
LOG = BASE / "runs" / "proxy_watch.log"

PROBE = "https://ipinfo.io/json"


def _curl():
    try:
        import curl_cffi  # noqa: F401
    except Exception:
        v = str(BASE / "vendor")
        if Path(v).exists() and v not in sys.path:
            sys.path.insert(0, v)
    from curl_cffi import requests as cr
    return cr


def sample(proxy: str, timeout: float = 25.0) -> dict:
    cr = _curl()
    px = {"http": proxy, "https": proxy}
    t0 = time.time()
    row = {"ts": round(time.time(), 3),
           "iso": datetime.now().isoformat(timespec="seconds"),
           "proxy": proxy}
    try:
        r = cr.get(PROBE, proxies=px, timeout=timeout, verify=False, impersonate="chrome")
        d = r.json()
        row.update(ok=True, code=r.status_code, ip=d.get("ip"),
                   country=d.get("country"), city=d.get("city"),
                   org=str(d.get("org") or "")[:40],
                   ms=int((time.time() - t0) * 1000))
    except Exception as exc:
        msg = f"{type(exc).__name__}: {str(exc)[:90]}"
        row.update(ok=False, ms=int((time.time() - t0) * 1000), err=msg)
        low = msg.lower()
        if "407" in low:
            row["kind"] = "auth"
        elif "timeout" in low or "timed out" in low:
            row["kind"] = "timeout"
        elif "refused" in low or "connect" in low:
            row["kind"] = "conn"
        else:
            row["kind"] = "other"
    return row


def append(row: dict, out: Path, log: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    flag = "✅" if row.get("ok") else "❌"
    line = (f'{row["iso"][11:19]} {flag} ip={row.get("ip") or "-":<16} '
            f'{row.get("country") or "":<3} {row.get("city") or "":<10} '
            f'{row.get("ms") or 0:>5}ms  {row.get("err") or ""}')
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("ok")]
    ips = []
    first_switch = None
    t_first = None
    for r in rows:
        if not r.get("ok"):
            continue
        if t_first is None:
            t_first = r["ts"]
        if not ips or ips[-1] != r["ip"]:
            if ips and first_switch is None:
                first_switch = r["ts"] - t_first
            ips.append(r["ip"])
    return {
        "n": len(rows),
        "ok": len(ok),
        "uptime": (len(ok) / len(rows) * 100) if rows else 0,
        "distinct": len({r.get("ip") for r in ok}),
        "first_switch_min": round(first_switch / 60, 1) if first_switch else None,
        "span_min": round((rows[-1]["ts"] - rows[0]["ts"]) / 60, 1) if len(rows) > 1 else 0,
        "fails": [r for r in rows if not r.get("ok")],
    }


def cmd_report(a) -> int:
    out = Path(a.out)
    if not out.exists():
        print(f"还没有取样文件：{out}")
        return 1
    rows = [json.loads(ln) for ln in out.open(encoding="utf-8") if ln.strip()]
    s = summarize(rows)
    print("=" * 74)
    print(f"取样 {s['n']} 次，成功 {s['ok']}（可用率 {s['uptime']:.1f}%），"
          f"跨度 {s['span_min']} 分钟")
    print(f"不同出口 IP：{s['distinct']} 个"
          + (f"，首次变化在第 {s['first_switch_min']} 分钟" if s["first_switch_min"] else "，全程没变"))
    if s["fails"]:
        print(f"\n失败 {len(s['fails'])} 次：")
        for r in s["fails"][:8]:
            print(f"  {r['iso'][11:19]}  {r.get('kind')}  {(r.get('err') or '')[:80]}")
    # 结论
    if s["n"] < 3:
        print("\n样本太少，先让它多跑一会儿")
    elif s["uptime"] >= 99 and s["distinct"] <= 1 and s["span_min"] >= 55:
        print(f"\n结论：跨 {s['span_min']:.0f} 分钟全程可用且出口没变 —— 支撑「一小时有效」")
    elif s["distinct"] <= 1 and s["uptime"] >= 99 and s["span_min"] < 55:
        print(f"\n结论：目前 {s['span_min']:.0f} 分钟 100% 可用、出口没变（样本还不够回答\"能不能撑一小时\"，继续观察）")
    elif s["distinct"] > 1:
        print(f"\n结论：出口在第 {s['first_switch_min']} 分钟换过 —— 票会作废，必须绑定「换 IP 就重铸」")
    else:
        print("\n结论：见上面失败明细")
    print("=" * 74)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="http://222.216.120.212:45792")
    ap.add_argument("--interval", type=float, default=60.0, help="取样间隔（秒）")
    ap.add_argument("--minutes", type=float, default=75.0, help="总共监视多久（分钟）")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", action="store_true", help="只看已有结果")
    a = ap.parse_args()
    if a.report:
        return cmd_report(a)

    out, log = Path(a.out), LOG
    deadline = time.time() + a.minutes * 60
    print(f"开始监视 {a.proxy}：每 {a.interval:.0f}s 一次，共 {a.minutes:.0f} 分钟", flush=True)
    print(f"结果实时写入 {out} 与 {log}；随时另开窗口跑 --report", flush=True)
    rows = []
    while time.time() < deadline:
        t0 = time.time()
        row = sample(a.proxy)
        rows.append(row)
        append(row, out, log)
        s = summarize(rows)
        print(f"   └ 累计 {s['n']} 次，可用率 {s['uptime']:.0f}%，"
              f"出口 {s['distinct']} 个"
              + (f"，第 {s['first_switch_min']} 分钟变过" if s['first_switch_min'] else "")
              + "；Ctrl+C 可提前结束", flush=True)
        sleep = a.interval - (time.time() - t0)
        if sleep > 0:
            time.sleep(sleep)
    cmd_report(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
