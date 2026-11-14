"""临时诊断：提取 mask 与真值 mask 的 IoU，定位瓶颈在提取还是在相似度。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm

ious_p, ious_s = [], []
for seed in range(40):
    b = make_board(seed=seed, n=4 + seed % 3, hard=(seed % 3 == 0))
    for i, box in enumerate(b.pieces):
        s = sm.extract(b.image, box)
        if s is not None:
            ious_p.append(b.iou_vs_truth("piece", i, s.mask, (s.ox, s.oy)))
    for i, box in enumerate(b.slots):
        s = sm.extract(b.image, box)
        if s is not None:
            ious_s.append(b.iou_vs_truth("slot", i, s.mask, (s.ox, s.oy)))

for tag, v in (("piece", ious_p), ("slot", ious_s)):
    a = np.array(v)
    print(f"{tag}: n={len(a)} meanIoU={a.mean():.3f} "
          f"p10={np.percentile(a,10):.3f} p50={np.percentile(a,50):.3f} "
          f"p90={np.percentile(a,90):.3f}  <0.7 占比={np.mean(a<0.7):.1%}")
