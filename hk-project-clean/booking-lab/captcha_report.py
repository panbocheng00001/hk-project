# -*- coding: utf-8 -*-
"""captcha_report.py —— 验证码人工交接耗时报表（只读 journal）
=====================================================================
数据来源：runs/journal/YYYY-MM-DD.jsonl 里 captcha.py 写入的两类事件
    src=captcha ev=handoff_done   who / kind / waited_s / result / renotify_n / timeout
    src=captcha ev=renotify       who / kind / n / waited_s

用法：
    python captcha_report.py --days 7        # 最近 7 天（含今天）
    python captcha_report.py --selftest      # 用构造事件流校验统计逻辑

口径：
    耗时 = 首次交接 → 人工完成（handoff_done.waited_s），只统计 result=solved 的；
    超时的单独计数，不混进耗时分位数。分位数用线性插值（与 numpy 默认一致）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
JDIR = BASE / "runs" / "journal"


def percentile(xs: list[float], q: float) -> float | None:
    """线性插值分位数，q ∈ [0, 1]。"""
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _day_of(r: dict) -> str:
    iso = str(r.get("iso") or "")
    if len(iso) >= 10:
        return iso[:10]
    ts = _num(r.get("ts"))
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts is not None else "?"


def load(days: int, jdir: Path = JDIR, today: date | None = None) -> list[dict]:
    """读最近 days 天的 captcha 交接事件；文件缺失 / 坏行都跳过。"""
    today = today or date.today()
    rows: list[dict] = []
    for i in range(max(1, days) - 1, -1, -1):
        p = Path(jdir) / f"{today - timedelta(days=i):%Y-%m-%d}.jsonl"
        if not p.is_file():
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(r, dict):
                        rows.append(r)
        except OSError as e:
            print(f"[report] 读取失败，跳过 {p.name}：{e}")
    return [r for r in rows
            if r.get("src") == "captcha" and r.get("ev") in ("handoff_done", "renotify")]


def _group(done: list[dict]) -> dict:
    waits = [w for r in done if r.get("result") == "solved"
             for w in [_num(r.get("waited_s"))] if w is not None]
    n = len(done)
    solved = sum(1 for r in done if r.get("result") == "solved")
    renos = [_num(r.get("renotify_n")) or 0.0 for r in done]
    return {
        "n": n,
        "solved": solved,
        "timeout": sum(1 for r in done if r.get("timeout") is True
                       or r.get("result") == "timeout"),
        "rate": solved / n if n else None,
        "p50": percentile(waits, 0.5),
        "p90": percentile(waits, 0.9),
        "max": max(waits) if waits else None,
        "avg_renotify": sum(renos) / n if n else None,
    }


def summarize(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("src") == "captcha" and r.get("ev") == "handoff_done"]
    by_kind: dict[str, list[dict]] = defaultdict(list)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in done:
        by_kind[str(r.get("kind") or "?")].append(r)
        by_day[_day_of(r)].append(r)
    s = _group(done)
    s["slider"] = len(by_kind.get("slider", []))
    s["renotify_events"] = sum(1 for r in rows
                               if r.get("src") == "captcha" and r.get("ev") == "renotify")
    s["by_kind"] = {k: _group(v) for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]))}
    s["by_day"] = {d: _group(v) for d, v in sorted(by_day.items())}
    return s


def _f(v, fmt: str = "{:.1f}s") -> str:
    return "-" if v is None else fmt.format(v)


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _row(cells: list, widths: tuple[int, ...]) -> str:
    """按终端显示宽度对齐（中文占两格）：第一列左对齐，其余右对齐。"""
    import unicodedata

    def dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

    out = []
    for i, (c, w) in enumerate(zip(cells, widths)):
        c = str(c)
        pad = " " * max(0, w - dw(c))
        out.append(c + pad if i == 0 else pad + c)
    return "".join(out)


_W = (12, 6, 9, 6, 9, 9, 9)


def render(s: dict, days: int) -> str:
    L = [f"验证码人工交接报表（最近 {days} 天）", "=" * 60,
         f"交接总次数      {s['n']}（其中滑块 {s['slider']}）",
         f"人工完成率      {_pct(s['rate'])}（完成 {s['solved']} / 超时 {s['timeout']}）",
         f"完成耗时        P50 {_f(s['p50'])}   P90 {_f(s['p90'])}   max {_f(s['max'])}",
         f"平均提醒次数    {_f(s['avg_renotify'], '{:.2f}')}（renotify 事件 {s['renotify_events']} 条）",
         "", "按类型", "-" * 60,
         _row(["kind", "次数", "完成率", "超时", "P50", "P90", "max"], _W)]
    for k, g in s["by_kind"].items():
        L.append(_row([k, g["n"], _pct(g["rate"]), g["timeout"],
                       _f(g["p50"]), _f(g["p90"]), _f(g["max"])], _W))
    L += ["", "按天趋势", "-" * 60,
          _row(["日期", "次数", "完成率", "超时", "P50", "P90", "均提醒"], _W)]
    for d, g in s["by_day"].items():
        L.append(_row([d, g["n"], _pct(g["rate"]), g["timeout"],
                       _f(g["p50"]), _f(g["p90"]), _f(g["avg_renotify"], "{:.2f}")], _W))
    return "\n".join(L)


# ============================================================================
# 自测
# ============================================================================
def _ev(day: str, ev: str, **f) -> dict:
    return {"iso": f"{day}T10:00:00.000+08:00", "src": "captcha", "ev": ev, **f}


def _fixture() -> list[dict]:
    a, b = "2026-11-10", "2026-11-11"
    return [
        _ev(a, "handoff_done", who="w1", kind="slider", waited_s=10, result="solved",
            renotify_n=0, timeout=False),
        _ev(a, "renotify", who="w1", kind="slider", n=1, waited_s=45),
        _ev(a, "handoff_done", who="w1", kind="slider", waited_s=20, result="solved",
            renotify_n=1, timeout=False),
        _ev(a, "handoff_done", who="w2", kind="image_code", waited_s=40, result="solved",
            renotify_n=0, timeout=False),
        _ev(a, "handoff_done", who="w2", kind="slider", waited_s=180.4, result="timeout",
            renotify_n=2, timeout=True),
        _ev(b, "renotify", who="w1", kind="slider", n=1, waited_s=45),
        _ev(b, "handoff_done", who="w1", kind="slider", waited_s=30, result="solved",
            renotify_n=1, timeout=False),
        _ev(b, "handoff_done", who="w3", kind="unknown", waited_s=181, result="timeout",
            renotify_n=3, timeout=True),
        {"iso": f"{b}T10:00:01.000+08:00", "src": "pool", "ev": "mint", "ok": True},
        _ev(b, "handoff_done", who="w4", kind="slider", waited_s="bad", result="timeout",
            renotify_n=0, timeout=True),
    ]


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    def ok(name: str, got, want, tol: float = 1e-9) -> None:
        if isinstance(want, float) and got is not None:
            passed = abs(got - want) <= tol
        else:
            passed = got == want
        checks.append((f"{name}: got={got!r} want={want!r}", passed))

    ok("percentile 单值", percentile([5.0], 0.5), 5.0)
    ok("percentile 空", percentile([], 0.5), None)
    ok("percentile P90 插值", percentile([4, 1, 3, 2], 0.9), 3.7)
    ok("percentile P50 偶数个", percentile([10, 20, 30, 40], 0.5), 25.0)

    s = summarize(_fixture())
    # 完成耗时 [10, 20, 40, 30] → 排序 [10, 20, 30, 40]
    ok("交接总次数", s["n"], 7)
    ok("滑块次数", s["slider"], 5)
    ok("完成", s["solved"], 4)
    ok("超时", s["timeout"], 3)
    ok("完成率", s["rate"], 4 / 7)
    ok("P50", s["p50"], 25.0)
    ok("P90", s["p90"], 37.0)
    ok("max", s["max"], 40.0)
    ok("平均提醒", s["avg_renotify"], 7 / 7)
    ok("renotify 事件", s["renotify_events"], 2)
    sl = s["by_kind"]["slider"]
    ok("slider 次数", sl["n"], 5)
    ok("slider P50", sl["p50"], 20.0)          # [10, 20, 30]
    ok("slider P90", sl["p90"], 28.0)
    ok("slider 超时", sl["timeout"], 2)
    ok("image_code P50", s["by_kind"]["image_code"]["p50"], 40.0)
    ok("unknown 完成率", s["by_kind"]["unknown"]["rate"], 0.0)
    ok("按天 日期", list(s["by_day"]), ["2026-11-10", "2026-11-11"])
    ok("11-10 P50", s["by_day"]["2026-11-10"]["p50"], 20.0)   # [10, 20, 40]
    ok("11-11 完成率", s["by_day"]["2026-11-11"]["rate"], 1 / 3)

    # 读盘路径：今天往前 7 天的窗口、坏行、窗口外的文件
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rows = _fixture()
        for day in ("2026-11-10", "2026-11-11"):
            lines = [json.dumps(r, ensure_ascii=False) for r in rows if r["iso"].startswith(day)]
            (d / f"{day}.jsonl").write_text("\n".join(lines + ["{坏行", ""]), encoding="utf-8")
        (d / "2026-10-01.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
        got = load(7, d, today=date(2026, 11, 11))
        ok("load 过滤后条数", len(got), 9)
        ok("load 后统计一致", summarize(got)["p90"], 37.0)
        ok("load 窗口=1天", summarize(load(1, d, today=date(2026, 11, 11)))["n"], 3)
        ok("load 空目录", load(7, d / "nope"), [])

    for line, passed in checks:
        print(("  ok   " if passed else "  FAIL ") + line)
    bad = sum(1 for _, p in checks if not p)
    print(f"\n{len(checks) - bad}/{len(checks)} 项通过")
    print("PASS" if not bad else "FAIL")
    return 0 if not bad else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="验证码人工交接耗时报表（只读 journal）")
    ap.add_argument("--days", type=int, default=7, help="最近 N 天（含今天），默认 7")
    ap.add_argument("--dir", default=str(JDIR), help="journal 目录，默认 runs/journal")
    ap.add_argument("--selftest", action="store_true", help="用构造事件流校验统计逻辑")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        rows = load(a.days, Path(a.dir))
        s = summarize(rows)
    except Exception as e:
        print(f"无数据（读取 journal 出错：{type(e).__name__}: {e}）")
        return 0
    if not s["n"]:
        print(f"无数据：{a.dir} 最近 {a.days} 天没有 captcha/handoff_done 记录")
        return 0
    print(render(s, a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
