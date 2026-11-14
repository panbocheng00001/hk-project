"""临时诊断：分层归因 —— 完美分割下配对准确率是多少（匹配层天花板）。"""
import numpy as np
import cv2
from solver.synth_match import make_board
from solver import shape_match as sm

W = sm.ScoreWeights()


def shape_from_mask(g: np.ndarray, label: str):
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
                    solidity=1.0, extent=1.0, compact=0.0, label=label,
                    ox=0, oy=0)


ok_n = tot = 0
for seed in range(25):
    b = make_board(seed=seed, n=4 + seed % 3, hard=(seed % 3 == 0))
    n = len(b.pieces)
    ps = [shape_from_mask(b.piece_masks[i], f"p{i}") for i in range(n)]
    ss = [shape_from_mask(b.slot_masks[i], f"s{i}") for i in range(n)]
    if any(s is None for s in ps + ss):
        continue
    r = sm.match(ps, ss, W, use_app=False)
    pred = {p.piece: p.slot for p in r.pairs}
    tot += 1
    ok_n += int(all(pred.get(k) == v for k, v in enumerate(b.truth)))

print(f"GT-mask 配对天花板: board_acc = {ok_n}/{tot} = {ok_n/max(1,tot):.1%}")
