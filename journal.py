# -*- coding: utf-8 -*-
"""journal.py —— 全链路事件账本（每步都留痕，方便事后分析）
=================================================================================
为什么单独做一个账本，而不是继续往几个 jsonl 里各写各的：
    现在的痕迹是散的 —— 扩展的状态在 runs/ext_status.jsonl，网络在 runs/net/，
    票池只打屏幕，replay 只打屏幕，验证码的 dx 藏在 status 的 msg 里。
    事后想回答一句「17:00:03 到 17:05:10 之间到底发生了什么、时间花在哪」，
    得开四五个文件对时间戳。这个模块把它们并成一条带序号的事件流。

每条事件长这样（一行 JSON，append-only）：

    {"seq": 128, "ts": 1794329xxx.123, "iso": "2026-11-11T10:12:58.123+08:00",
     "pid": 8123, "src": "pool", "ev": "mint",
     "ip_id": "local", "ok": true, "cost": 6.14, "egress": "104.28.155.244"}

落盘位置：
    runs/journal/2026-11-11.jsonl   机器读（一行一条，永不覆盖）
    runs/journal/2026-11-11.log     人读（同样的内容压成一行文本，可直接 tail）

用法（代码里）：
    from journal import J
    J().log("pool", "mint", ip_id="p01", ok=True, cost=3.4, egress="1.2.3.4")

用法（命令行）：
    python journal.py --tail 30              # 最近 30 条
    python journal.py --timeline             # 今天的完整时间线（带 Δt）
    python journal.py --timeline --inst w1   # 只看某个窗口
    python journal.py --stages               # 阶段耗时统计（时间花在哪）
    python journal.py --stats                # 事件计数 / 错误汇总
    python journal.py --grep captcha         # 关键词检索
    python journal.py --report               # 生成 runs/journal/report_YYYY-MM-DD.md
    python journal.py --csv out.csv          # 导 CSV，给 Excel 透视
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
JDIR = BASE / "runs" / "journal"

# 每条事件里出现的"关键字段"顺序 —— 决定人读那行的可读性
BIZ_STAGES = {"entry", "auth", "country", "form", "otp", "calendar", "confirm",
              "queue", "mtcaptcha", "waf_captcha", "recover", "unknown", "done"}

KEY_ORDER = ("ok", "stage", "event", "path", "code", "status", "ms", "cost",
             "ip_id", "egress", "dx", "pairs", "level", "how", "retry",
             "from", "to", "n", "msg", "url", "err")

_SKIP_EMPTY = (None, "", [], {})


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


class Journal:
    """进程内单例；线程安全；每次写都 flush（崩了也不丢）。"""

    def __init__(self, jdir: Path = JDIR, echo: bool = False):
        self.dir = Path(jdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pid = os.getpid()
        self.echo = echo
        self._lock = threading.Lock()
        self._seq = 0
        self._day = ""
        self._fh = None
        self._th = None

    # -- 文件句柄按天切换 --------------------------------------------------
    def _ensure(self, ts: float):
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        if day == self._day and self._fh:
            return
        for fh in (self._fh, self._th):
            try:
                if fh:
                    fh.close()
            except Exception:
                pass
        self._day = day
        self._fh = (self.dir / f"{day}.jsonl").open("a", encoding="utf-8")
        self._th = (self.dir / f"{day}.log").open("a", encoding="utf-8")

    # -- 写一条 ------------------------------------------------------------
    def log(self, src: str, ev: str, **fields) -> dict:
        ts = time.time()
        with self._lock:
            self._ensure(ts)
            self._seq += 1
            rec = {"seq": self._seq, "ts": round(ts, 3), "iso": _now_iso(ts),
                   "pid": self.pid, "src": src, "ev": ev}
            for k, v in fields.items():
                if v in _SKIP_EMPTY:
                    continue
                if isinstance(v, (str, int, float, bool)) or v is None:
                    rec[k] = v
                else:
                    rec[k] = str(v)[:400]
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
            self._th.write(_human(rec) + "\n")
            self._th.flush()
        if self.echo:
            print(_human(rec), flush=True)
        return rec

    def close(self) -> None:
        with self._lock:
            for fh in (self._fh, self._th):
                try:
                    if fh:
                        fh.close()
                except Exception:
                    pass
            self._fh = self._th = None


_J = None
_J_LOCK = threading.Lock()


def J() -> Journal:
    global _J
    if _J is None:
        with _J_LOCK:
            if _J is None:
                _J = Journal()
    return _J


def _human(rec: dict) -> str:
    t = rec["iso"][11:23]
    head = f"{t} [{rec['src']:<6}] {rec['ev']:<12}"
    parts = []
    for k in KEY_ORDER:
        if k in rec and rec[k] not in _SKIP_EMPTY:
            parts.append(f"{k}={rec[k]}")
    for k, v in rec.items():
        if k in ("seq", "ts", "iso", "pid", "src", "ev") or k in KEY_ORDER:
            continue
        parts.append(f"{k}={v}")
    return head + " " + " ".join(parts)


# ============================================================================
# 读侧工具
# ============================================================================
def load_day(day: str = "", jdir: Path = JDIR) -> list[dict]:
    day = day or datetime.now().strftime("%Y-%m-%d")
    p = Path(jdir) / f"{day}.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.open("r", encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _fmt_delta(sec: float) -> str:
    if sec < 60:
        return f"+{sec:5.1f}s"
    m, s = divmod(int(sec), 60)
    if m < 60:
        return f"+{m:2d}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"+{h}h{m:02d}m"


def cmd_tail(a) -> int:
    rows = load_day(a.date)
    for r in rows[-a.tail:]:
        print(_human(r))
    day = a.date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n（今天共 {len(rows)} 条，见 {JDIR / (day + '.jsonl')}）")
    return 0


def _summary(rec: dict) -> str:
    parts = []
    for k in KEY_ORDER:
        if k in rec and rec[k] not in _SKIP_EMPTY and k != "stage":
            parts.append(f"{k}={rec[k]}")
    for k, v in rec.items():
        if k in ("seq", "ts", "iso", "pid", "src", "ev", "stage") or k in KEY_ORDER:
            continue
        parts.append(f"{k}={v}")
    return " ".join(parts)


def cmd_timeline(a) -> int:
    rows = load_day(a.date)
    if a.inst:
        rows = [r for r in rows if r.get("inst") == a.inst or r.get("ip_id") == a.inst]
    if not rows:
        print("没有事件（先跑一次流程）")
        return 1
    print(f"{'时刻':<14}{'Δt':>8}  {'来源':<7}{'事件':<11} 摘要")
    print("-" * 112)
    prev = rows[0]["ts"]
    last_stage = ""
    shown = 0
    for r in rows:
        d = r["ts"] - prev
        prev = r["ts"]
        is_stage = r.get("ev") == "stage" and r.get("stage")
        if is_stage and r["stage"] != last_stage:
            last_stage = r["stage"]
            msg = str(r.get("msg") or "")[:60]
            print(f"{r['iso'][11:23]:<14}{_fmt_delta(d):>8}  {'▶阶段':<7}{r['stage']:<11} {msg}")
            shown += 1
            continue
        if is_stage:
            continue                                   # 同阶段重复上报不刷屏
        if a.min_gap and d < a.min_gap and r.get("ev") in ("beat", "http", "reqs"):
            continue
        print(f"{r['iso'][11:23]:<14}{_fmt_delta(d):>8}  {r['src']:<7}{r['ev']:<11} {_summary(r)[:92]}")
        shown += 1
    print("-" * 112)
    print(f"共 {len(rows)} 条事件，展示 {shown} 行，跨度 {_fmt_delta(rows[-1]['ts'] - rows[0]['ts'])[1:]}")
    return 0


def cmd_stages(a) -> int:
    rows = load_day(a.date)
    if a.inst:
        rows = [r for r in rows if r.get("inst") == a.inst]
    marks = [r for r in rows if r.get("ev") == "stage" and r.get("stage")
             and (a.all or r["stage"] in BIZ_STAGES)]
    if len(marks) < 2:
        print("阶段记录不足，跑一轮再看")
        return 1
    print(f"{'阶段':<16}{'进入时刻':<14}{'停留':>9}   备注")
    print("-" * 78)
    for i, m in enumerate(marks):
        end = marks[i + 1]["ts"] if i + 1 < len(marks) else rows[-1]["ts"]
        dur = end - m["ts"]
        note = str(m.get("msg") or "")[:44]
        print(f"{m['stage']:<16}{m['iso'][11:19]:<14}{dur:9.1f}s   {note}")
    total = marks[-1]["ts"] - marks[0]["ts"]
    print("-" * 78)
    print(f"合计 {total:.0f}s（{total/60:.1f} 分钟）")
    return 0


def cmd_stats(a) -> int:
    rows = load_day(a.date)
    if not rows:
        print("没有事件")
        return 1
    from collections import Counter
    by_src = Counter(r["src"] for r in rows)
    by_ev = Counter(f"{r['src']}/{r['ev']}" for r in rows)
    errs = [r for r in rows if r.get("ok") is False or (r.get("code") or 0) >= 400
            or r.get("status") in (403, 412)]
    print(f"事件总数 {len(rows)}  跨度 {_fmt_delta(rows[-1]['ts'] - rows[0]['ts'])[1:]}")
    print("\n按来源：")
    for k, v in by_src.most_common():
        print(f"  {k:<10}{v:6d}")
    print("\n按事件：")
    for k, v in by_ev.most_common(14):
        print(f"  {k:<24}{v:6d}")
    print(f"\n异常事件 {len(errs)} 条：")
    for r in errs[:12]:
        print("  " + _human(r)[:118])
    # 关键指标
    mints = [r for r in rows if r.get("ev") == "mint"]
    if mints:
        ok = [r for r in mints if r.get("ok")]
        costs = [r.get("cost") or 0 for r in ok]
        print(f"\n铸票 {len(mints)} 次，成功 {len(ok)}，"
              f"均耗时 {sum(costs)/max(1,len(costs)):.2f}s")
    caps = [r for r in rows if r.get("ev") == "captcha"]
    if caps:
        sol = sum(1 for r in caps if r.get("ok"))
        print(f"验证码 {len(caps)} 次，自动通过 {sol}")
    return 0


def cmd_grep(a) -> int:
    rows = load_day(a.date)
    n = 0
    for r in rows:
        line = _human(r)
        if a.grep.lower() in line.lower():
            print(line)
            n += 1
    print(f"\n命中 {n} 条")
    return 0


def cmd_csv(a) -> int:
    import csv
    rows = load_day(a.date)
    if not rows:
        print("没有事件")
        return 1
    cols = ["seq", "iso", "src", "ev", "inst", "ip_id", "stage", "ok", "code",
            "status", "ms", "cost", "egress", "dx", "how", "retry", "url", "msg", "err"]
    extra = sorted({k for r in rows for k in r} - set(cols) - {"ts", "pid"})
    with open(a.csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols + extra, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"已导出 {len(rows)} 行 → {a.csv}")
    return 0


def cmd_report(a) -> int:
    rows = load_day(a.date)
    if not rows:
        print("没有事件")
        return 1
    day = a.date or datetime.now().strftime("%Y-%m-%d")
    out = JDIR / f"report_{day}.md"
    from collections import Counter
    errs = [r for r in rows if r.get("ok") is False or (r.get("code") or 0) >= 400
            or r.get("status") in (403, 412)]

    L = [f"# 运行报告 {day}", "",
         f"- 事件 **{len(rows)}** 条，{rows[0]['iso'][11:19]} → {rows[-1]['iso'][11:19]}",
         f"- 来源分布：" + "，".join(f"{k} {v}" for k, v in
                                 Counter(r["src"] for r in rows).most_common()),
         ""]

    # 关键指标
    mints = [r for r in rows if r.get("ev") == "mint"]
    if mints:
        okm = [r for r in mints if r.get("ok")]
        avg = sum((r.get("cost") or 0) for r in okm) / max(1, len(okm))
        L.append(f"- 铸票 {len(mints)} 次，成功 {len(okm)}，均耗时 {avg:.2f}s")
    healths = [r for r in rows if r.get("ev") == "health"]
    if healths:
        L.append(f"- 保活探测 {len(healths)} 次，200 的 {sum(1 for r in healths if r.get('code')==200)} 次")
    verds = [r for r in rows if r.get("ev") == "verdict"]
    if verds:
        c = Counter(r.get("verdict") for r in verds)
        L.append("- 业务校验：" + "，".join(f"{k} {v}" for k, v in c.items()))
    caps = [r for r in rows if r.get("ev") == "captcha"]
    if caps:
        L.append(f"- 验证码求解 {len(caps)} 次，成功 {sum(1 for r in caps if r.get('ok'))} 次")
    L.append(f"- 异常事件 **{len(errs)}** 条")
    L.append("")

    # 业务阶段
    marks = [r for r in rows if r.get("ev") == "stage" and r.get("stage")
             and r["stage"] in BIZ_STAGES]
    if marks:
        L += ["## 业务阶段", "", "| 阶段 | 进入 | 停留 | 说明 |", "|---|---|---|---|"]
        for i, m in enumerate(marks):
            end = marks[i + 1]["ts"] if i + 1 < len(marks) else rows[-1]["ts"]
            L.append(f"| {m['stage']} | {m['iso'][11:19]} | {end - m['ts']:.0f}s | "
                     f"{str(m.get('msg') or '')[:56]} |")
        L.append("")

    if errs:
        L += ["## 异常", "", "```"]
        for r in errs[:40]:
            L.append(_human(r))
        L += ["```", ""]

    L += ["## 完整时间线", "", "```"]
    prev = rows[0]["ts"]
    last_stage = ""
    for r in rows:
        if r.get("ev") == "stage" and r.get("stage"):
            if r["stage"] == last_stage:
                prev = r["ts"]
                continue
            last_stage = r["stage"]
        L.append(f"{r['iso'][11:23]} {_fmt_delta(r['ts'] - prev):>8}  {_human(r)}")
        prev = r["ts"]
    L += ["```", ""]

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"报告已生成 → {out}（{len(rows)} 条事件，{len(errs)} 条异常）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="全链路事件账本")
    ap.add_argument("--date", default="", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--timeline", action="store_true")
    ap.add_argument("--stages", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--grep", default="")
    ap.add_argument("--csv", default="")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--inst", default="", help="只看某个窗口/票（w1 / p01 / local）")
    ap.add_argument("--all", action="store_true", help="--stages 时连注入类阶段一起算")
    ap.add_argument("--min-gap", type=float, default=0.0,
                    help="时间线里过滤掉间隔小于该值的心跳/http 噪声")
    a = ap.parse_args()
    if a.tail:
        return cmd_tail(a)
    if a.timeline:
        return cmd_timeline(a)
    if a.stages:
        return cmd_stages(a)
    if a.stats:
        return cmd_stats(a)
    if a.grep:
        return cmd_grep(a)
    if a.csv:
        return cmd_csv(a)
    if a.report:
        return cmd_report(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
