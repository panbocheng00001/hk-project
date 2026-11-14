"""临时诊断：_stability 是否工作。"""
import numpy as np, cv2
from solver.synth_match import make_board
from solver import shape_match as sm

b = make_board(seed=3, n=4, hard=True)
x, y, w, h = b.pieces[0]
pad = int(min(w, h) * 0.30)
crop = b.image[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
h2, w2 = crop.shape[:2]
padded = cv2.copyMakeBorder(crop, 4, 4, 4, 4, cv2.BORDER_REPLICATE)
lab = cv2.cvtColor(padded, cv2.COLOR_BGR2LAB).astype(np.float32)
bg = sm._corner_bg(lab)
d = np.linalg.norm(lab - bg[None, None, :], axis=2)
print("bg", bg, "dmax", d.max(), "dmean", d.mean())
print("corner bg vs center:", bg, lab[lab.shape[0]//2, lab.shape[1]//2])

ms = []
for t in (30, 40, 50):
    m = np.where(d > t, 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
    ms.append(m)
    print(t, "cov", m.mean() / 255)
print("IoU 30vs40", sm._stability(ms[0], [ms[1]]))
print("IoU 40vs50", sm._stability(ms[1], [ms[2]]))
