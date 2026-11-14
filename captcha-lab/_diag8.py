"""临时诊断：候选集 oracle 上限（best candidate IoU）vs 实际选择，判断瓶颈在生成还是打分。"""
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

_orig = sm.mask_from_color


def cands_with_iou(crop, truth_roi):
    """复刻 mask_from_color 的候选生成，返回 [(tag, iou)]。"""
    h, w = crop.shape[:2]
    ph, pw = max(2, int(h * 0.30)), max(2, int(w * 0.30))
    padded = cv2.copyMakeBorder(crop, ph, ph, pw, pw, cv2.BORDER_REPLICATE)
    lab = cv2.cvtColor(padded, cv2.COLOR_BGR2LAB).astype(np.float32)
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def cleanup(m, close_it=5):
        if m is None or m.mean() < 1:
            return None
        k = k5 if close_it >= 5 else k3
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k3, iterations=1)
        if m.mean() < 1:
            return None
        n, labm, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        if n <= 1:
            return None
        areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
        keep = [i + 1 for i in range(n - 1) if areas[i] >= 0.05 * areas.max()]
        m = np.where(np.isin(labm, keep), 255, 0).astype(np.uint8)
        return sm._fill_holes(m)

    out = []
    seeds = sm._center_seeds(lab)
    for tol in (32.0, 46.0, 62.0, 80.0, 100.0):
        out.append((f"grow{tol:.0f}", cleanup(sm._seeded_grow(lab, seeds, tol, True), 3)))
    for tol in (18.0, 28.0, 40.0):
        out.append((f"growD{tol:.0f}", cleanup(sm._seeded_grow(lab, seeds, tol, False), 3)))
    bg = sm._corner_bg(lab)
    d = cv2.GaussianBlur(np.linalg.norm(lab - bg[None, None, :], axis=2), (3, 3), 0)
    dmax = float(d.max())
    if dmax >= 10:
        dn = (d / dmax * 255).astype(np.uint8)
        otsu, _ = cv2.threshold(dn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for t in sorted({round(v, 2) for v in [float(otsu)] + [f * dmax for f in (0.22, 0.32, 0.45, 0.60)] if 3 <= v <= 0.95 * dmax}):
            for inv in (False, True):
                m = np.where(dn > t, 255, 0).astype(np.uint8)
                if inv:
                    m = cv2.bitwise_not(m)
                out.append((f"thr{t:.0f}{'i' if inv else ''}", cleanup(m)))
    for lo, hi in ((50, 140), (80, 200), (30, 90)):
        for j, m in enumerate(sm._contour_fill(padded[:, :, :3], lo, hi)):
            out.append((f"edge{lo}_{j}", cleanup(m, 3)))

    res = []
    for tag, m in out:
        if m is None:
            continue
        sub = m[ph:ph + h, pw:pw + w]
        g = truth_roi
        a, b = sub > 127, g > 127
        u = np.count_nonzero(a | b)
        res.append((tag, float(np.count_nonzero(a & b) / u) if u else 0.0, m))
    return res


oracle, chosen, tags_best = [], [], []
for seed in range(25):
    b = make_board(seed=seed, n=4, hard=(seed % 3 == 0))
    for which, boxes, pool in (("piece", b.pieces, b.piece_masks), ("slot", b.slots, b.slot_masks)):
        for i, box in enumerate(boxes):
            x, y, w, h = box
            pad = int(max(2, min(w, h) * 0.30))
            rx, ry = max(0, x - pad), max(0, y - pad)
            crop = b.image[ry:ry + h + 2 * pad, rx:rx + w + 2 * pad]
            g = pool[i][ry:ry + crop.shape[0], rx:rx + crop.shape[1]]
            cs = cands_with_iou(crop, g)
            if not cs:
                continue
            oracle.append(max(c[1] for c in cs))
            s = sm.extract(b.image, box)
            chosen.append(b.iou_vs_truth(which, i, s.mask, (s.ox, s.oy)) if s else 0)
            tags_best.append(max(cs, key=lambda c: c[1])[0])

o, c = np.array(oracle), np.array(chosen)
print(f"candidate-set oracle IoU: mean={o.mean():.3f} p10={np.percentile(o,10):.3f} >=0.8:{(o>=0.8).mean():.1%}")
print(f"actual chosen   IoU     : mean={c.mean():.3f} p10={np.percentile(c,10):.3f} >=0.8:{(c>=0.8).mean():.1%}")
print(f"oracle gap (oracle-chosen) = {(o-c).mean():.3f}")
from collections import Counter
print("best-channel histogram:", Counter(tags_best).most_common(8))
