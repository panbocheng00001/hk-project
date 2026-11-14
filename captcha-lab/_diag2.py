"""临时诊断：rot_iou 矩阵 vs 真实形状种类，判断描述子是否有区分度。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm

W = sm.ScoreWeights()
for seed in (3, 7, 11, 21):
    b = make_board(seed=seed, n=4, hard=(seed % 2 == 1))
    kinds = b.meta["kinds"]
    ps = [sm.extract(b.image, bx) for bx in b.pieces]
    ss = [sm.extract(b.image, bx) for bx in b.slots]
    print(f"=== seed={seed} kinds={kinds} truth={b.truth}")
    print("      " + "  ".join(f"S{j}({kinds[j][:6]})" for j in range(len(ss))))
    for i, p in enumerate(ps):
        rots, _ = sm._rot_masks(p.nmask, W.angle_step)
        row = []
        for j, s in enumerate(ss):
            sc, d = sm.score_pair(p, s, rots, W)
            row.append(f"{d['rot_iou']:.2f}/{sc:+.2f}")
        kp = kinds[b.truth[i]]
        mark = "".join(" ^" if b.truth[i] == j else "  " for j in range(len(ss)))
        print(f"P{i}({kp[:6]:6s}) " + " ".join(f"{v:>11s}" for v in row))
        print(f"           truth-> S{b.truth[i]}   {mark}")
