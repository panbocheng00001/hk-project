# -*- coding: utf-8 -*-
"""iterate_solve.py —— 自适应逼近：不预设比例，边拖边测斜率，迭代到 dx≈0
=====================================================================
为什么不用固定比例：
    2026-11-16 两次实测的「图形位移/手柄位移」不一致（+0.317 与 -0.15），
    说明映射方向/系数会随题目或位置变化。所以改成在线辨识：
        拖一小步 → 量 dx 变化 → 反推斜率 → 按斜率算下一步 → 收敛

判据：
    图形位移 dx 的绝对值 < 5px 视为对齐，然后点 Submit。

用法：
    python iterate_solve.py            # 最多 8 轮，不提交
    python iterate_solve.py --submit   # 收敛后点 Submit
"""
from __future__ import annotations

import json
import sys
import time

from PIL import ImageGrab

import waf_drag as W
import live_solve as L

HANDLE_INSET = (28, 350)          # 面板内手柄初始位置（canvas 实测）


def measure():
    img = L.grab()
    panel = L.find_panel2(img)
    if not panel:
        return None, None, None
    shapes, pr = L.pair_dx2(img, panel)
    return panel, pr, shapes


def handle_pos(panel, drag_done):
    """手柄当前屏幕坐标：面板内 (28,350) 起，加上累计拖动量。"""
    px, py, _, _ = panel
    return (px + HANDLE_INSET[0] + drag_done, py + HANDLE_INSET[1])


def main():
    submit = "--submit" in sys.argv
    hwnd, _ = W.find_w1_window()
    if not hwnd:
        print(json.dumps({"err": "找不到窗口"}, ensure_ascii=False)); return 2
    W.focus(hwnd); time.sleep(0.5)

    panel, pr, _ = measure()
    if not panel or not pr:
        print(json.dumps({"err": "没有验证码面板或配不上对"}, ensure_ascii=False)); return 3
    print(json.dumps({"panel": panel, "dx0": round(pr["dx"], 1),
                      "solid": pr["solid"], "hollow": pr["hollow"],
                      "shape_dist": pr.get("shape_dist")}, ensure_ascii=False), flush=True)

    slope = None          # d(dx) / d(拖右为正)
    drag_done = 0
    hist = []
    for i in range(8):
        dx = pr["dx"]
        if abs(dx) < 5:
            print(json.dumps({"converged": True, "dx": round(dx, 1), "rounds": i}, ensure_ascii=False))
            break
        if slope is None:
            step = 60 if dx < 0 else -60        # 先探一步，方向按 dx 的相反猜
        else:
            step = -dx / slope if abs(slope) > 0.02 else (60 if dx < 0 else -60)
            step = max(-200, min(200, step))
        # 手柄行程保护
        hx0 = panel[0] + HANDLE_INSET[0]
        new_abs = drag_done + step
        step = max(-(hx0 - panel[0]) + 2, min(360 - (HANDLE_INSET[0]), new_abs) - drag_done)
        if abs(step) < 8:
            step = 8 if dx < 0 else -8
        h = (panel[0] + HANDLE_INSET[0] + drag_done, panel[1] + HANDLE_INSET[1])
        W.drag(h, step)
        time.sleep(0.55)
        prev_dx = dx
        panel2, pr2, _ = measure()
        if not pr2:
            print(json.dumps({"round": i, "note": "拖动后测不到配对了", "step": round(step, 1)}, ensure_ascii=False))
            break
        drag_done += step
        slope = (pr2["dx"] - prev_dx) / step if step else slope
        pr = pr2
        hist.append(dict(round=i, step=round(step, 1), dx_before=round(prev_dx, 1),
                         dx_after=round(pr2["dx"], 1), slope=round(slope, 3) if slope else None))
        print(json.dumps(hist[-1], ensure_ascii=False), flush=True)

    print(json.dumps({"final_dx": round(pr["dx"], 1) if pr else None,
                      "converged": bool(pr and abs(pr["dx"]) < 5),
                      "handle_drag_total": round(drag_done, 1)}, ensure_ascii=False))

    if submit and pr and abs(pr["dx"]) < 5:
        px, py, pw, ph = panel
        sx, sy = px + pw / 2, py + int(ph * 0.945)
        W.move_to(sx, sy); time.sleep(0.12)
        W.button(True); time.sleep(0.07); W.button(False)
        print(json.dumps({"submitted": [round(sx), round(sy)]}, ensure_ascii=False))
    ImageGrab.grab().save("runs/_iter_final.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
