"""
拖拽归位配对 —— 离线评测
=========================

指标口径（对齐线上"能不能过"）：
  board_acc   : 整盘全对的占比  ← 唯一有意义的指标（错一对 = 整盘重来）
  pair_acc    : 单个配对正确的占比
  strict_acc  : 只在"全盘 margin 都 ≥ 阈值"的子集上，board_acc 是多少
                （用来校准"何时该提交、何时该重取图"）
  mrr         : 正确槽位在得分排序中的平均倒数排名（衡量排序质量，不是硬指标）

同时做三件事：
  1) 逐通道消融：去掉 rot_iou / fd / hu / radial / app 各掉多少 → 决定该优化谁
  2) 权重网格搜索：给出比默认值更好的组合（打印到 stdout）
  3) 难样本分层：easy / hard 分别报数，别用 easy 的数字骗自己

用法：
    python bench_match.py 200            # 200 盘
    python bench_match.py 200 --sweep    # 额外跑权重搜索
"""
from __future__ import annotations

import itertools
import sys
import time
from typing import Dict, List, Sequence

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from solver import shape_match as sm
from solver.synth_match import make_board


# --------------------------------------------------------------------------- #
def build_case(seed: int, n: int, hard: bool):
    b = make_board(seed=seed, n=n, hard=hard)
    pieces, slots = [], []
    for i, box in enumerate(b.pieces):
        s = sm.extract(b.image, box, label=f"p{i}")
        if s is not None:
            pieces.append(s)
    for i, box in enumerate(b.slots):
        s = sm.extract(b.image, box, label=f"s{i}")
        if s is not None:
            slots.append(s)
    if len(pieces) != n or len(slots) != n:
        return None
    return pieces, slots, b.truth


def evaluate(n: int = 200, hard_ratio: float = 0.4,
             weights: sm.ScoreWeights | None = None,
             use_app: bool = True, verbose: bool = False) -> Dict:
    w = weights or sm.ScoreWeights()
    res = {"boards": 0, "board_ok": 0, "pairs": 0, "pair_ok": 0,
           "easy": [0, 0], "hard": [0, 0], "mrr": 0.0,
           "conf_ok": 0, "conf_n": 0, "conf_thr": 0.0,
           "ms": 0.0, "fails": []}
    t0 = time.perf_counter()
    margins_all: List[float] = []
    per_board_margin: List[tuple] = []

    for i in range(n):
        hard = (i % 100) < hard_ratio * 100
        case = build_case(i, 4 + i % 3, hard)
        if case is None:
            continue
        pieces, slots, truth = case
        r = sm.match(pieces, slots, w, use_app=use_app)
        if not r.pairs:
            continue
        res["boards"] += 1
        pred = {p.piece: p.slot for p in r.pairs}
        ok = all(pred.get(k) == v for k, v in enumerate(truth))
        res["board_ok"] += int(ok)
        res["easy" if not hard else "hard"][0] += int(ok)
        res["easy" if not hard else "hard"][1] += 1
        for k, v in enumerate(truth):
            res["pairs"] += 1
            res["pair_ok"] += int(pred.get(k) == v)
            row = r.cost[k].copy()
            order = list(np.argsort(-row))
            if v in order:
                res["mrr"] += 1.0 / (1 + order.index(v))
        mn = min(p.margin for p in r.pairs)
        margins_all.extend(p.margin for p in r.pairs)
        per_board_margin.append((mn, ok))
        if not ok and len(res["fails"]) < 6:
            res["fails"].append({
                "seed": i, "hard": hard, "truth": truth,
                "pred": [pred.get(k, -1) for k in range(len(truth))],
                "scores": [[round(float(r.cost[a, b]), 2) for b in range(len(slots))]
                           for a in range(len(pieces))],
            })
        if verbose:
            print(f"  seed={i} hard={hard} ok={ok} truth={truth} pred={pred}")

    res["ms"] = (time.perf_counter() - t0) / max(1, res["boards"]) * 1000

    # 校准：margin 阈值 vs 整盘正确率（保守估计用 min margin）
    best = (0.0, 0.0)
    for thr in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20):
        sel = [ok for mn, ok in per_board_margin if mn >= thr]
        if len(sel) >= max(10, 0.15 * len(per_board_margin)):
            acc = sum(sel) / len(sel)
            cover = len(sel) / max(1, len(per_board_margin))
            if acc > best[1]:
                best = (thr, acc, cover)
    res["conf_thr"] = best[0]
    if len(best) >= 3:
        res["conf_acc"], res["conf_cover"] = best[1], best[2]
    return res


def report(tag: str, r: Dict) -> None:
    b = max(1, r["boards"])
    print(f"[{tag}] boards={r['boards']}  {r['ms']:.1f} ms/board")
    print(f"  board_acc : {r['board_ok']}/{b} = {r['board_ok']/b:.1%}")
    print(f"  pair_acc  : {r['pair_ok']}/{max(1,r['pairs'])} = "
          f"{r['pair_ok']/max(1,r['pairs']):.1%}")
    print(f"  mrr       : {r['mrr']/max(1,r['pairs']):.3f}")
    for k in ("easy", "hard"):
        h, t = r[k]
        if t:
            print(f"  {k:5s}     : {h}/{t} = {h/t:.1%}")
    if "conf_acc" in r:
        print(f"  margin≥{r['conf_thr']:.2f} 时 board_acc={r['conf_acc']:.1%} "
              f"(覆盖 {r['conf_cover']:.0%})  ← 线上提交门限")


def ablate(n: int = 120) -> None:
    print("\n=== 通道消融（去掉某通道后的 board_acc）===")
    base = evaluate(n)
    report("full", base)
    variants = {
        "no rot_iou": dict(rot_iou=0.0),
        "no fd": dict(fd=0.0),
        "no hu": dict(hu=0.0),
        "no radial": dict(radial=0.0),
        "no app": dict(app=0.0),
        "no angle_pen": dict(upright_penalty=0.0),
        "step=5°": dict(angle_step=5),
        "step=30°": dict(angle_step=30),
    }
    for name, kw in variants.items():
        w = sm.ScoreWeights(**kw)
        r = evaluate(n, weights=w, use_app=("app" not in kw))
        b = max(1, r["boards"])
        print(f"  {name:12s} board={r['board_ok']/b:.1%}  "
              f"pair={r['pair_ok']/max(1,r['pairs']):.1%}  ({r['ms']:.0f} ms)")


def sweep(n: int = 80) -> None:
    print("\n=== 权重网格搜索（board_acc 优先）===")
    grid = {
        "rot_iou": [0.40, 0.46, 0.55],
        "app": [0.10, 0.18, 0.26],
        "upright_penalty": [0.0, 0.14, 0.24],
    }
    keys = list(grid)
    best = (-1.0, None)
    for combo in itertools.product(*[grid[k] for k in keys]):
        kw = dict(zip(keys, combo))
        r = evaluate(n, weights=sm.ScoreWeights(**kw))
        b = max(1, r["boards"])
        acc = r["board_ok"] / b
        if acc > best[0]:
            best = (acc, kw, r["ms"])
    print(f"  best board_acc={best[0]:.1%}  weights={best[1]}  ({best[2]:.0f} ms)")
    print(f"  → 建议把 ScoreWeights 默认值改成这组")


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    report("default", evaluate(N))
    ablate(min(120, N))
    if "--sweep" in sys.argv:
        sweep(min(80, N))
