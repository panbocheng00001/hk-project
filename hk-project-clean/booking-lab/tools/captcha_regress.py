# -*- coding: utf-8 -*-
"""captcha_regress.py —— 复刻题回归闸门（调用 + 统计，不含识别逻辑）

定位
----
把「captcha_auto.solve_page → captcha_lab_bridge → captcha-lab/solver」这条已有链路
包成一条可复现、有退出码的回归命令。本脚本不实现、不修改任何识别算法与判据：
位移由内核算，判定由复刻题服务给，这里只负责**固定题号、连跑、统计、给退出码**。

输入输出
--------
    python tools/captcha_regress.py --n 30 --start 1000 [--json runs/regress.json]

    --n           题数（默认 30）
    --start       起始题号（默认 1000）；题号段固定 ⇒ 结果可复现
    --fail-under  通过率门槛（默认 1.0）
    --json        把逐题结果写到文件
    --headed      有头运行（排障用）

    退出码：0 达标 / 1 未达标 / 3 复刻题服务没起 / 4 浏览器不可用

不变量
------
    · 题号由页面自己的 newChallenge(seed) 决定，不自己造题；
    · 判定只读页面写入的 window.__grade 与 #seed，不自己重算位移；
    · 同一 --start/--n 重复跑，题号段与逐题结果一致。

失败路径
--------
    服务没起 → 打印启动命令，退出 3，不重试；
    浏览器起不来 → 打印原因，退出 4；
    单题异常 → 记 ok=False + note，继续跑完，由通过率决定退出码（不静默跳过）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

BASE_DEFAULT = "http://127.0.0.1:8777"
START_CMD = r"cd booking-lab\captcha_mock && node server.js   # 127.0.0.1:8777"

# tools/ 不是包：把上一级（booking-lab）放进 sys.path，才能 import captcha_auto
_PROJ = Path(__file__).resolve().parents[1]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))


# --------------------------------------------------------------------------- #
# 纯函数：题号、统计、退出码（可离线单测）
# --------------------------------------------------------------------------- #
def seeds(start: int, n: int) -> List[int]:
    """固定题号段。题号固定是"可复现"的唯一来源。"""
    return [int(start) + i for i in range(int(n))]


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """逐题结果 → 汇总口径。

    pass_rate 只按 ok 且页面判定 pass 计；误差取自页面自己的 diff（真值由 /grade 给）。
    """
    total = len(rows)
    passed = sum(1 for r in rows if r.get("ok") and r.get("pass"))
    diffs = [float(r["diff"]) for r in rows if isinstance(r.get("diff"), (int, float))]
    elapsed = [float(r["elapsed_ms"]) for r in rows if isinstance(r.get("elapsed_ms"), (int, float))]

    def _pct(xs: List[float], q: float) -> Optional[float]:
        if not xs:
            return None
        s = sorted(xs)
        if len(s) == 1:
            return s[0]
        pos = (len(s) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "diff_mean": statistics.fmean(diffs) if diffs else None,
        "diff_p95": _pct(diffs, 0.95),
        "diff_max": max(diffs) if diffs else None,
        "elapsed_total_s": sum(elapsed) / 1000.0 if elapsed else 0.0,
        "failed_seeds": [r.get("seed") for r in rows if not (r.get("ok") and r.get("pass"))],
    }


def decide_exit(summary: Dict[str, Any], fail_under: float) -> int:
    """通过率达标 0，否则 1。没有跑到任何一题也算未达标。"""
    if not summary.get("total"):
        return 1
    return 0 if float(summary.get("pass_rate", 0.0)) >= float(fail_under) else 1


# --------------------------------------------------------------------------- #
# 环境探测
# --------------------------------------------------------------------------- #
def service_alive(base: str, timeout: float = 2.0) -> bool:
    """复刻题服务是否在线（走 /healthz，不触发出题）。

    回环请求显式使用空代理表，避免系统代理接管 127.0.0.1。
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base.rstrip("/") + "/healthz", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 跑一题 / 跑一轮
# --------------------------------------------------------------------------- #
def _read_grade(page) -> Dict[str, Any]:
    try:
        return dict(page.evaluate("() => window.__grade || null") or {})
    except Exception:
        return {}


