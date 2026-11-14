"""临时诊断：提取耗时拆解 + 候选裁剪的精度/速度权衡。"""
import time
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

boards = [make_board(seed=s, n=4, hard=(s % 3 == 0)) for s in range(20)]

t0 = time.perf_counter()
for b in boards:
    for box in b.pieces + b.slots:
        sm.extract(b.image, box)
print(f"full extract: {(time.perf_counter()-t0)/len(boards)*1000:.0f} ms/board "
      f"({len(boards[0].pieces)+len(boards[0].slots)} shapes)")

# 拆解：只看各通道耗时
b = boards[0]
box = b.pieces[0]
x, y, w, h = box
pad = int(min(w, h) * 0.30)
crop = b.image[max(0, y-pad):y+h+pad, max(0, x-pad):x+w+pad]
padded = cv2.copyMakeBorder(crop, 6, 6, 6, 6, cv2.BORDER_REPLICATE)
lab = cv2.cvtColor(padded, cv2.COLOR_BGR2LAB).astype(np.float32)
seeds = sm._center_seeds(lab)

for name, fn in (
    ("floodFill x8", lambda: [sm._seeded_grow(lab, seeds, t, True) for t in (32, 46, 62, 80, 100)]
     + [sm._seeded_grow(lab, seeds, t, False) for t in (18, 28, 40)]),
    ("contour_fill x3", lambda: [sm._contour_fill(padded[:, :, :3], lo, hi) for lo, hi in ((50, 140), (80, 200), (30, 90))]),
    ("polar_sig", lambda: sm._polar_sig(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))),
    ("full mask_from_color", lambda: sm.mask_from_color(crop, pad_ratio=0.30)),
):
    t0 = time.perf_counter()
    for _ in range(20):
        fn()
    print(f"  {name:22s} {(time.perf_counter()-t0)/20*1000:6.2f} ms")
