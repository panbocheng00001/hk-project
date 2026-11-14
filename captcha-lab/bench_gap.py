"""缺口定位离线评测：合成 200 张图，统计 top-1 / top-3 命中率，并对比各策略。

指标（业界口径）：
  hit@k(px) = top-k 候选中存在与真值中心偏差 <= px 的比例
  工程阈值 <=4px：多数站点容差 5~8px，4px 内基本一次过
"""
from __future__ import annotations

import sys
import time
import numpy as np
import cv2

try:                                     # Windows 控制台默认 cp936/cp1252，统一 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from solver.synth import make_sample
from solver import gap


def eval_all(n: int = 200, side: int = 46) -> dict:
    stats = {"hit1_4": 0, "hit1_8": 0, "hit3_4": 0, "hit3_8": 0, "fail": []}
    per_method = {"box": [0, 0], "canny": [0, 0], "tpl": [0, 0], "shadow": [0, 0]}
    t0 = time.time()

    for i in range(n):
        img, piece, (gx, gy, gw, gh) = make_sample(side=side, seed=i)
        gt_cx = gx + gw / 2.0

        cands = gap.locate(img, piece=piece, topk=3)
        if not cands:
            stats["fail"].append((i, "no_cand"))
            continue
        d1 = abs(cands[0].cx - gt_cx)
        d3 = min(abs(c.cx - gt_cx) for c in cands)
        stats["hit1_4"] += d1 <= 4
        stats["hit1_8"] += d1 <= 8
        stats["hit3_4"] += d3 <= 4
        stats["hit3_8"] += d3 <= 8
        if d3 > 8:
            stats["fail"].append((i, f"gt={gt_cx:.0f} pred={[round(c.cx) for c in cands]}"))

        # 单策略对比
        for name, fn in (("box", lambda: gap._strategy_box_energy(img)),
                         ("canny", lambda: gap._strategy_canny(img)),
                         ("tpl", lambda: gap._strategy_template(img, piece)),
                         ("shadow", lambda: gap._strategy_shadow(img))):
            cs = fn()
            per_method[name][1] += 1
            if cs and any(abs(c.cx - gt_cx) <= 8 for c in cs):
                per_method[name][0] += 1

    stats["elapsed"] = time.time() - t0
    stats["per_method"] = per_method
    return stats


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    s = eval_all(n)
    print(f"samples={n}  elapsed={s['elapsed']:.1f}s  ({s['elapsed']/n*1000:.0f} ms/img)")
    print(f"top1 <=4px : {s['hit1_4']}/{n} = {s['hit1_4']/n:.1%}")
    print(f"top1 <=8px : {s['hit1_8']}/{n} = {s['hit1_8']/n:.1%}")
    print(f"top3 <=4px : {s['hit3_4']}/{n} = {s['hit3_4']/n:.1%}")
    print(f"top3 <=8px : {s['hit3_8']}/{n} = {s['hit3_8']/n:.1%}")
    print("per-method recall@8px (任一候选中):")
    for k, (h, t) in s["per_method"].items():
        print(f"  {k:7s} {h}/{t} = {h/max(t,1):.1%}")
    print(f"misses: {len(s['fail'])}")
    for f in s["fail"][:8]:
        print("  ", f)