def run(base: str, seed_list: Sequence[int], headless: bool = True,
        log: Callable[[str], None] = print) -> List[Dict[str, Any]]:
    """逐题跑：固定题号 → 调页面自己的 newChallenge(seed) → 交 solve_page → 收判定。"""
    from playwright.sync_api import sync_playwright

    import captcha_auto  # 同目录上一级已在 sys.path

    rows: List[Dict[str, Any]] = []
    base = base.rstrip("/")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            for number, requested_seed in enumerate(seed_list, 1):
                t0 = time.perf_counter()
                row: Dict[str, Any] = {
                    "n": number,
                    "seed": int(requested_seed),
                    "ok": False,
                    "pass": False,
                    "submitted_dx": None,
                    "truth_dx": None,
                    "diff": None,
                    "elapsed_ms": 0.0,
                    "note": "",
                }
                try:
                    page.goto(base + "/", wait_until="networkidle")
                    page.wait_for_selector("#handle")
                    # 固定题号：页面里 newChallenge(seed) 是全局函数，用它出题即可复现
                    page.evaluate("(s) => newChallenge(s)", int(requested_seed))
                    page.wait_for_timeout(400)
                    shown = page.inner_text("#seed").strip()
                    actual_seed = int(shown)
                    row["seed"] = actual_seed
                    if actual_seed != int(requested_seed):
                        raise ValueError(
                            f"题号不一致：请求 {requested_seed}，页面回显 {actual_seed}"
                        )
                    r = captcha_auto.solve_page(page, who=f"reg{actual_seed}",
                                                log=lambda *_a: None)
                    g = _read_grade(page)
                    row.update({
                        "ok": bool(r.get("ok")),
                        "tries": r.get("tries"),
                        "note": r.get("note"),
                        "submitted_dx": g.get("submitted_dx"),
                        "truth_dx": g.get("truth_dx"),
                        "diff": g.get("diff"),
                        "pass": bool(g.get("pass")),
                        "slider_dx": g.get("sliderDx"),
                    })
                except Exception as e:
                    row["note"] = f"{type(e).__name__}: {e}"
                row["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                rows.append(row)
                if not (row["ok"] and row["pass"]):
                    log(f"[captcha_regress] seed={row['seed']} FAIL "
                        f"note={row.get('note') or '页面未通过'}")
        finally:
            browser.close()
    return rows


def render(rows: Sequence[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    """把逐题结果与汇总渲染成终端表格，失败行包含题号和 note。"""

    def _f(v: Any, nd: int = 3) -> str:
        return "-" if v is None else f"{float(v):.{nd}f}"

    lines = [
        " n  seed        ok    pass  submitted   truth    diff  elapsed  note",
        "-" * 88,
    ]
    for row in rows:
        note = "" if row.get("pass") else str(row.get("note") or "页面未通过")
        lines.append(
            f"{int(row['n']):2d}  {str(row.get('seed')):<10}"
            f"  {str(bool(row.get('ok'))):<5}"
            f" {str(bool(row.get('pass'))):<5}"
            f" {_f(row.get('submitted_dx'), 2):>10}"
            f" {_f(row.get('truth_dx'), 2):>7}"
            f" {_f(row.get('diff'), 2):>7}"
            f" {float(row.get('elapsed_ms') or 0):7.0f}ms  {note}"
        )
    failed = ""
    if summary["failed_seeds"]:
        failed = " | 失败题号 " + ",".join(str(s) for s in summary["failed_seeds"])
    lines.append("-" * 88)
    lines.append(
        f"摘要：通过 {summary['passed']}/{summary['total']} "
        f"({summary['pass_rate'] * 100:.1f}%) | "
        f"误差 mean={_f(summary['diff_mean'])}px "
        f"p95={_f(summary['diff_p95'])}px max={_f(summary['diff_max'])}px | "
        f"总耗时 {summary['elapsed_total_s']:.1f}s{failed}"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="复刻题回归闸门（调用 + 统计，不含识别逻辑）")
    ap.add_argument("--n", type=int, default=30, help="题数，默认 30")
    ap.add_argument("--start", type=int, default=1000, help="起始题号，默认 1000")
    ap.add_argument("--base", default=BASE_DEFAULT, help=f"复刻题服务地址，默认 {BASE_DEFAULT}")
    ap.add_argument("--fail-under", type=float, default=1.0, help="通过率门槛，默认 1.0")
    ap.add_argument("--json", default=None, help="逐题结果写到这个文件")
    ap.add_argument("--headed", action="store_true", help="有头运行（排障用）")
    a = ap.parse_args(argv)

    if a.n <= 0:
        print("--n 必须大于 0")
        return 1

    if not service_alive(a.base):
        print(f"复刻题服务没起（{a.base}）。先起服务再跑：")
        print(f"  {START_CMD}")
        return 3

    if not playwright_available():
        print("浏览器不可用：未安装 playwright。pip install playwright && playwright install chromium")
        return 4

    seed_list = seeds(a.start, a.n)
    print(f"题号段 {seed_list[0]}..{seed_list[-1]}（{len(seed_list)} 题），服务 {a.base}")
    try:
        rows = run(a.base, seed_list, headless=not a.headed)
    except Exception as e:  # 浏览器起不来 / 启动期异常
        print(f"浏览器不可用：{type(e).__name__}: {e}")
        return 4

    summary = summarize(rows)
    print("")
    print(render(rows, summary))

    if a.json:
        dst = Path(a.json)
        dst.parent.mkdir(parents=True, exist_ok=True)
        payload = {"base": a.base, "start": a.start, "n": a.n,
                   "fail_under": a.fail_under, "summary": summary, "rows": rows}
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"逐题结果：{dst}")

    return decide_exit(summary, a.fail_under)


if __name__ == "__main__":
    sys.exit(main())
