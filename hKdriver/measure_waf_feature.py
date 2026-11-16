# -*- coding: utf-8 -*-
"""measure_waf_feature.py —— 把华为云 WAF「形状对齐」滑块的特征量化成 JSON
=====================================================================
输入：runs/captcha_*.png（canvas 层）、runs/captcha_bg_*.png（背景层）、
      runs/steps/2026-11-16/*.png（整页面板）
输出：runs/captcha_specimens/waf_captcha_features.json
      —— 颜色、几何、坐标全部是实测值，供识别规则/复刻服务直接引用

为什么单独量：
    写识别规则最怕「凭印象」。文案、尺寸、滑轨 y、手柄半径这些必须从像素里量出来，
    否则换分辨率/DPR 就会错位。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
OUT = RUNS / "captcha_specimens" / "waf_captcha_features.json"


def load(p):
    """canvas 层是 RGBA PNG（透明=底板），cv2 读会被当黑/灰底，
    统一用 PIL 读、按 alpha 合到白底，保证「背景色」是真实页面观感。"""
    from PIL import Image
    im = Image.open(str(p))
    if im.mode == "RGBA":
        a = np.array(im).astype(np.float32)
        alpha = a[:, :, 3:4] / 255.0
        rgb = a[:, :, :3]
        out = rgb * alpha + 255.0 * (1 - alpha)
        return cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def dom_color(img, box=None):
    a = img if box is None else img[box[1]:box[3], box[0]:box[2]]
    q = (a // 4).reshape(-1, 3)
    code = q[:, 0].astype(np.int64) * 65536 + q[:, 1].astype(np.int64) * 256 + q[:, 2]
    c = Counter(code.tolist()).most_common(1)[0][0]
    b, g, r = c // 65536, (c // 256) % 256, c % 256
    # 量化时 //4 过，这里必须乘回去（曾经漏乘，导致白底被读成 63 灰）
    return [int(min(255, r * 4 + 2)), int(min(255, g * 4 + 2)), int(min(255, b * 4 + 2))]


def measure_canvas(p):
    img = load(p)
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out = {"file": p.name, "size": [w, h], "bg_rgb": dom_color(img)}

    # 文案行：顶部 12% 内比底色暗的横向条带
    top = img[: int(h * 0.09)]
    tg = cv2.cvtColor(top, cv2.COLOR_BGR2GRAY)
    dark = (tg < 220).astype(np.uint8)
    rows = np.where(dark.sum(axis=1) > 20)[0]
    if len(rows):
        out["prompt_text_rows"] = [int(rows.min()), int(rows.max())]

    # 滑轨：中下部一条横向浅灰长线
    band = img[int(h * 0.7):h, :]
    bg_ = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    line = (np.abs(bg_.astype(int) - 200) < 26).astype(np.uint8)
    rowsum = line.sum(axis=1)
    if rowsum.max() > w * 0.4:
        ry = int(np.argmax(rowsum)) + int(h * 0.7)
        cols = np.where(line[ry - int(h * 0.7)] > 0)[0]
        out["track"] = {"y": ry, "x0": int(cols.min()), "x1": int(cols.max()),
                        "length": int(cols.max() - cols.min())}

    # 手柄：轨道上最蓝的圆
    b, g, r = (band[:, :, i].astype(int) for i in range(3))
    blue = ((b > 140) & (g > 55) & (g < 180) & (r < 130)).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if 10 <= ww <= 46 and 10 <= hh <= 46 and abs(ww - hh) <= 10:
            M = cv2.moments(c)
            if M["m00"] <= 0:
                continue
            cx = M["m10"] / M["m00"]; cy = M["m01"] / M["m00"] + int(h * 0.7)
            out["handle"] = {"cx": round(float(cx), 1), "cy": round(float(cy), 1),
                             "d": int(max(ww, hh)),
                             "rgb": [int(v) for v in cv2.mean(band, mask=cv2.drawContours(
                                 np.zeros(blue.shape, np.uint8), [c], -1, 255, -1))[:3][::-1]]}
            break

    # Submit 按钮：底部居中的大蓝色块
    low = img[int(h * 0.86):h, :]
    b2, g2, r2 = (low[:, :, i].astype(int) for i in range(3))
    bl = ((b2 > 180) & (g2 > 80) & (r2 < 130)).astype(np.uint8) * 255
    cnts2, _ = cv2.findContours(bl, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts2:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww > 100 and 20 < hh < 80:
            if best is None or ww * hh > best[2] * best[3]:
                best = (x, y + int(h * 0.86), ww, hh)
    if best:
        x, y, ww, hh = best
        out["submit"] = {"bbox": [x, y, ww, hh], "cx": x + ww // 2, "cy": y + hh // 2,
                         "rgb": dom_color(img, (x, y, x + ww, y + hh))}

    # 前景实心形状（题干那道要拖的）
    bgc = np.array(out["bg_rgb"][::-1], dtype=np.float32)
    diff = np.linalg.norm(img.astype(np.float32) - bgc, axis=2)
    mask = (diff > 40).astype(np.uint8) * 255
    mask[: int(h * 0.09)] = 0
    mask[int(h * 0.70):] = 0
    cnts3, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fps = []
    for c in cnts3:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww < 24 or hh < 24 or ww > 120 or hh > 120:
            continue
        kx, ky = max(1, int(ww * 0.35)), max(1, int(hh * 0.35))
        core = mask[y + ky:y + hh - ky, x + kx:x + ww - kx]
        fill = cv2.countNonZero(core) / float(core.size) if core.size else 0
        m = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(m, [c], -1, 255, -1)
        bm = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(bm, [c], -1, 255, 2)
        fps.append(dict(xy=[x + ww // 2, y + hh // 2], wh=[int(ww), int(hh)],
                        fill=round(float(fill), 2),
                        solid=bool(fill > 0.55),
                        rgb=[int(v) for v in cv2.mean(img, mask=bm)[:3][::-1]]))
    out["shapes"] = fps
    return out


def measure_bg(p):
    img = load(p)
    h, w = img.shape[:2]
    out = {"file": p.name, "size": [w, h], "bg_rgb": dom_color(img)}
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 网格线：比底色亮一档的细线，量间距
    col = gray.mean(axis=0); row = gray.mean(axis=1)
    peaks = [i for i in range(2, w - 2) if col[i] > col[i - 2] + 1.2 and col[i] > col[i + 2] + 1.2]
    if len(peaks) > 2:
        d = np.diff(peaks)
        out["grid_pitch_x"] = int(np.median(d))
    peaks2 = [i for i in range(2, h - 2) if row[i] > row[i - 2] + 1.2 and row[i] > row[i + 2] + 1.2]
    if len(peaks2) > 2:
        out["grid_pitch_y"] = int(np.median(np.diff(peaks2)))

    bgc = np.array(out["bg_rgb"][::-1], dtype=np.float32)
    diff = np.linalg.norm(img.astype(np.float32) - bgc, axis=2)
    mask = (diff > 40).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hs = []
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww < 14 or hh < 14 or ww > 130 or hh > 130:
            continue
        kx, ky = max(1, int(ww * 0.35)), max(1, int(hh * 0.35))
        core = mask[y + ky:y + hh - ky, x + kx:x + ww - kx]
        fill = cv2.countNonZero(core) / float(core.size) if core.size else 0
        approx = cv2.approxPolyDP(c, 0.05 * cv2.arcLength(c, True), True)
        m = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(m, [c], -1, 255, 2)
        hs.append(dict(xy=[x + ww // 2, y + hh // 2], wh=[int(ww), int(hh)],
                       fill=round(float(fill), 2), solid=bool(fill > 0.55),
                       verts=int(len(approx)),
                       rgb=[int(v) for v in cv2.mean(img, mask=m)[:3][::-1]]))
    hs.sort(key=lambda d: (d["xy"][1], d["xy"][0]))
    out["shapes"] = hs
    return out


def measure_travel_ratio(p_before, p_after):
    """同一道题「拖前 / 拖后」两张 canvas：手柄走了多少、图形走了多少。
    这个比值是滑块行程 ↔ 图形位移的换算系数，比 1:1 的假设更重要。

    手柄在拖后样本里常常检不出来（已拖过、颜色变深），此时用「浅灰轨道起点」
    当代理：实测 handle.cx ≈ track.x0 + 3.5。
    """
    a = measure_canvas(p_before)
    b = measure_canvas(p_after)

    def handle_x(m):
        if m.get("handle"):
            return m["handle"]["cx"]
        if m.get("track"):
            return m["track"]["x0"] + 3.5
        return None

    ha, hb = handle_x(a), handle_x(b)
    sa = [s for s in a.get("shapes", []) if s["solid"]]
    sb = [s for s in b.get("shapes", []) if s["solid"]]
    if ha is None or hb is None or len(sa) != 1 or len(sb) != 1:
        return None
    d_handle = hb - ha
    d_shape = sb[0]["xy"][0] - sa[0]["xy"][0]
    return dict(before=p_before.name, after=p_after.name,
                handle_x=[round(ha, 1), round(hb, 1)],
                shape_x=[sa[0]["xy"][0], sb[0]["xy"][0]],
                d_handle=round(d_handle, 1), d_shape=round(d_shape, 1),
                ratio=round(d_shape / d_handle, 3) if d_handle else None)


def main():
    res = {"measured_at": "2026-11-16", "source": "华为云 WAF (Server: CW) Quidditch 形状对齐滑块"}

    # 1) canvas 层样本（题目图形 + 滑轨 + 按钮）
    canv = sorted(RUNS.glob("captcha_17*.png"))
    canv = [p for p in canv if "bg" not in p.name]
    res["canvas_samples"] = [measure_canvas(p) for p in canv[-3:]]

    # 2) 背景层样本（空心轮廓 + 网格）
    bgs = sorted(RUNS.glob("captcha_bg_17*.png"))[-3:]
    res["bg_samples"] = [measure_bg(p) for p in bgs]

    # 3) 尺寸关系
    if res["canvas_samples"] and res["bg_samples"]:
        cw, ch = res["canvas_samples"][-1]["size"]
        bw, bh = res["bg_samples"][-1]["size"]
        res["layer_geometry"] = {"canvas": [cw, ch], "bg": [bw, bh],
                                 "same_width": cw == bw,
                                 "bg_covers_top_ratio": round(bh / ch, 3)}

    # 3.5) 行程比：滑块走了多少 ↔ 图形走了多少（最关键的换算系数）
    tr = []
    for a, b in [("captcha_172856.png", "captcha_172956.png"),
                 ("captcha_172956.png", "captcha_172959.png"),
                 ("captcha_172856.png", "captcha_172959.png")]:
        r = measure_travel_ratio(RUNS / a, RUNS / b)
        if r:
            tr.append(r)
    res["travel_ratio_samples"] = tr
    if tr:
        res["travel_ratio"] = round(float(np.median([x["ratio"] for x in tr])), 3)
        res["travel_ratio_note"] = (
            "图形位移 = 手柄位移 × travel_ratio（实测 ≈0.32，不是 1:1）。"
            "所以要拖 dx 像素，手柄要走 dx / travel_ratio（≈3.15×dx）；"
            "滑轨总长 369px ⇒ 图形最大可位移约 117px。")

    # 4) 文案（人工核对过的三种）
    res["prompts"] = [
        "Move the slider so the solid shape lands on the hollow one.",
        "Adjust the slider until the two shapes line up.",
        "Drag the slider until the solid and hollow shapes overlap.",
    ]
    res["prompt_lang_variants"] = ["zh-CN 版未捕获（本次会话 lang=sc 仍出英文题干）"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print("\n->", OUT, file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
