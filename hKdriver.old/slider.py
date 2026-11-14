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

import logging
import math
import os
import random
import time

log = logging.getLogger("hkdriver.slider")

# ---------------------------------------------------------------- 图像分析

try:
    import cv2
    import numpy as np
    HAS_CV = True
except Exception:               # 没装 opencv 时退化为「只靠 DOM 提示」
    HAS_CV = False


# ---------------------------------------------------------------- captcha-lab 桥
# 识别内核：优先走 captcha-lab（与 captcha_mock/analyze.js 同一套判据），
# 不可用时回退本文件的 legacy 实现（locate_shapes + _track）。桥文件本身
# 导入失败（极罕见）也当作不可用，运行时统一回退，绝不静默。
try:
    import captcha_lab_bridge as _bridge
    from captcha_lab_bridge import BridgeUnavailable
    _BRIDGE_IMPORT_ERR: Exception | None = None
except Exception as _e:         # pragma: no cover - 桥文件缺失/损坏
    _bridge = None

    class BridgeUnavailable(RuntimeError):   # type: ignore[no-redef]
        """占位：桥文件本身无法导入时使用。"""

    _BRIDGE_IMPORT_ERR = _e
    log.warning("captcha_lab_bridge 导入失败，将只用 legacy：%s", _e)


def _captcha_lab_enabled() -> bool:
    """CAPTCHA_LAB 开关：默认开（=1）；显式设为 0 时强制走 legacy 实现。"""
    return os.environ.get("CAPTCHA_LAB", "1").strip() != "0"


def _png_width(png_bytes) -> int | None:
    """从 PNG 头部（IHDR）读出位图宽度，用于 legacy 回退时换算 slider_dx。"""
    try:
        if (isinstance(png_bytes, (bytes, bytearray)) and len(png_bytes) >= 24
                and bytes(png_bytes[:8]) == b"\x89PNG\r\n\x1a\n"):
            return int.from_bytes(bytes(png_bytes[16:20]), "big")
    except Exception:
        pass
    return None


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


def _build_drag_track(dx: float, seed: int | None = None
                      ) -> tuple[list[tuple[float, float, float]], str]:
    """构造拖动轨迹的绝对位移点 ``[(x, y, t_ms), ...]`` 与来源标签。

    ``x`` 相对起点（0 → dx），``y`` 为相对抖动，``t`` 为毫秒。
    优先用 captcha-lab 的 ``make_track``（stealth 拟人 + 风控自检）；
    CAPTCHA_LAB=0 或桥不可用时，回退本文件 ``_track`` 的 legacy 增量轨迹，
    并打日志说明回退原因（禁止静默回退）。
    """
    if _captcha_lab_enabled() and _bridge is not None:
        try:
            pts = _bridge.make_track(dx, profile="stealth", seed=seed)
            return pts, "captcha_lab"
        except BridgeUnavailable as e:
            log.warning("make_track 回退 legacy（桥不可用）：%s", e)
        except Exception as e:
            log.warning("make_track 回退 legacy（求解异常）：%s", e)
    # legacy：把 _track 的增量序列累加成绝对点，并补一条时间轴
    seq = _track(dx)
    pts: list[tuple[float, float, float]] = []
    cur = 0.0
    t = 0.0
    for step in seq:
        cur += step
        t += random.uniform(8.0, 30.0)
        pts.append((cur, random.uniform(-0.8, 0.8), t))
    return pts, "legacy"


