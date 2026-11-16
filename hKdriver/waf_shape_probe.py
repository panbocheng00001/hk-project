# -*- coding: utf-8 -*-
"""waf_shape_probe.py —— 华为云 WAF「形状对齐」滑块：离线解析 v2
=====================================================================
输入：验证码面板截图（整页）
输出：
    panel        面板 bbox
    shapes[]     每个形状的中心(面板坐标/整图坐标)、bbox、色相、填充率、族
    pairs[]      「实心 ↔ 空心」配对候选，含 dx（滑块需要右移的像素）

v2 相对 v1 的改动（v1 在 172003 上把棕色实心判成空心、还漏了暗色形状）：
    · 背景色改为「面板内众数颜色」，用色差（欧氏距离）出掩膜 —— 不再依赖绝对 HSV 阈值
    · 掩膜先开运算去掉 1px 网格线，再闭运算合并描边
    · 实心判定：内缩 35% 区域的填充率（空心轮廓中心必然是背景色）
    · 裁掉面板下部 22%（滑轨 + Submit），避免把滑块误当形状

用法：
    python waf_shape_probe.py <png> [--debug out.png] [--brief]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


def load(p: Path):
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"读不出图片: {p}")
    return img


def find_panel(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 80).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 200 or h < 200:
            continue
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    return best


def bg_color(roi):
    """面板底色 = 出现次数最多的颜色（量化到 8 级）。"""
    q = (roi // 8).reshape(-1, 3)                       # 每通道 0..31（BGR 顺序）
    b = q[:, 0].astype(np.int32); g = q[:, 1].astype(np.int32); r = q[:, 2].astype(np.int32)
    packed = b * 1024 + g * 32 + r
    code = Counter(packed.tolist()).most_common(1)[0][0]
    bb, gg, rr = code // 1024, (code // 32) % 32, code % 32
    return np.array([bb, gg, rr], dtype=np.float32) * 8 + 4      # → BGR


def shapes_in(roi, bg, thr=42.0):
    diff = np.linalg.norm(roi.astype(np.float32) - bg, axis=2)
    mask = (diff > thr).astype(np.uint8) * 255
    # 不做开运算：空心形状是 1~2px 细线，一开就被擦掉；网格线靠 thr 排除（色差约 10）
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        area = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)
        if area < 110 or w < 10 or h < 10 or w > 90 or h > 90:
            continue
        inner = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(inner, [c], -1, 255, -1)
        # 内缩 35%：这时只能用**原始掩膜**看中心有没有彩色彩
        # （若用填充后的轮廓算，空心环内部也会被填满，永远判成实心）
        kx = max(1, int(w * 0.35)); ky = max(1, int(h * 0.35))
        core = mask[y + ky:y + h - ky, x + kx:x + w - kx]
        core_fill = (cv2.countNonZero(core) / float(core.size)) if core.size else 0.0
        approx = cv2.approxPolyDP(c, 0.05 * cv2.arcLength(c, True), True)
        k = len(approx)
        fam = {3: "tri", 4: "quad", 5: "penta", 6: "hexa"}.get(k, f"poly{k}")
        if k >= 8:
            fam = "round"
        # 颜色：只统计掩膜命中的像素（空心形状不会被内部背景稀释）
        pix = roi[y:y + h, x:x + w][mask[y:y + h, x:x + w] > 0]
        if len(pix):
            b, g, r = pix.mean(axis=0)
        else:
            b = g = r = 0
        out.append(dict(
            solid=core_fill > 0.55, core_fill=round(float(core_fill), 3),
            verts=k, fam=fam, w=w, h=h, area=int(area),
            cx=x + w / 2.0, cy=y + h / 2.0,
            rgb=[int(r), int(g), int(b)],
        ))
    out.sort(key=lambda d: (round(d["cy"], -1), d["cx"]))
    return out, mask


def pair_up(shapes):
    cands = []
    for s in shapes:
        if not s["solid"]:
            continue
        for h in shapes:
            if h["solid"]:
                continue
            score = 0.0
            score += 100 if s["fam"] == h["fam"] else -60
            score -= abs(s["w"] - h["w"]) * 2.0
            score -= abs(s["h"] - h["h"]) * 2.0
            score -= abs(s["cy"] - h["cy"]) * 0.8          # 同一行更像一对
            cands.append(dict(score=round(score, 1), dx=round(h["cx"] - s["cx"], 1),
                              dy=round(h["cy"] - s["cy"], 1), solid=s, hollow=h))
    cands.sort(key=lambda d: -d["score"])
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("png")
    ap.add_argument("--debug", default="")
    ap.add_argument("--brief", action="store_true")
    a = ap.parse_args()
    img = load(Path(a.png))
    H, W = img.shape[:2]
    panel = find_panel(img)
    if not panel:
        print(json.dumps({"error": "找不到深色面板", "size": [W, H]}, ensure_ascii=False))
        return 1
    px, py, pw, ph = panel
    cut = py + int(ph * 0.78)                     # 去掉滑轨 + Submit
    roi = img[py:cut, px:px + pw]
    bg = bg_color(roi)
    shapes, mask = shapes_in(roi, bg)
    for s in shapes:
        s["panel_xy"] = [round(px + s["cx"]), round(py + s["cy"])]
        s["cx"] = round(s["cx"], 1); s["cy"] = round(s["cy"], 1)
    cands = pair_up(shapes)

    brief = dict(
        image=str(a.png), size=[W, H], panel=panel, bg_rgb=[int(v) for v in bg[::-1]],
        solids=sum(1 for s in shapes if s["solid"]), hollows=sum(1 for s in shapes if not s["solid"]),
        shapes=[dict(xy=s["panel_xy"], fam=s["fam"], solid=s["solid"], w=s["w"], h=s["h"]) for s in shapes],
        pairs=[dict(score=c["score"], dx=c["dx"], dy=c["dy"],
                    solid=c["solid"]["panel_xy"], hollow=c["hollow"]["panel_xy"],
                    fam=c["solid"]["fam"]) for c in cands[:5]],
        best_dx=(cands[0]["dx"] if cands else None),
    )
    print(json.dumps(brief if a.brief else dict(brief, shapes_full=shapes), ensure_ascii=False, indent=2))

    if a.debug:
        vis = img.copy()
        cv2.rectangle(vis, (px, py), (px + pw, py + ph), (0, 255, 255), 2)
        for s in shapes:
            col = (0, 0, 255) if s["solid"] else (0, 255, 0)
            x0 = int(px + s["cx"] - s["w"] / 2); y0 = int(py + s["cy"] - s["h"] / 2)
            cv2.rectangle(vis, (x0, y0), (x0 + s["w"], y0 + s["h"]), col, 2)
            cv2.putText(vis, f'{s["fam"]}{"S" if s["solid"] else "H"}', (x0, y0 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)
        for c in cands[:1]:
            cv2.line(vis, tuple(c["solid"]["panel_xy"]), tuple(c["hollow"]["panel_xy"]), (255, 0, 255), 2)
        cv2.imwrite(a.debug, vis)
        print("debug ->", a.debug, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
