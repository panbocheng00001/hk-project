"""临时诊断：极坐标相位相关 vs 旋转搜索 IoU（GT mask 上比较判别力）。"""
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

NR, NT = 48, 180          # 径向 48 档，角向 180 档（2°）


def polar_sig(mask, nr=NR, nt=NT):
    """mask → 极坐标占用率图 (nr, nt)，按等效半径归一，旋转 = 角向循环移位。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.zeros((nr, nt), np.float32)
    cx, cy = xs.mean(), ys.mean()
    r = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    rmax = np.percentile(r, 99)
    if rmax <= 0:
        return np.zeros((nr, nt), np.float32)
    th = (np.arctan2(ys - cy, xs - cx) + 2 * np.pi) % (2 * np.pi)
    ri = np.clip((r / rmax * nr).astype(int), 0, nr - 1)
    ti = np.clip((th / (2 * np.pi) * nt).astype(int), 0, nt - 1)
    g = np.zeros((nr, nt), np.float32)
    np.add.at(g, (ri, ti), 1.0)
    # 角向平滑，容忍量化误差
    g = cv2.GaussianBlur(g, (1, 5), 0)
    g /= max(1e-6, g.max())
    return g


def polar_rot_sim(a, b):
    """角向循环相关，取最大相似度 + 对应转角。"""
    # 用 FFT 做循环互相关（对每个 r 行独立，等价于整体角向平移）
    A = np.fft.rfft(a, axis=1)
    B = np.fft.rfft(b, axis=1)
    cc = np.fft.irfft(A * np.conj(B), axis=1)     # (nr, nt)
    per_shift = cc.sum(axis=0)
    k = int(np.argmax(per_shift))
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    sim = float(per_shift[k] / denom) if denom > 0 else 0.0
    ang = k * 360.0 / a.shape[1]
    return sim, ang


def norm_bbox(mask, canvas=128):
    """按 bbox 最长边归一（保留长宽比），质心居中。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.zeros((canvas, canvas), np.uint8)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    sub = mask[y0:y1, x0:x1]
    s = canvas * 0.86 / max(sub.shape)
    M = np.array([[s, 0, canvas / 2 - s * (xs.mean() - x0)],
                  [0, s, canvas / 2 - s * (ys.mean() - y0)]], np.float32)
    return cv2.warpAffine(mask, M, (canvas, canvas), flags=cv2.INTER_NEAREST)


def iou_bin(a, b):
    A, B = a > 127, b > 127
    u = np.count_nonzero(A | B)
    return float(np.count_nonzero(A & B) / u) if u else 0.0


cases = []
for seed in range(40):
    b = make_board(seed=seed, n=4, hard=(seed % 3 == 0))
    cases.append((b.piece_masks, b.slot_masks, b.truth, b.meta["kinds"]))

for name, fn in (
    ("A: 现有 rot_iou(96,area归一,10°)", None),
    ("B: rot_iou(128,bbox归一,5°)", None),
    ("C: 极坐标循环相关", None),
    ("D: B + C 平均", None),
):
    tp, fp, okb = [], [], 0
    for pm, sm_, truth, kinds in cases:
        n = len(pm)
        if name.startswith("A"):
            P = [sm._norm_mask(m) for m in pm]
            S = [sm._norm_mask(m) for m in sm_]
            step = 10
        elif name.startswith("B"):
            P = [norm_bbox(m) for m in pm]
            S = [norm_bbox(m) for m in sm_]
            step = 5
        else:
            P = [polar_sig(m) for m in pm]
            S = [polar_sig(m) for m in sm_]
        cost = np.zeros((n, n), np.float32)
        for i in range(n):
            if name.startswith("C") or name.startswith("D"):
                ci = [polar_rot_sim(P[i], S[j])[0] for j in range(n)]
            if name.startswith("A") or name.startswith("B"):
                W = sm.ScoreWeights(angle_step=step)
                rots, _ = sm._rot_masks(P[i], step)
                bi = [sm.rot_iou(rots, type("S", (), {"nmask": S[j]})(), W)[0]
                      for j in range(n)]
            if name.startswith("D"):
                cost[i] = 0.5 * np.array(bi) + 0.5 * np.array(ci)
            elif name.startswith("C"):
                cost[i] = ci
            else:
                cost[i] = bi
        assign = sm._hungarian(cost)
        okb += int(all(assign[k] == v for k, v in enumerate(truth)))
        for i in range(n):
            for j in range(n):
                (tp if truth[i] == j else fp).append(cost[i, j])
    tp, fp = np.array(tp), np.array(fp)
    d = (tp.mean() - fp.mean()) / max(1e-6, np.sqrt((tp.var() + fp.var()) / 2))
    print(f"{name:34s} board={okb}/{len(cases)}={okb/len(cases):.0%}  "
          f"true={tp.mean():.3f} false={fp.mean():.3f}  d'={d:+.2f}  "
          f"重叠={np.mean(fp >= np.percentile(tp, 10)):.1%}")