def human_drag(page, handle, dx: float, hold_ms: int = 120,
               seed: int | None = None) -> bool:
    """按住滑块，按拟人轨迹拖到 dx（滑轨像素）处再松开。handle 是 Playwright locator。

    事件仍走 Playwright 的 ``page.mouse``：在 Chromium 下其底层即
    CDP ``Input.dispatchMouseEvent``（isTrusted=true 的可信通道），
    **不使用合成 PointerEvent 替代**。轨迹来源优先 captcha-lab 的 make_track，
    日志记录来源、点数与耗时。
    """
    try:
        box = handle.bounding_box()
        if not box:
            log.warning("human_drag 取不到手柄 bounding_box")
            return False
        x0 = box["x"] + box["width"] / 2
        y0 = box["y"] + box["height"] / 2

        pts, source = _build_drag_track(dx, seed)
        t_start = time.time()

        page.mouse.move(x0 + random.uniform(-2, 2), y0 + random.uniform(-2, 2))
        time.sleep(random.uniform(0.05, 0.15))
        page.mouse.down()
        time.sleep(random.uniform(0.08, 0.22))     # 按下后短暂停顿

        prev_t = 0.0
        for (px, py, tms) in pts:
            page.mouse.move(x0 + px, y0 + py)
            dt = (tms - prev_t) / 1000.0
            prev_t = tms
            if dt > 0:
                time.sleep(min(dt, 0.08))          # 单步睡眠封顶，避免长轨迹卡住
        time.sleep(hold_ms / 1000.0)               # 到位后停一下再松手
        page.mouse.up()
        elapsed = (time.time() - t_start) * 1000.0
        log.info("human_drag 完成 dx=%.2f 来源=%s 点数=%d 耗时=%.0fms",
                 float(dx), source, len(pts), elapsed)
        return True
    except Exception as e:
        log.warning("human_drag 异常：%s", e)
        return False


def solve(page, canvas_selector: str = "canvas",
          handle_selector: str = ".slider, [class*=slider], [class*=handle]",
          debug_dir: str | None = None,
          track_w: float = 400.0, handle_w: float = 34.0) -> dict:
    """一次完整尝试：截图 → 求 dx/slider_dx → 拖动。返回诊断信息。

    识别优先走 captcha-lab 内核（source=captcha_lab），不可用或失败时回退本文件
    legacy 图像分析（source=legacy）；两条路都会把 slider_dx 换算好再拖动。
    """
    result: dict = {"ok": False, "source": None}
    # 1) 先试 DOM 提示
    hint = read_dom_hint(page)
    result["dom_hint"] = hint.get("found")

    # 2) 截图
    png = b""
    try:
        png = page.locator(canvas_selector).first.screenshot()
    except Exception:
        try:
            png = page.screenshot()
        except Exception:
            pass
    if not png:
        log.warning("solve 没拿到画布截图")
        return result

    dbg = f"{debug_dir}/slider_{int(time.time())}.png" if debug_dir else None
    loc: dict | None = None
    source: str | None = None
    t0 = time.time()

    # 3) 首选 captcha-lab 识别内核
    if _captcha_lab_enabled() and _bridge is not None:
        try:
            res = _bridge.solve_canvas(png, track_w=track_w, handle_w=handle_w)
            if res.get("ok"):
                loc = {"dx": res["dx"], "slider_dx": res["slider_dx"],
                       "current_x": res["current_x"], "target_x": res["target_x"],
                       "pairs": res["pairs"], "level": res["level"]}
                source = "captcha_lab"
            else:
                log.warning("captcha_lab 识别失败，转 legacy：%s", res.get("error"))
        except BridgeUnavailable as e:
            log.warning("captcha_lab 桥不可用，转 legacy：%s", e)
        except Exception as e:
            log.warning("captcha_lab 识别异常，转 legacy：%s", e)

    # 4) 回退：本文件 legacy 图像分析（locate_shapes）
    if loc is None and HAS_CV:
        legacy = locate_shapes(png, dbg)
        if legacy:
            dx = legacy["dx"]
            bmp_w = _png_width(png) or 400.0
            rng = max(10.0, float(track_w) - float(handle_w))
            loc = {"dx": dx, "slider_dx": dx * rng / bmp_w,
                   "current_x": legacy["current_x"], "target_x": legacy["target_x"],
                   "pairs": legacy.get("pairs"), "level": None}
            source = "legacy"

    result["locate"] = loc
    result["source"] = source
    if not loc:
        log.warning("两条路都没算出 dx")
        return result

    elapsed_solve = (time.time() - t0) * 1000.0
    log.info("识别完成 来源=%s dx=%.2f slider_dx=%.2f 耗时=%.0fms",
             source, float(loc["dx"]), float(loc["slider_dx"]), elapsed_solve)
    result["dx"] = loc["dx"]
    result["slider_dx"] = loc["slider_dx"]

    # 5) 拖动（轨迹走 make_track，事件走 page.mouse 可信通道）
    handle = page.locator(handle_selector).first
    result["dragged"] = human_drag(page, handle, loc["slider_dx"])
    result["ok"] = bool(result["dragged"])
    return result
