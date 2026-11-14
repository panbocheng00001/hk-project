"""临时诊断：用真值 mask 算 rot_iou 天花板，判断还有多少提升空间。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm

W = sm.ScoreWeights()
tp, tm = [], []
for seed in range(30):
    b = make_board(seed=seed, n=4, hard=(seed % 3 == 0))
    kinds = b.meta["kinds"]
    for i in range(4):
        pn = sm._norm_mask(b.piece_masks[i])
        rots, _ = sm._rot_masks(pn, W.angle_step)
        for j in range(4):
            sn = sm._norm_mask(b.slot_masks[j])
            iou, ang = sm.rot_iou(rots, type("S", (), {"nmask": sn})(), W)
            (tp if b.truth[i] == j else tm).append(iou)

tp, tm = np.array(tp), np.array(tm)
print(f"ceiling  true-pair rot_iou: mean={tp.mean():.3f} p10={np.percentile(tp,10):.3f} min={tp.min():.3f}")
print(f"ceiling false-pair rot_iou: mean={tm.mean():.3f} p90={np.percentile(tm,90):.3f} max={tm.max():.3f}")
print(f"分离度 (p10_true - p90_false) = {np.percentile(tp,10)-np.percentile(tm,90):+.3f}")
