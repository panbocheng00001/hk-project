# -*- coding: utf-8 -*-
"""slider.py —— 华为 WAF 滑块验证（图形重合类）自动化
=====================================================================
验证码形态（2026-11-09 实测截图）：
    「拖动滑块，让实心图形和空心图形完全重合。」
    深色网格画布 + 若干几何图形（实心/空心轮廓各一对），底部一条滑块。

原理：
    滑块控制其中一个实心图形的水平位移，拖到与同类空心轮廓重合即可。
    所以只需要算出「目标 X - 当前 X」→ 换算成滑块的像素位移 dx。

三层实现，从稳到狠：
    1) locate_shapes(img)      图像分析求 dx（本文件，可离线自测）
    2) read_dom_hint(page)     先看页面有没有把坐标/答案写在 JS 变量里（最省事）
    3) human_drag(page, ...)   人类轨迹拖动（变速 + 微抖 + 末端回拉）

注意：这类验证码会结合**轨迹特征**（速度/加速度/抖动）做二次判定，
      所以光算对 dx 不够，拖动过程必须像人。
"""
from __future__ import annotations

import math
import random
import time

# ---------------------------------------------------------------- 图像分析

try:
    import cv2
    import numpy as np
    HAS_CV = True
except Exception:               # 没装 opencv 时退化为「只靠 DOM 提示」
    HAS_CV = False


def _to_cv(png_bytes):
    if not HAS_CV:
        return None
    if isinstance(png_bytes, str):
        with open(png_bytes, "rb") as fh:
            png_bytes = fh.read()
    arr = np.frombuffer(png_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def locate_shapes(png_bytes, debug_path: str | None = None) -> dict | None:
    """在验证码画布图里找出「实心图形」与「同类空心轮廓」，返回其水平偏移。

    入参可以是 bytes，也可以是文件路径。
    返回 {'dx': 像素差, 'target_x': ..., 'current_x': ..., 'shape': 边数} / None

    判定思路（对齐实测截图）：
      · canvas 是深色网格，图形是亮色/彩色 → 先按亮度+饱和度取前景
      · 实心图形 = 外接框填充率高；空心轮廓 = 填充率低
      · 同一题里实心/空心是**同一种形状**，用外接框尺寸 + 近似多边形边数配对
    """
    img = _to_cv(png_bytes)
    if img is None:
        return None
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]

    # 前景 = 彩色图形 或 很亮的白色图形；深色网格与背景被排除
    mask = (((sat > 60) & (val > 55)) | ((val > 185) & (sat < 70))).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 120 or area > h * w * 0.25:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 12 or bh < 12:
            continue
        # 关键：用「掩码像素密度」区分实心与空心。
        # contourArea 量的是被围住的面积，空心轮廓也会得到 0.7+，分不开；
        # 而实心图形几乎填满框（>0.45），空心只有一圈线（一般 <0.35）。
        roi = mask[y:y + bh, x:x + bw]
        density = cv2.countNonZero(roi) / float(bw * bh)
        approx = cv2.approxPolyDP(c, 0.03 * cv2.arcLength(c, True), True)
        blobs.append({
            "x": x + bw / 2.0, "y": y + bh / 2.0,
            "w": bw, "h": bh, "area": area,
            "fill": area / float(bw * bh),
            "density": density,
            "sides": len(approx),
        })

    if len(blobs) < 2:
        return None

    # 实心 = 密度高；空心轮廓 = 密度低
    hollow = [b for b in blobs if b["density"] < 0.45]
    solid = [b for b in blobs if b["density"] >= 0.45]
    if not hollow:
        hollow = sorted(blobs, key=lambda b: b["density"])[:1]
    if not solid:
        solid = sorted(blobs, key=lambda b: -b["density"])[:1]
    if not hollow or not solid:
        return None

    best = None
    for s in solid:
        for t in hollow:
            dw = abs(s["w"] - t["w"]) / max(1.0, max(s["w"], t["w"]))
            dh = abs(s["h"] - t["h"]) / max(1.0, max(s["h"], t["h"]))
            if dw > 0.40 or dh > 0.40:
                continue
            side_pen = 0 if s["sides"] == t["sides"] else 0.15
            dy = abs(s["y"] - t["y"]) / max(1.0, h)
            score = dw + dh + side_pen + dy * 0.6
            if best is None or score < best[0]:
                best = (score, s, t)

    if best is None:
        return None
    _, s, t = best
    dx = t["x"] - s["x"]

    if debug_path:
        vis = img.copy()
        cv2.rectangle(vis, (int(s["x"] - s["w"] / 2), int(s["y"] - s["h"] / 2)),
                      (int(s["x"] + s["w"] / 2), int(s["y"] + s["h"] / 2)), (0, 255, 0), 2)
        cv2.rectangle(vis, (int(t["x"] - t["w"] / 2), int(t["y"] - t["h"] / 2)),
                      (int(t["x"] + t["w"] / 2), int(t["y"] + t["h"] / 2)), (0, 0, 255), 2)
        try:
            cv2.imwrite(debug_path, vis)
        except Exception:
            pass

    return {"dx": dx, "current_x": s["x"], "target_x": t["x"],
            "shape": f"{s['sides']}-gon", "solid_fill": round(s["fill"], 2),
            "hollow_fill": round(t["fill"], 2), "pairs": len(blobs)}


