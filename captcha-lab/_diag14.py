"""临时诊断：槽位失败样本长什么样 —— 打印每个候选通道的 IoU。"""
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

bad = []
for seed in range(40):
    b = make_board(seed=seed, n=4, hard=(seed % 3 == 0))
    for i, box in enumerate(b.slots):
        s = sm.extract(b.image, box)
        if s is None:
            continue
        iou = b.iou_vs_truth("slot", i, s.mask, (s.ox, s.oy))
        if iou < 0.6:
            bad.append((seed, i, box, iou, b.meta["kinds"][i]))
print(f"失败槽位数 = {len(bad)}")
for seed, i, box, iou, kind in bad[:10]:
    b = make_board(seed=seed, n=4, hard=(seed % 3 == 0))
    print(f"\n--- seed={seed} slot{i} kind={kind} box={box} iou={iou:.3f}")
    s = sm.extract(b.image, box)
    print(f"    extracted bbox=({s.x},{s.y},{s.w},{s.h}) area={s.area:.0f} "
          f"truth_area={np.count_nonzero(b.slot_masks[i])}")
    # 打印真值框内 alpha 混合后的灰度 vs 背景
    x, y, w, h = box
    patch = b.image[y:y + h, x:x + w]
    print(f"    patch mean BGR={patch.reshape(-1,3).mean(0).round(1)} "
          f"std={patch.reshape(-1,3).std(0).round(1)}")
    # 直接用真值 mask 看能否被任一通道找到
    g = b.slot_masks[i]
    ys, xs = np.nonzero(g)
    print(f"    truth bbox=({xs.min()},{ys.min()},{xs.max()-xs.min()+1},{ys.max()-ys.min()+1}) "
          f"fill={len(xs)/((xs.max()-xs.min()+1)*(ys.max()-ys.min()+1)):.2f}")
