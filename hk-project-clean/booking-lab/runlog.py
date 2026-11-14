# -*- coding: utf-8 -*-
"""runlog.py —— 逐步回放与分析（每一步：状态 / DOM / 接口 / 接口返回）
================================================================================
数据从哪来：
    runs/runlog/<日期>/<序号>_<时分秒>_<窗口>_<标签>/
        meta.json    状态：stage / url / title / 可见文本 / 全部表单控件值 /
                     可点元素 / iframe / 滚动位置 / cookie 名 / 截图与 DOM 的尺寸
        dom.html     当时的整页 HTML（阶段级 + 关键动作）
        shot.png     当时的画面
    runs/net/net.jsonl      netrec 钩到的业务请求：表单字段 / XHR / fetch + **响应体**
    runs/netlog.jsonl       webRequest 看到的每个请求 + 状态码（含页面导航）

用法：
    python runlog.py --list                    # 今天每一步一行
    python runlog.py --step 7                  # 第 7 步的全部细节 + 当时前后的接口
    python runlog.py --step 7 --raw            # 连响应体一起打印
    python runlog.py --api                     # 今天所有接口汇总（method/path/状态/大小）
    python runlog.py --report                  # 生成 runs/runlog/report_<日期>.md
    python runlog.py --day 2026-11-12 --list
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNLOG = BASE / "runs" / "runlog"
NETREC = BASE / "runs" / "net" / "net.jsonl"     # netrec（请求字段 + 响应体）
NETLOG = BASE / "runs" / "netlog.jsonl"          # webRequest（请求 + 状态码）

WIN_BEFORE = 3000      # 该步之前多少毫秒内的接口算「本步触发」
WIN_AFTER = 6000       # 之后多少毫秒算「本步的结果」


# ---------------------------------------------------------------- 读取
def _jsonl(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    for ln in p.open("r", encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def load_steps(day: str) -> list[dict]:
    d = RUNLOG / day
    if not d.exists():
        return []
    out = []
    for p in sorted([x for x in d.iterdir() if x.is_dir()]):
        meta = {}
        try:
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        out.append({
            "dir": p, "name": p.name, "meta": meta,
            "dom": p / "dom.html", "shot": p / "shot.png",
        })
    return out


def _ts(step: dict) -> int:
    m = step["meta"]
    if isinstance(m.get("ts"), (int, float)):
        return int(m["ts"])
    # 兜底：从目录名 012_231526 里解析
    try:
        hms = step["name"].split("_")[1]
        t = datetime.now().replace(hour=int(hms[0:2]), minute=int(hms[2:4]),
                                   second=int(hms[4:6]), microsecond=0)
        return int(t.timestamp() * 1000)
    except Exception:
        return 0


def nets_for(step: dict) -> list[dict]:
    t = _ts(step)
    if not t:
        return []
    lo, hi = t - WIN_BEFORE, t + WIN_AFTER
    rows = [r for r in _jsonl(NETREC)
            if isinstance(r.get("ts"), (int, float)) and lo <= r["ts"] <= hi]
    return sorted(rows, key=lambda r: r.get("ts") or 0)


def status_codes_for(step: dict) -> list[dict]:
    """webRequest 的 done / hdr 记录（页面导航也能看到状态码与响应头）。"""
    t = _ts(step)
    if not t:
        return []
    lo, hi = t - WIN_BEFORE, t + WIN_AFTER
    out = []
    for r in _jsonl(NETLOG):
        if r.get("ph") not in ("done", "hdr"):
            continue
        ts = r.get("t")
        if not isinstance(ts, (int, float)):
            continue
        ts = int(ts if ts > 1e12 else ts * 1000)
        if lo <= ts <= hi:
            out.append(r)
    return out


# ---------------------------------------------------------------- 展示
def _clip(s, n=160) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\n", " ").replace("\r", " ")
    return s[:n] + ("…" if len(s) > n else "")


def _path_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        return u.path + ("?…" if u.query else "")
    except Exception:
        return url


def cmd_list(a) -> int:
    steps = load_steps(a.day)
    if not steps:
        print(f"{a.day} 还没有步骤记录（先跑一轮：run_local.ps1）")
        return 1
    print(f"{'#':<4}{'时刻':<9}{'阶段':<12}{'动作':<26}{'控件':>4} {'DOM':>4} {'图':>3}  URL / 文本")
    print("-" * 118)
    for i, s in enumerate(steps, 1):
        m = s["meta"]
        hms = s["name"].split("_")[1]
        hms = f"{hms[0:2]}:{hms[2:4]}:{hms[4:6]}"
        print(f"{i:<4}{hms:<9}{str(m.get('stage') or '-'):<12}"
              f"{str(m.get('step') or '-'):<26}"
              f"{len(m.get('controls') or []):>4} "
              f"{'有' if s['dom'].exists() else '-':>4} "
              f"{'有' if s['shot'].exists() else '-':>3}  "
              f"{_clip(m.get('url') or '', 70)}")
        txt = _clip(m.get("text") or "", 70)
        if txt:
            print(f"    └ {txt}")
    print("-" * 118)
    print(f"共 {len(steps)} 步  目录：{RUNLOG / a.day}")
    return 0


def cmd_step(a) -> int:
    steps = load_steps(a.day)
    if not steps:
        print(f"{a.day} 还没有步骤记录")
        return 1
    n = a.step
    if n < 1 or n > len(steps):
        print(f"步骤序号超范围：1..{len(steps)}")
        return 1
    s = steps[n - 1]
    m = s["meta"]
    print("=" * 100)
    print(f"第 {n} 步  {s['name']}")
    print("=" * 100)
    print(f"阶段      : {m.get('stage')}")
    print(f"动作      : {m.get('step')}  (stepNo={m.get('stepNo')})")
    print(f"时刻      : {m.get('savedAt')}")
    print(f"URL       : {m.get('url')}")
    print(f"标题      : {m.get('title')}")
    print(f"视图      : {m.get('viewport')}  滚动={m.get('scroll')}  正文={m.get('textLen')} 字")
    print(f"文件      : {m.get('dir')}")
    print(f"            dom={'有' if s['dom'].exists() else '无'}  "
          f"shot={'有' if s['shot'].exists() else '无'}  "
          f"domBytes={m.get('domBytes')} shotBytes={m.get('shotBytes')}")

    ck = m.get("cookies") or []
    print(f"cookie    : {len(ck)} 条  {', '.join(sorted({c.get('name', '') for c in ck}))[:100]}")

    txt = _clip(m.get("text") or "", 1200)
    if txt:
        print("\n-- 页面文本 --")
        print(txt)

    ctl = m.get("controls") or []
    if ctl:
        print(f"\n-- 表单控件（{len(ctl)}）--")
        for c in ctl[:60]:
            flag = "√" if c.get("visible") else "×"
            chk = "" if c.get("checked") in (None, False) else " [checked]"
            print(f"  {flag} {c.get('tag')}/{c.get('type'):<8} name={c.get('name')!r:<34}"
                  f" value={_clip(c.get('value'), 60)!r}{chk}")

    itv = m.get("interactive") or []
    if itv:
        print(f"\n-- 可点元素（{len(itv)}，前 25）--")
        for c in itv[:25]:
            print(f"  {c.get('tag'):<8} {_clip(c.get('text'), 50)!r}")

    ifr = m.get("iframes") or []
    if ifr:
        print(f"\n-- iframe（{len(ifr)}）--")
        for f in ifr:
            print(f"  {f.get('w')}x{f.get('h')}  {_clip(f.get('src'), 90)}")

    rows = nets_for(s)
    if rows:
        print(f"\n-- 本步前后的接口（{len(rows)} 条）--")
        for r in rows:
            kind = r.get("kind") or "?"
            if kind.endswith("_resp"):
                print(f"  ← {kind:<10} {r.get('status')} {_clip(r.get('from'), 70)}"
                      f"  len={r.get('len')}")
                if a.raw:
                    print("      " + _clip(r.get("body"), 800))
            else:
                flds = r.get("fields") or []
                sample = ", ".join(f"{k}={_clip(v, 24)}" for k, v in flds[:8])
                print(f"  → {kind:<10} {r.get('method')} {_clip(r.get('url'), 70)}")
                if sample:
                    print(f"      字段({len(flds)}): {_clip(sample, 150)}")
    codes = status_codes_for(s)
    # 默认只看「会有信息量」的请求：页面导航 / XHR / fetch（静态资源太多，淹没重点）
    if not a.all_http:
        keep = {"main_frame", "xmlhttprequest", "fetch", "other"}
        codes = [r for r in codes if (r.get("type") or "") in keep]
    # 同一 url+type 只留一条，优先 hdr（它带 content-type / length）
    merged = {}
    for r in codes:
        key = (r.get("url"), r.get("type"))
        if key not in merged or r.get("ph") == "hdr":
            merged[key] = r
    codes = list(merged.values())
    if codes:
        print(f"\n-- 同时段 HTTP（webRequest，{len(codes)}）--")
        for r in codes[:24]:
            hdr = r.get("hdr") or {}
            extra = ""
            if hdr:
                extra = "  " + " ".join(f"{k}={_clip(v, 34)}" for k, v in list(hdr.items())[:3])
            print(f"  {r.get('ph')} {r.get('s')}  {(r.get('type') or '-'):<12} "
                  f"{_clip(r.get('url'), 72)}{extra}")
    print()
    return 0


def _day_bounds(day: str):
    try:
        d0 = datetime.strptime(day, "%Y-%m-%d")
    except Exception:
        d0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(d0.timestamp() * 1000)
    return t0, t0 + 86400 * 1000


def cmd_api(a) -> int:
    t0, t1 = _day_bounds(a.day)
    rows = [r for r in _jsonl(NETREC)
            if isinstance(r.get("ts"), (int, float)) and t0 <= r["ts"] < t1]
    logs = [r for r in _jsonl(NETLOG)
            if isinstance(r.get("t"), (int, float))
            and t0 <= (r["t"] if r["t"] > 1e12 else r["t"] * 1000) < t1]
    if not rows and not logs:
        print("没有接口记录（runs/net/net.jsonl 与 runs/netlog.jsonl 都是空的）")
        return 1
    from collections import defaultdict
    agg = defaultdict(lambda: {"codes": defaultdict(int), "len": 0, "sample": "",
                               "req": 0, "resp": 0})
    for r in rows:
        kind = r.get("kind") or "?"
        url = r.get("url") or r.get("from") or ""
        if not url:
            continue
        method = (r.get("method") or "GET").upper()
        key = f"{method} {_path_of(url)}"
        it = agg[key]
        if kind.endswith("_resp"):
            it["resp"] += 1
            it["codes"][r.get("status")] += 1
            it["len"] = max(it["len"], int(r.get("len") or 0))
            if not it["sample"]:
                it["sample"] = "← " + _clip(r.get("body"), 120)
        else:
            it["req"] += 1
            flds = r.get("fields") or []
            if flds and not it["sample"]:
                it["sample"] = "→ " + _clip(", ".join(f"{k}={v}" for k, v in flds[:6]), 120)
            elif r.get("body") and not it["sample"]:
                it["sample"] = "→ " + _clip(r.get("body"), 120)

    print("== 业务接口（netrec：表单字段 / XHR / fetch 响应体）==")
    if not agg:
        print("  （没有钩到的业务请求）")
    else:
        print(f"{'方法':<6}{'请求':>4}{'响应':>4}  {'状态':<12}{'最大响应':>9}  路径")
        print("-" * 118)
        for key, it in sorted(agg.items(), key=lambda kv: -(kv[1]["req"] + kv[1]["resp"])):
            method, path = key.split(" ", 1)
            codes = ",".join(f"{k}×{v}" for k, v in
                             sorted(it["codes"].items(), key=lambda x: -x[1])[:4])
            print(f"{method:<6}{it['req']:>4}{it['resp']:>4}  {codes:<12}{it['len']:>9}  {path}")
            if it["sample"]:
                print(f"    └ {it['sample']}")

    # 全站请求：webRequest 看到的一切（含页面导航/静态资源），带状态码与 content-type
    print("\n== 全站请求 + 返回（webRequest：状态码 / content-type）==")
    if not logs:
        print("  （没有 webRequest 记录）")
        return 0
    allg = defaultdict(lambda: {"n": 0, "codes": defaultdict(int), "types": set(), "ctype": ""})
    for r in logs:
        if r.get("ph") != "done":
            continue
        url = r.get("url") or ""
        if not url:
            continue
        it = allg[_path_of(url)]
        it["n"] += 1
        it["codes"][r.get("s")] += 1
        if r.get("type"):
            it["types"].add(r["type"])
    for r in logs:
        if r.get("ph") != "hdr":
            continue
        h = r.get("hdr") or {}
        k = _path_of(r.get("url") or "")
        if k in allg and h.get("content-type"):
            allg[k]["ctype"] = h["content-type"]

    print(f"{'次数':>4}  {'状态':<10}{'类型':<16}{'content-type':<34}路径")
    print("-" * 118)
    for key, it in sorted(allg.items(), key=lambda kv: -kv[1]["n"]):
        codes = ",".join(f"{k}×{v}" for k, v in sorted(it["codes"].items(), key=lambda x: -x[1])[:3])
        types = "/".join(sorted(it["types"]))[:15]
        print(f"{it['n']:>4}  {codes:<10}{types:<16}{_clip(it['ctype'], 32):<34}{key}")
    print("-" * 118)
    print(f"业务接口 {len(agg)} 个；全站路径 {len(allg)} 个"
          f"（runs/net/net.jsonl + runs/netlog.jsonl）")
    return 0


def cmd_report(a) -> int:
    steps = load_steps(a.day)
    if not steps:
        print(f"{a.day} 还没有步骤记录")
        return 1
    out = RUNLOG / f"report_{a.day}.md"
    L = [f"# {a.day} 逐步回放", "",
         f"共 **{len(steps)}** 步。每步包含：状态 / 表单控件 / 页面文本 / 当时接口 / 接口返回 / 截图。",
         "",
         "| # | 时刻 | 阶段 | 动作 | 控件 | DOM | 图 |",
         "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(steps, 1):
        m = s["meta"]
        hms = s["name"].split("_")[1]
        L.append(f"| {i} | {hms[0:2]}:{hms[2:4]}:{hms[4:6]} | {m.get('stage')} | "
                 f"{_clip(m.get('step'), 30)} | {len(m.get('controls') or [])} | "
                 f"{'有' if s['dom'].exists() else '-'} | {'有' if s['shot'].exists() else '-'} |")
    L.append("")

    for i, s in enumerate(steps, 1):
        m = s["meta"]
        L += [f"## {i}. {m.get('step') or m.get('stage')}", "",
              f"- 时刻：`{m.get('savedAt')}`　阶段：`{m.get('stage')}`　stepNo={m.get('stepNo')}",
              f"- URL：`{_clip(m.get('url'), 160)}`",
              f"- 标题：{_clip(m.get('title'), 80)}　正文：{m.get('textLen')} 字　"
              f"视图：{m.get('viewport')}　滚动：{m.get('scroll')}",
              f"- cookie：{len(m.get('cookies') or [])} 条　"
              f"DOM：{m.get('domBytes') or 0} 字节　截图：{m.get('shotBytes') or 0} 字节",
              ""]
        # 控件
        ctl = [c for c in (m.get("controls") or []) if c.get("visible") or c.get("value")]
        if ctl:
            L += ["**表单控件**", "", "| 标签 | 名称 | 类型 | 值 |", "|---|---|---|---|"]
            for c in ctl[:40]:
                L.append(f"| {_clip(c.get('label'), 24)} | `{c.get('name')}` | {c.get('type')} | "
                         f"`{_clip(c.get('value'), 60)}` |")
            L.append("")
        # 文本
        txt = _clip(m.get("text") or "", 600)
        if txt:
            L += ["**页面文本**", "", "```", txt, "```", ""]
        # 接口
        rows = nets_for(s)
        if rows:
            L += ["**本步接口**", "", "```"]
            for r in rows[:12]:
                kind = r.get("kind") or "?"
                if kind.endswith("_resp"):
                    L.append(f"← {r.get('status')} {_clip(r.get('from'), 100)}  len={r.get('len')}")
                else:
                    flds = r.get("fields") or []
                    L.append(f"→ {r.get('method')} {_clip(r.get('url'), 100)}")
                    if flds:
                        L.append("   字段: " + _clip(", ".join(f"{k}={v}" for k, v in flds[:8]), 200))
            L += ["```", ""]
        # 截图
        if s["shot"].exists():
            rel = s["shot"].relative_to(BASE).as_posix()
            L += [f"![step {i}](<{rel}>)", ""]
        if s["dom"].exists():
            rel = s["dom"].relative_to(BASE).as_posix()
            L += [f"DOM：[{rel}](<{rel}>)", ""]

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成 → {out}（{len(steps)} 步）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="逐步回放：状态 / DOM / 接口 / 返回")
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--raw", action="store_true", help="--step 时连响应体一起打印")
    ap.add_argument("--all-http", action="store_true",
                    help="--step 时连静态资源请求一起列（默认只看导航/XHR/fetch）")
    ap.add_argument("--api", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.list:
        return cmd_list(a)
    if a.step:
        return cmd_step(a)
    if a.api:
        return cmd_api(a)
    if a.report:
        return cmd_report(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