# ---------------------------------------------------------------- DOM 提示

DOM_HINT_JS = r"""
() => {
  const out = {found: [], canvas: null};
  // 有些实现把答案放在全局变量里，先找一遍（命中就完全不用图像识别）
  const keys = [];
  for (const k of Object.keys(window)) {
    if (/captcha|verify|slider|puzzle|offset|distance|target/i.test(k)) {
      try {
        const v = window[k];
        if (typeof v === 'number' || typeof v === 'string') {
          keys.push({key: k, value: String(v).slice(0, 60)});
        }
      } catch (e) {}
    }
  }
  out.found = keys.slice(0, 20);
  const c = document.querySelector('canvas');
  if (c) out.canvas = {w: c.width, h: c.height, rect: c.getBoundingClientRect().toJSON()};
  const s = document.querySelector('.slider, .slider-btn, [class*=slider], [class*=handle]');
  if (s) out.handle = {cls: s.className, rect: s.getBoundingClientRect().toJSON()};
  return out;
}
"""


def read_dom_hint(page) -> dict:
    """先看页面里有没有现成答案（很多验证码前端会留坐标变量）。"""
    try:
        return page.evaluate(DOM_HINT_JS) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 轨迹拖动

def _track(dx: float, steps: int | None = None) -> list[float]:
    """生成「先快后慢、带微抖、末端小幅回拉」的位移序列（和真人拖动手感一致）。"""
    if steps is None:
        steps = max(18, min(60, int(abs(dx) / 4) + 18))
    seq, moved = [], 0.0
    for i in range(1, steps + 1):
        t = i / steps
        # ease-out：起步快、收尾慢
        e = 1 - (1 - t) ** 2.2
        pos = dx * e
        jitter = random.uniform(-1.2, 1.2) * (1 - t * 0.6)
        step = pos + jitter - moved
        moved = pos
        seq.append(step)
    # 末端过冲 1~3px 再回拉，人拖到目标常这样
    if abs(dx) > 20 and random.random() < 0.7:
        over = random.uniform(1.0, 3.0) * (1 if dx > 0 else -1)
        seq.append(over)
        seq.append(-over)
    return seq


def human_drag(page, handle, dx: float, hold_ms: int = 120) -> bool:
    """按住滑块，按人类轨迹拖到 dx 处再松开。handle 是 Playwright locator。"""
    try:
        box = handle.bounding_box()
        if not box:
            return False
        x0 = box["x"] + box["width"] / 2
        y0 = box["y"] + box["height"] / 2
        page.mouse.move(x0 + random.uniform(-2, 2), y0 + random.uniform(-2, 2))
        time.sleep(random.uniform(0.05, 0.15))
        page.mouse.down()
        time.sleep(random.uniform(0.08, 0.22))     # 按下后短暂停顿
        cur = x0
        for step in _track(dx):
            cur += step
            page.mouse.move(cur, y0 + random.uniform(-0.8, 0.8))
            time.sleep(random.uniform(0.008, 0.030))
        time.sleep(hold_ms / 1000.0)               # 到位后停一下再松手
        page.mouse.up()
        return True
    except Exception:
        return False


def solve(page, canvas_selector: str = "canvas",
          handle_selector: str = ".slider, [class*=slider], [class*=handle]",
          debug_dir: str | None = None) -> dict:
    """一次完整尝试：截图 → 求 dx → 拖动。返回诊断信息。"""
    result = {"ok": False}
    # 1) 先试 DOM 提示
    hint = read_dom_hint(page)
    result["dom_hint"] = hint.get("found")

    # 2) 截图 + 图像分析
    png = b""
    try:
        png = page.locator(canvas_selector).first.screenshot()
    except Exception:
        try:
            png = page.screenshot()
        except Exception:
            pass
    if png and HAS_CV:
        dbg = None
        if debug_dir:
            dbg = f"{debug_dir}/slider_{int(time.time())}.png"
        loc = locate_shapes(png, dbg)
        result["locate"] = loc
        if loc:
            handle = page.locator(handle_selector).first
            result["dragged"] = human_drag(page, handle, loc["dx"])
            result["ok"] = bool(result["dragged"])
    return result
