"""
几何重合滑块（华为 WAF 形态）求解器
===================================

题型：深色网格画布上散落若干几何图形，其中一对是「同形同色的实心 + 空心轮廓」。
拖动滑块水平移动实心图形，使其与空心轮廓完全重合。

与拼图滑块的差别：
  · 没有「缺口」，要判断的是**两个独立图形的水平中心差**；
  · 判据不是边缘能量，而是「实心/空心的填充密度 + 8×8 占用剖面 + 色相」；
  · 前端提交的是 `slider_dx = dx * (trackW - handleW) / bmpW`，
    即画布像素差要换算成滑轨像素。

本实现与 `hKdriver/captcha_mock/analyze.js` 的判据逐条对齐（三档阈值、4 邻域连通域、
密度分类、空心填充、8×8 剖面距离、色相精修），所以两边的 dx 可以直接对比。

```python
from solver.overlap import analyze, to_slider_dx

res = analyze("canvas.png")
slider_dx = to_slider_dx(res.dx, bmp_w=400, track_w=400, handle_w=34)
```
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

__all__ = [
    "Blob", "OverlapResult", "analyze", "analyze_payload",
    "to_slider_dx", "solve_canvas", "LEVELS",
]

ImageLike = Union[np.ndarray, bytes, bytearray, str]


@dataclass
class Blob:
    cx: float
    cy: float
    w: int
    h: int
    count: int
    density: float
    rgb: Tuple[float, float, float]
    hue: float                     # -1 表示接近灰色
    prof: np.ndarray               # 8x8 占用剖面
    prof_filled: Optional[np.ndarray] = None
    x0: int = 0
    y0: int = 0

    @property
    def solid(self) -> bool:
        return self.density >= 0.45


@dataclass
class OverlapResult:
    ok: bool
    dx: float = 0.0                # 目标中心 - 当前中心（画布位图像素）
    current_x: float = 0.0
    target_x: float = 0.0
    score: float = 0.0
    pairs: int = 0
    level: int = -1
    refined_hue: Optional[int] = None
    solid_fill: float = 0.0
    hollow_fill: float = 0.0
    image_size: Tuple[int, int] = (0, 0)
    blobs: List[Blob] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "dx": round(self.dx, 2),
            "current_x": round(self.current_x, 2), "target_x": round(self.target_x, 2),
            "score": round(self.score, 4), "pairs": self.pairs, "level": self.level,
            "refined_hue": self.refined_hue,
            "solid_fill": round(self.solid_fill, 3),
            "hollow_fill": round(self.hollow_fill, 3),
            "image_size": list(self.image_size),
            "error": self.error,
        }


# 三档前景阈值：(sat_hi, mx_hi, mx_lo, sat_lo)，判定式为
#   (sat > sat_hi and mx > mx_hi) or (mx > mx_lo and sat < sat_lo)
# 从严到松逐档回退，防止深色网格把图形连成一片（网格亮度低，第一档就能排掉）。
LEVELS: Tuple[Tuple[float, float, float, float], ...] = (
    (90.0, 90.0, 190.0, 80.0),
    (70.0, 70.0, 175.0, 85.0),
    (50.0, 55.0, 160.0, 95.0),
)


def _level_mask(mx: np.ndarray, sat: np.ndarray, level: int) -> np.ndarray:
    s_hi, m_hi, m_lo, s_lo = LEVELS[max(0, min(level, len(LEVELS) - 1))]
    return ((sat > s_hi) & (mx > m_hi)) | ((mx > m_lo) & (sat < s_lo))


# --------------------------------------------------------------------------- #
# 图像工具
# --------------------------------------------------------------------------- #
def _load_rgb(src: ImageLike) -> np.ndarray:
    """统一成 RGB uint8，顺序与 analyze.js 的 RGBA 前三个通道一致。"""
    if isinstance(src, np.ndarray):
        img = src
    elif isinstance(src, (bytes, bytearray)):
        img = cv2.imdecode(np.frombuffer(bytes(src), np.uint8), cv2.IMREAD_UNCHANGED)
    elif isinstance(src, str):
        s = src.strip()
        if s.startswith("data:") and "," in s:
            import base64
            img = cv2.imdecode(np.frombuffer(base64.b64decode(s.split(",", 1)[1]),
                                             np.uint8), cv2.IMREAD_UNCHANGED)
        elif _looks_like_path(s):
            from .gap import imread_any
            img = imread_any(s)
        else:
            import base64
            img = cv2.imdecode(np.frombuffer(base64.b64decode(s, validate=False),
                                             np.uint8), cv2.IMREAD_UNCHANGED)
    else:
        raise TypeError(f"不支持的图片类型 {type(src)!r}")

    if img is None:
        raise ValueError("图像解码失败")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)[:, :, :3]
    else:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img


def _looks_like_path(s: str) -> bool:
    no_sep = ("/" not in s) and ("\\" not in s)
    return (not no_sep) or len(s) < 8 or (":" in s[:3])


def _hue(r: float, g: float, b: float) -> float:
    R, G, B = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(R, G, B), min(R, G, B)
    d = mx - mn
    if d < 0.02:
        return -1.0
    if mx == R:
        h = ((G - B) / d) % 6
    elif mx == G:
        h = (B - R) / d + 2
    else:
        h = (R - G) / d + 4
    return (h * 60.0 + 360.0) % 360.0


def _hue_dist(a: float, b: float) -> float:
    if a < 0 or b < 0:
        return 0.15
    d = abs(a - b)
    if d > 180:
        d = 360 - d
    return d / 180.0


# --------------------------------------------------------------------------- #
# 掩膜与连通域
# --------------------------------------------------------------------------- #
def _build_mask(rgb: np.ndarray, level: int, hue_center: Optional[float] = None) -> np.ndarray:
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6) * 255.0, 0.0)

    mask = _level_mask(mx, sat, level)

    if hue_center is not None and hue_center >= 0:
        # 与 JS 一致：色相差 > 16° 的像素直接排除
        R, G, B = r / 255.0, g / 255.0, b / 255.0
        d = mx / 255.0 - mn / 255.0
        hue = np.full(mx.shape, -1.0, np.float32)
        nz = d > 0.02
        rr, gg, bb = R[nz], G[nz], B[nz]
        dd = d[nz]
        h = np.where(rr == mx[nz] / 255.0,
                     (((gg - bb) / dd) % 6),
                     np.where(gg == mx[nz] / 255.0,
                              (bb - rr) / dd + 2,
                              (rr - gg) / dd + 4))
        hue[nz] = ((h * 60.0) + 360.0) % 360.0
        delta = np.abs(hue - hue_center)
        delta = np.where(delta > 180, 360 - delta, delta)
        mask &= (hue >= 0) & (delta <= 16)
    return mask


def _profile(mask: np.ndarray, x0: int, y0: int, bw: int, bh: int,
             extra: Optional[np.ndarray] = None) -> np.ndarray:
    prof = np.zeros(64, np.float32)
    h, w = mask.shape[:2]
    for gy in range(8):
        ya = y0 + (bh * gy) // 8
        yb = y0 + max(1, (bh * (gy + 1)) // 8)
        for gx in range(8):
            xa = x0 + (bw * gx) // 8
            xb = x0 + max(1, (bw * (gx + 1)) // 8)
            ya2, yb2 = min(ya, h), min(yb, h)
            xa2, xb2 = min(xa, w), min(xb, w)
            if ya2 >= yb2 or xa2 >= xb2:
                continue
            cell = mask[ya2:yb2, xa2:xb2]
            tot = cell.size
            on = int(cell.sum())
            if extra is not None:
                on += int(extra[ya2:yb2, xa2:xb2].sum())
            prof[gy * 8 + gx] = on / tot if tot else 0.0
    return prof


def _fill_hollow(mask: np.ndarray, x0: int, y0: int, bw: int, bh: int) -> np.ndarray:
    """把空心轮廓「填满」：从包围盒边界出发，把与非掩膜连通的区域标为外部。"""
    h, w = mask.shape[:2]
    crop = mask[y0:y0 + bh, x0:x0 + bw]
    inv = (~crop).astype(np.uint8)
    if inv.size == 0:
        return np.zeros_like(mask)
    # 补一圈 1，让边界外的连通区域从角点一次填完（与 JS 从边界种子 BFS 等价）
    padded = np.pad(inv, 1, constant_values=1)
    ff = padded.copy()
    m = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, m, (0, 0), 2)
    outside = (ff[1:-1, 1:-1] == 2)
    inside = (~crop) & (~outside)
    filled = np.zeros_like(mask)
    filled[y0:y0 + bh, x0:x0 + bw] = (crop | inside).astype(np.uint8)
    return filled


def _components(mask: np.ndarray, rgb: np.ndarray, level: int) -> List[Blob]:
    h, w = mask.shape
    m8 = mask.astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m8, connectivity=4)
    blobs: List[Blob] = []
    total = float(w * h)
    for i in range(1, n):
        x0, y0, bw, bh, count = (int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                                  int(stats[i, 3]), int(stats[i, 4]))
        if count < 120 or bw < 12 or bh < 12 or count > total * 0.25:
            continue
        comp = labels[y0:y0 + bh, x0:x0 + bw] == i
        pix = rgb[y0:y0 + bh, x0:x0 + bw][comp]
        rgb_mean = tuple(float(v) for v in pix.mean(axis=0)) if pix.size else (0.0, 0.0, 0.0)
        dens = count / float(bw * bh)
        blob = Blob(
            cx=x0 + bw / 2.0, cy=y0 + bh / 2.0, w=bw, h=bh, count=count,
            density=dens, rgb=rgb_mean, hue=_hue(*rgb_mean),
            prof=_profile(m8, x0, y0, bw, bh), x0=x0, y0=y0,
        )
        if dens < 0.45:
            filled = _fill_hollow(m8, x0, y0, bw, bh)
            blob.prof_filled = _profile(m8, x0, y0, bw, bh, filled)
        blobs.append(blob)
    return blobs


# --------------------------------------------------------------------------- #
# 配对与主流程
# --------------------------------------------------------------------------- #
def _pair_best(blobs: Sequence[Blob], img_h: int) -> Optional[Tuple[float, Blob, Blob]]:
    solid = [b for b in blobs if b.solid]
    hollow = [b for b in blobs if not b.solid]
    if not solid or not hollow:
        by_den = sorted(blobs, key=lambda b: b.density, reverse=True)
        if not solid and by_den:
            solid = [by_den[0]]
        if not hollow and by_den:
            hollow = [by_den[-1]]

    best: Optional[Tuple[float, Blob, Blob]] = None
    for s in solid:
        for t in hollow:
            if s is t:
                continue
            dw = abs(s.w - t.w) / max(1, max(s.w, t.w))
            dh = abs(s.h - t.h) / max(1, max(s.h, t.h))
            if dw > 0.4 or dh > 0.4:
                continue
            dy = abs(s.cy - t.cy) / max(1, img_h)
            shape = float(np.abs(s.prof - t.prof_filled).mean()) if t.prof_filled is not None else 0.35
            score = dw + dh + dy * 0.6 + _hue_dist(s.hue, t.hue) * 0.6 + shape * 1.8
            if best is None or score < best[0]:
                best = (score, s, t)
    return best


def analyze(image: ImageLike, hue_hint: Optional[float] = None) -> OverlapResult:
    """给画布图，返回实心/空心配对与水平中心差 dx（位图像素）。"""
    rgb = _load_rgb(image)
    h, w = rgb.shape[:2]
    if hue_hint is not None:
        blobs = _components(_build_mask(rgb, 0, hue_hint), rgb, 0)
        best = _pair_best(blobs, h)
        if best:
            score, s, t = best
            return OverlapResult(True, t.cx - s.cx, s.cx, t.cx, score, len(blobs), 0,
                                 int(hue_hint), s.density, t.density, (w, h), blobs)

    blobs: List[Blob] = []
    used_level = -1
    for li in range(len(LEVELS)):
        found = _components(_build_mask(rgb, li), rgb, li)
        if len(found) >= 2:
            blobs, used_level = found, li
            break
        blobs = found

    if len(blobs) < 2:
        return OverlapResult(False, image_size=(w, h), pairs=len(blobs),
                             error="前景块不足（阈值过严或画布为空）")

    best = _pair_best(blobs, h)
    if best is None:
        return OverlapResult(False, image_size=(w, h), pairs=len(blobs),
                             error="没有可配对的实心/空心")

    score, s, t = best
    refined_hue: Optional[int] = None
    hues = sorted({int(round(b.hue / 12.0)) * 12
                   for b in blobs if b.solid and b.hue >= 0})[:4]
    for hc in hues:
        sub = _components(_build_mask(rgb, max(0, used_level), hc), rgb, max(0, used_level))
        if len(sub) < 2:
            continue
        cand = _pair_best(sub, h)
        if cand and cand[0] < score - 0.02:
            score, s, t = cand
            refined_hue = hc

    return OverlapResult(
        ok=True, dx=t.cx - s.cx, current_x=s.cx, target_x=t.cx, score=score,
        pairs=len(blobs), level=used_level, refined_hue=refined_hue,
        solid_fill=s.density, hollow_fill=t.density, image_size=(w, h), blobs=blobs,
    )


def analyze_payload(payload: Dict[str, Any]) -> OverlapResult:
    """吃与站点一致的 JSON：{dataUrl, bmpW, bmpH, ...}。"""
    data = payload.get("dataUrl") or payload.get("bg") or payload.get("image")
    return analyze(data)


def to_slider_dx(dx: float, bmp_w: float, track_w: float, handle_w: float,
                 min_range: float = 10.0) -> float:
    """画布像素差 → 滑轨像素位移。与前端 `range = trackW - handleW` 完全一致。"""
    rng = max(min_range, float(track_w) - float(handle_w))
    return float(dx) * rng / float(bmp_w)


def solve_canvas(image: ImageLike,
                 bmp_w: Optional[float] = None,
                 track_w: float = 400.0,
                 handle_w: float = 34.0,
                 min_range: float = 10.0) -> Dict[str, Any]:
    """一步到位：画布图 → {dx, slider_dx, ...}。"""
    res = analyze(image)
    out = res.to_dict()
    if bmp_w is None:
        bmp_w = res.image_size[0] or 1
    out["bmp_w"] = bmp_w
    out["track_w"] = track_w
    out["handle_w"] = handle_w
    out["slider_dx"] = (round(to_slider_dx(res.dx, bmp_w, track_w, handle_w, min_range), 2)
                        if res.ok else 0.0)
    return out


def draw_debug(image: ImageLike, result: OverlapResult, out_path: Optional[str] = None) -> np.ndarray:
    """把配对结果画出来：红=实心，绿=目标空心。"""
    rgb = _load_rgb(image)
    vis = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if result.ok:
        s = min(result.blobs, key=lambda b: abs(b.cx - result.current_x))
        t = min(result.blobs, key=lambda b: abs(b.cx - result.target_x))
        for blob, color, tag in ((s, (0, 0, 255), "solid"), (t, (0, 200, 0), "hollow")):
            cv2.rectangle(vis, (blob.x0, blob.y0),
                          (blob.x0 + blob.w, blob.y0 + blob.h), color, 2)
            cv2.putText(vis, tag, (blob.x0, max(12, blob.y0 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        cv2.arrowedLine(vis, (int(result.current_x), vis.shape[0] - 12),
                        (int(result.target_x), vis.shape[0] - 12), (0, 255, 255), 2,
                        tipLength=0.04)
        cv2.putText(vis, f"dx={result.dx:.1f}", (8, vis.shape[0] - 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    if out_path:
        ok, buf = cv2.imencode(".png", vis)
        if ok:
            buf.tofile(out_path)
    return vis


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("usage: python -m solver.overlap IMAGE [TRACK_W] [HANDLE_W] [DEBUG.png]")
        raise SystemExit(1)
    tw = float(sys.argv[2]) if len(sys.argv) > 2 else 400.0
    hw = float(sys.argv[3]) if len(sys.argv) > 3 else 34.0
    out = solve_canvas(sys.argv[1], track_w=tw, handle_w=hw)
    print(out)
    dbg = sys.argv[4] if len(sys.argv) > 4 else None
    if dbg:
        draw_debug(sys.argv[1], analyze(sys.argv[1]), dbg)
        print("debug ->", dbg)
