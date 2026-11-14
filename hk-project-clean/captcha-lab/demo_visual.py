"""可视化对比：真值缺口(GREEN) vs 预测 top1(RED) / top2(YELLOW)。"""
from __future__ import annotations

import os
import sys
import cv2
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from solver.synth import make_sample
from solver import gap


def draw_one(img, piece, gt, cands) -> np.ndarray:
    vis = img.copy()
    gx, gy, gw, gh = gt
    # 真值：绿色虚线框 + 中线
    for t in range(0, gw, 6):
        cv2.line(vis, (gx + t, gy), (gx + min(t + 3, gw), gy), (0, 255, 0), 2)
        cv2.line(vis, (gx + t, gy + gh), (gx + min(t + 3, gw), gy + gh), (0, 255, 0), 2)
    cv2.line(vis, (int(gx + gw / 2), gy), (int(gx + gw / 2), gy + gh), (0, 255, 0), 1)

    colors = [(0, 0, 255), (0, 200, 255), (255, 0, 255)]
    for i, c in enumerate(cands):
        col = colors[i % len(colors)]
        cv2.rectangle(vis, (c.x, c.y), (c.x + c.w, c.y + c.h), col, 2)
        err = c.cx - (gx + gw / 2)
        cv2.putText(vis, f"#{i} err={err:+.1f}px", (4, 16 + i * 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
    return vis


def main(out: str = "out/demo_compare.png", n: int = 3) -> None:
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tiles = []
    for i in range(n):
        img, piece, gt = make_sample(seed=100 + i)
        cands = gap.locate(img, piece=piece, topk=3)
        tiles.append(draw_one(img, piece, gt, cands))
        e = abs(cands[0].cx - (gt[0] + gt[2] / 2)) if cands else -1
        print(f"sample{100+i}: top1_err={e:.1f}px  cands={[(round(c.cx,1), c.method) for c in cands]}")
    grid = np.hstack([cv2.copyMakeBorder(t, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=(30, 30, 30))
                      for t in tiles])
    cv2.imencode(".png", grid)[1].tofile(out)
    print("saved ->", out)


if __name__ == "__main__":
    main()
