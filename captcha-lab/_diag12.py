"""临时诊断：在 GT mask 与 提取 mask 两级上网格搜索权重。"""
import itertools
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm


def shape_from_mask(g, label):
    cnts, _ = cv2.findContours(g, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(c)
    return sm.Shape(x=bx, y=by, w=bw, h=bh, mask=g, nmask=sm._norm_mask(g),
                    contour=c, hu=sm._hu_log(c), fd=sm._fourier_desc(c),
                    radial=sm._radial_sig(c, bx + bw / 2.0, by + bh / 2.0),
                    polar=sm._polar_sig(g),
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
    cases.append((ps, ss, b.truth, b.meta["kinds"], b.meta["hard"]))
print(f"cases={len(cases)} (hard={sum(1 for c in cases if c[4])})")


def evaluate(w, use_app):
    ok = okh = 0
    nh = 0
    for ps, ss, truth, kinds, hard in cases:
        r = sm.match(ps, ss, w, use_app=use_app)
        pred = {p.piece: p.slot for p in r.pairs}
        good = all(pred.get(k) == v for k, v in enumerate(truth))
        ok += int(good)
        if hard:
            nh += 1
            okh += int(good)
    return ok, nh, okh


print("\n=== 单一/组合通道（GT mask, 无外观）===")
configs = {
    "polar only": dict(polar=1.0, rot_iou=0, fd=0, radial=0, hu=0),
    "fd only": dict(polar=0, rot_iou=0, fd=1.0, radial=0, hu=0),
    "rot_iou only": dict(polar=0, rot_iou=1.0, fd=0, radial=0, hu=0),
    "polar+fd": dict(polar=0.7, rot_iou=0, fd=0.3, radial=0, hu=0),
    "polar+radial": dict(polar=0.7, rot_iou=0, fd=0, radial=0.3, hu=0),
    "polar+rot_iou": dict(polar=0.6, rot_iou=0.4, fd=0, radial=0, hu=0),
    "P+R+F": dict(polar=0.5, rot_iou=0.2, fd=0.2, radial=0.1, hu=0),
    "P+F": dict(polar=0.6, rot_iou=0.0, fd=0.3, radial=0.1, hu=0),
}
for name, kw in configs.items():
    ok, nh, okh = evaluate(sm.ScoreWeights(**kw), False)
    print(f"  {name:16s} board={ok}/{len(cases)}={ok/len(cases):.0%}  hard={okh}/{nh}")

print("\n=== 角度惩罚 ===")
for pen in (0.0, 0.05, 0.10, 0.20):
    ok, nh, okh = evaluate(sm.ScoreWeights(polar=0.6, rot_iou=0.0, fd=0.3, radial=0.1,
                                           hu=0.0, upright_penalty=pen), False)
    print(f"  pen={pen:.2f} board={ok}/{len(cases)}={ok/len(cases):.0%}  hard={okh}/{nh}")
