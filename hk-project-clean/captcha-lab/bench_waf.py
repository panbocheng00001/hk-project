"""几何重合滑块：端到端评测（对拍 booking-lab/captcha_mock 的判据）

用法：
    # 1) 起复刻服务
    cd booking-lab/captcha_mock && node server.js          # 127.0.0.1:8777
    # 2) 跑评测
    python bench_waf.py --n 30

指标：
    pass_rate   : /grade 判定通过的比例（容差默认 3 位图像素）
    dx_err      : 与真值的水平误差（位图像素），报 mean / p95 / max
    parity      : 与 Node 版 analyze.js 的 dx 差异（判据是否对齐）
"""
from __future__ import annotations

import argparse
import statistics
import sys
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from solver.overlap import analyze, solve_canvas, to_slider_dx


def run(base: str, n: int, start: int, track_w: float, handle_w: float,
        bmp_w: float, tol: int) -> Dict[str, Any]:
    import httpx

    rows: List[Dict[str, Any]] = []
    # trust_env=False：本机系统代理会连 127.0.0.1 的回环请求一起接管，
    # 结果是 /challenge 返回 503 空响应，看着像算法崩了，其实请求没到 Node。
    with httpx.Client(timeout=15.0, base_url=base, trust_env=False) as c:
        for i in range(n):
            seed = start + i
            ch = c.get("/challenge", params={"seed": seed, "dpr": 1}).json()
            res = solve_canvas(ch["dataUrl"], bmp_w=bmp_w, track_w=track_w,
                               handle_w=handle_w)
            row: Dict[str, Any] = {"seed": seed, "ok": res["ok"], "dx": res["dx"],
                                   "slider_dx": res["slider_dx"]}

            # 与 Node 版判据对拍（/analyze 会回 truth_dx 与自身 dx）
            try:
                a = c.post("/analyze", json={
                    "dataUrl": ch["dataUrl"], "bmpW": ch["bmpW"], "bmpH": ch["bmpH"],
                    "cssW": ch["geom"]["w"], "cssH": ch["geom"]["h"], "dpr": 1,
                    "inFrame": False, "trackW": track_w, "handleW": handle_w,
                    "seed": seed,
                }).json()
                row["js_dx"] = a.get("dx")
                row["truth_dx"] = a.get("truth_dx")
                row["parity"] = abs(res["dx"] - a["dx"]) if a.get("dx") is not None else None
                row["err"] = abs(res["dx"] - a["truth_dx"]) if a.get("truth_dx") is not None else None
            except Exception:
                pass

            g = c.post("/grade", json={"seed": seed, "sliderDx": res["slider_dx"],
                                       "bmpW": bmp_w, "trackW": track_w,
                                       "handleW": handle_w}).json()
            row["pass"] = bool(g.get("pass"))
            row["diff"] = g.get("diff")
            rows.append(row)

    errs = [r["err"] for r in rows if r.get("err") is not None]
    pars = [r["parity"] for r in rows if r.get("parity") is not None]
    diffs = [r["diff"] for r in rows if r.get("diff") is not None]
    return {
        "samples": n,
        "pass": sum(1 for r in rows if r["pass"]),
        "pass_rate": sum(1 for r in rows if r["pass"]) / max(1, n),
        "mean_err": statistics.fmean(errs) if errs else None,
        "p95_err": sorted(errs)[int(0.95 * (len(errs) - 1))] if errs else None,
        "max_err": max(errs) if errs else None,
        "mean_parity": statistics.fmean(pars) if pars else None,
        "max_parity": max(pars) if pars else None,
        "max_diff": max(diffs) if diffs else None,
        "tol": tol,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="华为 WAF 滑块端到端评测")
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--start", type=int, default=1000)
    ap.add_argument("--track-w", type=float, default=400.0)
    ap.add_argument("--handle-w", type=float, default=34.0)
    ap.add_argument("--bmp-w", type=float, default=400.0)
    ap.add_argument("--tol", type=int, default=3)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    s = run(a.base, a.n, a.start, a.track_w, a.handle_w, a.bmp_w, a.tol)
    print(f"样本 {s['samples']}  容差 {s['tol']}px")
    print(f"通过      {s['pass']}/{s['samples']} = {s['pass_rate']:.1%}")
    if s["mean_err"] is not None:
        print(f"误差(真值) mean={s['mean_err']:.2f}px  p95={s['p95_err']:.2f}px  "
              f"max={s['max_err']:.2f}px")
    if s["mean_parity"] is not None:
        print(f"与 JS 判据对齐  mean={s['mean_parity']:.2f}px  max={s['max_parity']:.2f}px")
    if a.verbose:
        for r in s["rows"]:
            print(f"  seed={r['seed']:<5} pass={r['pass']} dx={r['dx']:.2f} "
                  f"err={r.get('err')} parity={r.get('parity')} diff={r['diff']}")
    return 0 if s["pass_rate"] >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
