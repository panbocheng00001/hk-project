# -*- coding: utf-8 -*-
"""live_solve.py —— 一次进程内完成「前置→抓屏→定位→算dx→(拖→提交)」
=====================================================================
为什么不复用 waf_drag.py 的散装调用：
    每条 pwsh 命令之间前台焦点会被终端抢回，抓屏抓到的是别的窗口。
    所以 focus 和 grab 必须落在同一个 Python 进程里。

用法：
    python live_solve.py            # 只检测，输出标注图 runs/_live_marked.png
    python live_solve.py --go       # 检测 + 按 0.317 比例拖 + 提交
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

import waf_drag as W


def grab():
    return np.array(ImageGrab.grab())[:, :, ::-1].copy()


def find_panel2(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 80).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (360 <= w <= 470 and 360 <= h <= 470):
            continue
        if abs(w / float(h) - 0.98) > 0.12:
            continue
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    return best


def find_handle2(img, panel):
    """面板底部 45% 找蓝色圆点/圆环（放宽色阈，含描边）。"""
    px, py, pw, ph = panel
    y0 = py + int(ph * 0.55)
    roi = img[y0:py + ph, px:px + pw]
    b, g, r = (roi[:, :, i].astype(int) for i in range(3))
    blue = ((b > 110) & (b - r > 45) & (b - g > 25)).astype(np.uint8) * 255
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cands = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if not (8 <= w <= 60 and 8 <= h <= 60):
            continue
        if area < 40:
            continue
        M = cv2.moments(c)
        if M["m00"] <= 0:
            continue
        cands.append(dict(cx=px + M["m10"] / M["m00"], cy=y0 + M["m01"] / M["m00"],
                          w=w, h=h, area=int(area)))
    cands.sort(key=lambda d: -d["area"])
    return cands


def pair_dx2(img, panel):
    px, py, pw, ph = panel
    top = py + int(ph * 0.10)
    bot = py + int(ph * 0.62)
    roi = img[top:bot, px:px + pw]
    q = (roi // 8).reshape(-1, 3)
    code = q[:, 0].astype(np.int32) * 1024 + q[:, 1].astype(np.int32) * 32 + q[:, 2]
    vals, counts = np.unique(code, return_counts=True)
    c0 = vals[counts.argmax()]
    bg = np.array([c0 // 1024, (c0 // 32) % 32, c0 % 32], dtype=np.float32) * 8 + 4
    diff = np.linalg.norm(roi.astype(np.float32) - bg, axis=2)
    mask = (diff > 40).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    shapes = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 14 or h < 14 or w > 100 or h > 100:
            continue
        kx, ky = max(1, int(w * 0.35)), max(1, int(h * 0.35))
        core = mask[y + ky:y + h - ky, x + kx:x + w - kx]
        fill = cv2.countNonZero(core) / float(core.size) if core.size else 0
        approx = cv2.approxPolyDP(c, 0.05 * cv2.arcLength(c, True), True)
        shapes.append(dict(cx=px + x + w / 2.0, cy=top + y + h / 2.0,
                           w=w, h=h, fill=round(float(fill), 2), solid=bool(fill > 0.5),
                           verts=int(len(approx)), contour=c))
    # 轮廓只用于打分，输出时去掉（不可 JSON 序列化）
    def _clean(s):
        return {k: v for k, v in s.items() if k != "contour"}
    sol = [s for s in shapes if s["solid"]]
    hol = [s for s in shapes if not s["solid"]]
    best = None
    for s in sol:
        for t in hol:
            # 拖动是纯水平的：y 差超过 30 基本不可能是同一对（防止配对跳）
            if abs(t["cy"] - s["cy"]) > 30:
                continue
            try:
                ms = float(cv2.matchShapes(s["contour"], t["contour"],
                                           cv2.CONTOURS_MATCH_I1, 0))
            except Exception:
                ms = 5.0
            if s["verts"] != t["verts"] and ms > 0.18:
                continue                                  # 三角≠星形，直接不配
            sc = (-ms * 100 - abs(t["cy"] - s["cy"]) * 3.0
                  - abs(s["w"] - t["w"]) - abs(s["h"] - t["h"]))
            if best is None or sc > best[0]:
                best = (sc, s, t, ms)
    pair = None
    if best:
        _, s, t, ms = best
        pair = dict(dx=t["cx"] - s["cx"], solid=[round(s["cx"]), round(s["cy"])],
                    hollow=[round(t["cx"]), round(t["cy"])],
                    shape_dist=round(ms, 3), verts=[s["verts"], t["verts"]],
                    dy=round(t["cy"] - s["cy"], 1))
    return [_clean(s) for s in shapes], pair


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()

    hwnd, title = W.find_w1_window()
    print(json.dumps({"hwnd": hwnd, "title": title}, ensure_ascii=False), flush=True)
    if not hwnd:
        return 2
    ok = W.focus(hwnd)
    time.sleep(0.45)
    img = grab()
    print(json.dumps({"focus_ok": ok, "screen": list(img.shape[1::-1])}, ensure_ascii=False), flush=True)

    panel = find_panel2(img)
    if not panel:
        ImageGrab.grab().save("runs/_live_marked.png")
        print(json.dumps({"found": False}, ensure_ascii=False))
        return 3
    shapes, pr = pair_dx2(img, panel)
    hs = find_handle2(img, panel)
    print(json.dumps(dict(panel=panel, shapes=shapes, pair=pr,
                          handles=[{k: round(v, 1) if isinstance(v, float) else v for k, v in h.items()} for h in hs[:4]]),
                     ensure_ascii=False, indent=2), flush=True)

    vis = img.copy()
    px, py, pw, ph = panel
    cv2.rectangle(vis, (px, py), (px + pw, py + ph), (0, 255, 255), 2)
    for s in shapes:
        col = (0, 0, 255) if s["solid"] else (0, 255, 0)
        cv2.circle(vis, (int(s["cx"]), int(s["cy"])), 12, col, 2)
    for h in hs[:3]:
        cv2.circle(vis, (int(h["cx"]), int(h["cy"])), 14, (255, 0, 255), 2)
    cv2.imwrite("runs/_live_marked.png", vis)
    print("marked -> runs/_live_marked.png", flush=True)

    if not a.go:
        return 0
    if not pr or not hs:
        print(json.dumps({"skip": "缺 pair 或 handle"}, ensure_ascii=False)); return 4
    handle = hs[0]
    drag_px = pr["dx"] / W.TRAVEL_RATIO * a.scale
    print(json.dumps({"drag_px": round(drag_px, 1), "from": [round(handle["cx"]), round(handle["cy"])]}, ensure_ascii=False), flush=True)
    W.drag(handle, drag_px)
    time.sleep(0.5)
    img2 = grab()
    _, pr2 = pair_dx2(img2, panel)
    out = dict(need_left=round(pr2["dx"], 1) if pr2 else None)
    if pr2 and abs(pr2["dx"]) > 5:
        hs2 = find_handle2(img2, panel)
        if hs2:
            W.drag(hs2[0], pr2["dx"] / W.TRAVEL_RATIO)
            time.sleep(0.45)
            img3 = grab()
            _, pr3 = pair_dx2(img3, panel)
            out["need_left2"] = round(pr3["dx"], 1) if pr3 else None
    # 提交
    sx, sy = px + pw / 2, py + int(ph * 0.945)
    W.move_to(sx, sy); time.sleep(0.12)
    W.button(True); time.sleep(0.07); W.button(False)
    out["submit"] = [round(sx), round(sy)]
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
