"""临时诊断：用 GT mask 比较融合策略（原始加权 / 板内秩融合 / 板内 z-score）。"""
import itertools
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

W = sm.ScoreWeights()


def shape_from_mask(g, label):
    cnts, _ = cv2.findContours(g, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(c)
    return sm.Shape(x=bx, y=by, w=bw, h=bh, mask=g, nmask=sm._norm_mask(g),
                    contour=c, hu=sm._hu_log(c), fd=sm._fourier_desc(c),
                    radial=sm._radial_sig(c, bx + bw / 2.0, by + bh / 2.0),
                    hist=np.zeros((16, 16), np.float32), color=np.zeros(4, np.float32),
                    area=float(cv2.contourArea(c)), ar=bw / max(1.0, bh),
                    solidity=1.0, extent=1.0, compact=0.0, label=label, ox=0, oy=0)


cases = []
for seed in range(40):
    b = make_board(seed=seed, n=4 + seed % 3, hard=(seed % 3 == 0))
    n = len(b.pieces)
    ps = [shape_from_mask(b.piece_masks[i], f"p{i}") for i in range(n)]
    ss = [shape_from_mask(b.slot_masks[i], f"s{i}") for i in range(n)]
    if any(s is None for s in ps + ss):
        continue
    rots = [sm._rot_masks(p.nmask, W.angle_step)[0] for p in ps]
    ch = {k: np.zeros((n, n), np.float32) for k in ("rot_iou", "fd", "hu", "radial")}
    ang = np.zeros((n, n), np.float32)
    for i, p in enumerate(ps):
        for j, s in enumerate(ss):
            _, d = sm.score_pair(p, s, rots[i], W, use_app=False)
            for k in ch:
                ch[k][i, j] = d[k]
            ang[i, j] = d["ang"]
    cases.append((ch, ang, b.truth))
print(f"cases={len(cases)}")


def run(fn, name):
    ok = 0
    for ch, ang, truth in cases:
        cost = fn(ch, ang)
        assign = sm._hungarian(cost)
        ok += int(all(assign[k] == v for k, v in enumerate(truth)))
    print(f"  {name:34s} board_acc={ok}/{len(cases)} = {ok/len(cases):.1%}")


def raw(ch, ang, wts):
    n = ch["rot_iou"].shape[0]
    cost = sum(wts[k] * ch[k] for k in wts)
    pen = np.vectorize(lambda a: sm._angle_pen(a, W))(ang)
    return cost - pen


def rank_norm(m):
    """板内按列（槽位）做秩归一：每列独立排名，消除通道量纲。"""
    n, m2 = m.shape
    out = np.zeros_like(m)
    for j in range(m2):
        order = np.argsort(-m[:, j])
        for r, i in enumerate(order):
            out[i, j] = 1.0 - r / max(1, n - 1)
    return out


def z_norm(m):
    mu, sd = m.mean(), m.std()
    return (m - mu) / (sd if sd > 1e-6 else 1.0)


print("\n原始加权（当前默认）:")
for wts in (
    dict(rot_iou=0.46, fd=0.16, hu=0.10, radial=0.10),
    dict(rot_iou=0.70, fd=0.15, hu=0.00, radial=0.15),
    dict(rot_iou=1.00, fd=0.25, hu=0.00, radial=0.20),
    dict(rot_iou=1.00, fd=0.00, hu=0.00, radial=0.00),
    dict(rot_iou=0.00, fd=1.00, hu=0.00, radial=0.00),
):
    run(lambda c, a, w=wts: raw(c, a, w), f"raw {wts}")

print("\n板内秩融合:")
for wts in (dict(rot_iou=1.0, fd=1.0, radial=0.5, hu=0.0),
            dict(rot_iou=1.0, fd=0.6, radial=0.4, hu=0.0),
            dict(rot_iou=1.0, fd=0.0, radial=0.0, hu=0.0)):
    run(lambda c, a, w=wts: sum(w[k] * rank_norm(c[k]) for k in w)
        - np.vectorize(lambda x: sm._angle_pen(x, W))(a), f"rank {wts}")

print("\n板内 z-score 融合:")
for wts in (dict(rot_iou=1.0, fd=1.0, radial=0.5, hu=0.0),
            dict(rot_iou=1.0, fd=0.6, radial=0.4, hu=0.0)):
    run(lambda c, a, w=wts: sum(w[k] * z_norm(c[k]) for k in w)
        - np.vectorize(lambda x: sm._angle_pen(x, W))(a), f"z {wts}")
