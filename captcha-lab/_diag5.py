"""临时诊断：用真值 mask 逐通道测判别力（true vs false 分布重叠）。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm


def mk(nmask, mask, contour):
    # 只为了复用 score_pair 需要的字段
    class S:
        pass
    s = S()
    s.nmask = nmask
    s.mask = mask
    s.contour = contour
    s.hu = sm._hu_log(contour)
    s.fd = sm._fourier_desc(contour)
    s.radial = sm._radial_sig(contour, *np.mean(contour.reshape(-1, 2), axis=0))
    return s


W = sm.ScoreWeights()
ch = {k: ([], []) for k in ("rot_iou", "hu", "fd", "radial")}
for seed in range(30):
    b = make_board(seed=seed, n=4, hard=False)
    ps, ss = [], []
    for i in range(4):
        c = max(__import__("cv2").findContours(b.piece_masks[i], 0, 2)[0], key=__import__("cv2").contourArea)
        ps.append(mk(sm._norm_mask(b.piece_masks[i]), b.piece_masks[i], c))
    for j in range(4):
        c = max(__import__("cv2").findContours(b.slot_masks[j], 0, 2)[0], key=__import__("cv2").contourArea)
        ss.append(mk(sm._norm_mask(b.slot_masks[j]), b.slot_masks[j], c))
    for i in range(4):
        rots, _ = sm._rot_masks(ps[i].nmask, W.angle_step)
        for j in range(4):
            sc, d = sm.score_pair(ps[i], ss[j], rots, W, use_app=False)
            tgt = ch["rot_iou"]
            (tgt[0] if b.truth[i] == j else tgt[1]).append(d["rot_iou"])
            for k in ("hu", "fd", "radial"):
                (ch[k][0] if b.truth[i] == j else ch[k][1]).append(d[k])

for k, (t, f) in ch.items():
    t, f = np.array(t), np.array(f)
    print(f"{k:8s} true={t.mean():.3f}±{t.std():.3f}  false={f.mean():.3f}±{f.std():.3f}  "
          f"分离度(d')={ (t.mean()-f.mean())/max(1e-6,np.sqrt((t.var()+f.var())/2)):+.2f}  "
          f"重叠区={np.mean(f>=np.percentile(t,10)):.1%}")
