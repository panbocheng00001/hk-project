"""抗识别评测：用红队基线攻击每个出题预设，输出一次通过率与抗识别得分。

  直线拼图  攻击方 = solver.gap.locate（背景图 + 滑块图）
  曲线重合  攻击方 = solver.curve_match.contour_align（只用客户端可见的信息）

用法：
    python bench_robustness.py              # 两类题型，每个预设 100 题
    python bench_robustness.py 300          # 每个预设 300 题（更稳的数字）
    python bench_robustness.py --slider     # 只跑直线拼图
    python bench_robustness.py --curve      # 只跑曲线重合
    python bench_robustness.py --gallery    # 额外输出 out/presets.png、out/curve_presets.png
"""
from __future__ import annotations

import os
import sys
from typing import List

import cv2
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from defense import curve
from defense.challenge import generate
from defense.robustness import CURVE_PRESETS, PRESETS, run_presets


def _tile(name: str, vis: np.ndarray) -> np.ndarray:
    label = np.full((22, vis.shape[1], 3), 30, np.uint8)
    cv2.putText(label, name, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (240, 240, 240), 1, cv2.LINE_AA)
    return cv2.copyMakeBorder(np.vstack([label, vis]), 4, 4, 4, 4,
                              cv2.BORDER_CONSTANT, value=(30, 30, 30))


def _save_grid(tiles: List[np.ndarray], path: str) -> None:
    if len(tiles) % 2:
        tiles.append(np.full_like(tiles[0], 30))
    rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cv2.imencode(".png", np.vstack(rows))[1].tofile(path)
    print(f"gallery -> {path}")


def save_gallery(path: str = "out/presets.png", seed: int = 5) -> None:
    """直线拼图：每个预设一张样图，绿框标真缺口、红框标假缺口。"""
    tiles = []
    for name, cfg in PRESETS.items():
        ch = generate(cfg, seed=seed)
        vis = ch.bg.copy()
        s = ch.side
        cv2.rectangle(vis, (ch.gap_x, ch.gap_y), (ch.gap_x + s, ch.gap_y + s), (0, 220, 0), 2)
        for dx, dy, dw, dh in ch.decoys:
            cv2.rectangle(vis, (dx, dy), (dx + dw, dy + dh), (0, 0, 255), 1)
        tiles.append(_tile(name, vis))
    _save_grid(tiles, path)


def save_curve_gallery(path: str = "out/curve_presets.png", seed: int = 5) -> None:
    """曲线重合：实心图形画在起点，绿圈标答案、红圈标路径上的假目标。"""
    tiles = []
    for name, cfg in CURVE_PRESETS.items():
        ch = curve.generate(cfg, seed=seed)
        vis = curve.render(ch, 0.0)
        r = int(cfg.shape_size * 0.75)
        tx, ty = ch.path[ch.target_idx]
        cv2.circle(vis, (int(tx), int(ty)), r, (0, 220, 0), 2)
        for i in ch.path_decoy_idx:
            x, y = ch.path[i]
            cv2.circle(vis, (int(x), int(y)), r, (0, 0, 255), 1)
        tiles.append(_tile(name, vis))
    _save_grid(tiles, path)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 100
    only_slider, only_curve = "--slider" in sys.argv, "--curve" in sys.argv
    do_slider = only_slider or not only_curve
    do_curve = only_curve or not only_slider
    if "--gallery" in sys.argv:
        if do_slider:
            save_gallery()
        if do_curve:
            save_curve_gallery()

    print(f"samples/preset = {n}")
    print("top1 = 机器一次蒙对的比例（越低越好）  resist = 1 - top1")
    if do_slider:
        print("\n[直线拼图] attacker = solver.gap.locate (bg + piece)")
        for r in run_presets(n):
            print(r.row())
    if do_curve:
        print("\n[曲线重合] attacker = solver.curve_match.contour_align (bg + piece + path)")
        for r in run_presets(n, presets=CURVE_PRESETS):
            print(r.row())


if __name__ == "__main__":
    main()
