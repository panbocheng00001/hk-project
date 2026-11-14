"""临时诊断：单例 verbose，看多假设打分到底选了哪个。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm

b = make_board(seed=3, n=4, hard=True)
kinds = b.meta["kinds"]
print("kinds", kinds, "truth", b.truth)

for tag, boxes, pool in (("P", b.pieces, b.piece_masks), ("S", b.slots, b.slot_masks)):
    for i, box in enumerate(boxes):
        x, y, w, h = box
        pad = int(max(2, min(w, h) * 0.18))
        rx, ry = max(0, x - pad), max(0, y - pad)
        crop = b.image[ry:ry + h + 2 * pad, rx:rx + w + 2 * pad]
        print(f"\n--- {tag}{i} box={box} roi={crop.shape} "
              f"truth_area={np.count_nonzero(pool[i])} truth_box_area={w*h}")
        m = sm.mask_from_color(crop, pad_ratio=0.05, verbose=True)
        if m is not None:
            s = sm.extract(b.image, box)
            print(f"    chosen cov={m.mean()/255:.3f} area={np.count_nonzero(m)} "
                  f"iou={b.iou_vs_truth('piece' if tag=='P' else 'slot', i, m, (rx, ry)):.3f}")
