"""
拼图滑块验证码 —— 缺口定位（多策略融合）

支持三种输入形态：
  1) 只有带缺口的背景图          -> locate(bg)
  2) 背景图 + 滑块小图(png/jpg)   -> locate(bg, piece)
  3) 带缺口背景 + 无缺口完整背景  -> locate(bg, full_bg=full)   (最准，差分法)

输出 Candidate 列表，按置信度排序；工程上取 top1，失败再取 top2/top3 重试。
"""
from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Candidate:
    x: int
    y: int
    w: int
    h: int
    score: float = 0.0
    method: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def imread_any(path: str) -> np.ndarray:
    """兼容中文路径读取。"""
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(path)
    return img


def to_bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _edges(bgr: np.ndarray, lo: int = 70, hi: int = 190) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    e = cv2.Canny(gray, lo, hi)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.morphologyEx(e, cv2.MORPH_CLOSE, k, iterations=2)


def _size_ok(w: int, h: int, W: int, H: int,
             lo_ratio: float = 0.06, hi_ratio: float = 0.55) -> bool:
    side = min(W, H)
    if not (side * lo_ratio <= w <= W * hi_ratio):
        return False
    if not (side * lo_ratio <= h <= H * hi_ratio):
        return False
    ar = w / max(h, 1)
    return 0.45 <= ar <= 2.2


def _edge_density(edges: np.ndarray, rect: Tuple[int, int, int, int]) -> float:
    x, y, w, h = rect
    x, y = max(x, 0), max(y, 0)
    roi = edges[y:y + h, x:x + w]
    if roi.size == 0:
        return 0.0
    return float(roi.mean()) / 255.0


# --------------------------------------------------------------------------- #
# 策略 1：Canny 轮廓（通用，单图可用）
# --------------------------------------------------------------------------- #
def _strategy_canny(bg: np.ndarray, limit: int = 24) -> List[Candidate]:
    H, W = bg.shape[:2]
    edges = _edges(bg)

    # 闭合缺口的四条边：先闭运算，再取外轮廓
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=3)

    out: List[Candidate] = []
    cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts[:limit * 4]:
        x, y, w, h = cv2.boundingRect(c)
        if not _size_ok(w, h, W, H):
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        # 缺口一般是 4~6 边（方块 + 可能凸起）
        quad_bonus = 0.15 if 4 <= len(approx) <= 6 else 0.0
        d = _edge_density(edges, (x, y, w, h))
        out.append(Candidate(x, y, w, h, score=d + quad_bonus, method="canny"))

    # 兜底：边缘图直接做列/行投影找方块
    out.extend(_strategy_projection(edges))
    return out


def _strategy_projection(edges: np.ndarray) -> List[Candidate]:
    """缺口左右两条竖边在列投影上形成双峰，用它反推 x 区间。"""
    H, W = edges.shape[:2]
    col = (edges > 0).sum(axis=0).astype(np.float32)
    if col.max() <= 0:
        return []
    col = col / col.max()

    # 找强峰
    peaks = [i for i in range(1, W - 1) if col[i] > 0.45 and col[i] >= col[i - 1] and col[i] >= col[i + 1]]
    out: List[Candidate] = []
    for i in range(len(peaks) - 1):
        x0, x1 = peaks[i], peaks[i + 1]
        w = x1 - x0
        if not (W * 0.06 <= w <= W * 0.55):
            continue
        # 在该 x 区间内找行投影最强的 band 作为 y/h
        band = edges[:, x0:x1]
        row = (band > 0).sum(axis=1)
        if row.max() <= 0:
            continue
        ys = np.where(row > row.max() * 0.35)[0]
        if len(ys) == 0:
            continue
        y, h = int(ys.min()), int(ys.max() - ys.min())
        if h < H * 0.06:
            continue
        out.append(Candidate(x0, y, w, h, score=0.55, method="projection"))
    return out


# --------------------------------------------------------------------------- #
# 策略 2：模板匹配（给了滑块小图）
# --------------------------------------------------------------------------- #
def _strategy_template(bg: np.ndarray, piece: np.ndarray) -> List[Candidate]:
    H, W = bg.shape[:2]
    tpl = piece
    # 带 alpha 的 png：裁剪到 alpha 非零区域，并用 alpha 作 mask
    mask = None
    if tpl.ndim == 3 and tpl.shape[2] == 4:
        alpha = tpl[:, :, 3]
        ys, xs = np.where(alpha > 10)
        if len(xs) > 0:
            x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
            tpl = tpl[y0:y1, x0:x1]
            mask = tpl[:, :, 3].copy()
            tpl = tpl[:, :, :3]
    tpl = to_bgr(tpl)

    if tpl.shape[0] >= H or tpl.shape[1] >= W:
        s = min(H / (tpl.shape[0] + 1), W / (tpl.shape[1] + 1)) * 0.9
        tpl = cv2.resize(tpl, None, fx=s, fy=s)
        if mask is not None:
            mask = cv2.resize(mask, (tpl.shape[1], tpl.shape[0]))

    out: List[Candidate] = []
    for method, name in ((cv2.TM_CCOEFF_NORMED, "tpl_ccoeff"), (cv2.TM_SQDIFF_NORMED, "tpl_sqdiff")):
        try:
            if mask is not None and method == cv2.TM_SQDIFF_NORMED:
                res = cv2.matchTemplate(bg, tpl, method, mask=mask)
            elif mask is not None:
                res = cv2.matchTemplate(bg, tpl, cv2.TM_CCORR_NORMED, mask=mask)
            else:
                res = cv2.matchTemplate(bg, tpl, method)
        except cv2.error:
            continue
        res = np.nan_to_num(res, nan=0.0)
        flat = res.ravel()
        idx = np.argsort(flat)[::-1][:6] if method == cv2.TM_CCOEFF_NORMED else np.argsort(flat)[:6]
        for k in idx:
            y, x = np.unravel_index(k, res.shape)
            v = float(res[y, x])
            s = v if method == cv2.TM_CCOEFF_NORMED else 1.0 - v
            out.append(Candidate(int(x), int(y), tpl.shape[1], tpl.shape[0],
                                 score=max(0.0, s), method=name))
    return out


# --------------------------------------------------------------------------- #
# 策略 3：差分（同时拿到缺口图与完整图，最准）
# --------------------------------------------------------------------------- #
def _strategy_diff(bg: np.ndarray, full: np.ndarray) -> List[Candidate]:
    full = cv2.resize(full, (bg.shape[1], bg.shape[0]))
    d = cv2.absdiff(to_bgr(bg), to_bgr(full))
    g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)), iterations=2)

    H, W = bg.shape[:2]
    out: List[Candidate] = []
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 0.004 * W * H or not _size_ok(w, h, W, H, 0.05, 0.7):
            continue
        out.append(Candidate(x, y, w, h, score=0.95, method="diff"))
    return out


# --------------------------------------------------------------------------- #
# 策略 4：暗色/亮色遮罩块（缺口常带半透明遮罩）
# --------------------------------------------------------------------------- #
def _strategy_shadow(bg: np.ndarray) -> List[Candidate]:
    H, W = bg.shape[:2]
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2].astype(np.float32)
    # 局部均值差 -> 突出一块均匀的异常区域
    blur = cv2.blur(v, (31, 31))
    diff = np.abs(v - blur)
    m = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, th = cv2.threshold(m, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)), iterations=2)
    out: List[Candidate] = []
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not _size_ok(w, h, W, H, 0.05, 0.5):
            continue
        if w * h < 0.01 * W * H:
            continue
        out.append(Candidate(x, y, w, h, score=0.7, method="shadow"))
    return out


# --------------------------------------------------------------------------- #
# 策略 5：方形边框能量（积分图多尺度滑窗）—— 杂乱背景下的主力
# --------------------------------------------------------------------------- #
# 思想：缺口 = 一个"四条边梯度强、内部相对平坦"的正方形。
#   score(x,y,s) = mean(四边梯度线积分) - lambda * mean(框内梯度)
# 用积分图 + 行/列前缀和把每个位置算成 O(1)，多尺度一次扫完。
def _box_energy_map(grad: np.ndarray, s: int, lam: float = 1.15):
    H, W = grad.shape
    if s + 2 >= min(H, W):
        return None
    hor = np.cumsum(grad, axis=1)          # 行向前缀
    ver = np.cumsum(grad, axis=0)          # 列向前缀
    integ = cv2.integral(grad.astype(np.float64))

    # 纯切片实现（避免花式索引，速度快一个量级）
    top = hor[:H - s, s:W] - hor[:H - s, :W - s]
    bot = hor[s:H, s:W] - hor[s:H, :W - s]
    left = ver[s:H, :W - s] - ver[:H - s, :W - s]
    right = ver[s:H, s:W] - ver[:H - s, s:W]
    border = (top + bot + left + right) / (4.0 * s)

    inner = (integ[s:H, s:W] - integ[:H - s, s:W]
             - integ[s:H, :W - s] + integ[:H - s, :W - s]) / float(s * s)
    if inner.shape != border.shape:                       # 边界对齐保护
        hh = min(inner.shape[0], border.shape[0])
        ww = min(inner.shape[1], border.shape[1])
        border, inner = border[:hh, :ww], inner[:hh, :ww]

    score = border - lam * inner
    # 缺口不会紧贴最左（初始位置被滑块遮挡），给左侧一点惩罚
    xs = np.arange(border.shape[1], dtype=np.float32)[None, :]
    left_pen = np.clip((0.16 * W - xs) / (0.16 * W), 0, 1)
    return score - 0.15 * left_pen * border


def _strategy_box_energy(bg: np.ndarray,
                         sizes: Tuple[int, ...] = (34, 40, 46, 52, 60, 68, 76),
                         keep: int = 6) -> List[Candidate]:
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)
    # 双边滤波：压纹理、留结构，是杂乱背景下的关键一步
    gray = cv2.bilateralFilter(gray, 5, 55, 5)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)
    m = float(grad.max())
    if m <= 0:
        return []
    grad = grad / m

    cands: List[Candidate] = []
    for s in sizes:
        sc = _box_energy_map(grad, s)
        if sc is None:
            continue
        flat = sc.ravel()
        idx = np.argsort(flat)[::-1][:keep]
        for k in idx:
            y, x = np.unravel_index(k, sc.shape)
            v = float(sc[y, x])
            if v <= 0:
                continue
            cands.append(Candidate(int(x), int(y), s, s,
                                   score=1.4 * v, method=f"box{s}"))
    return cands


# --------------------------------------------------------------------------- #
# 融合
# --------------------------------------------------------------------------- #
def _iou(a: Candidate, b: Candidate) -> float:
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix = max(0, min(ax2, bx2) - max(a.x, b.x))
    iy = max(0, min(ay2, by2) - max(a.y, b.y))
    inter = ix * iy
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union else 0.0


def _method_weight(m: str) -> float:
    if m == "diff":
        return 1.00            # 两图差分，几乎无误差
    if m == "tpl_ccoeff":
        return 0.90
    if m == "tpl_sqdiff":
        return 0.85
    if m.startswith("box"):
        return 0.80            # 杂乱背景主力
    if m == "canny":
        return 0.50
    if m == "projection":
        return 0.45
    return 0.35                # shadow


def _borda_fuse(cands: List[Candidate]) -> List[Candidate]:
    """策略内排名 -> 加权投票 -> IoU 聚类累加。避免不同量纲直接混排。"""
    groups: dict[str, List[Candidate]] = {}
    for c in cands:
        groups.setdefault(c.method, []).append(c)

    for m, lst in groups.items():
        lst.sort(key=lambda z: -z.score)
        w = _method_weight(m)
        for r, c in enumerate(lst):
            c.extra["vote"] = w / (1.0 + r)

    clusters: List[List[Candidate]] = []
    for c in sorted(cands, key=lambda z: -z.extra["vote"]):
        for cl in clusters:
            if _iou(c, cl[0]) > 0.45:
                cl.append(c)
                break
        else:
            clusters.append([c])

    fused: List[Candidate] = []
    for cl in clusters:
        total = sum(x.extra["vote"] for x in cl)
        wsum = total or 1.0
        cx = sum(x.cx * x.extra["vote"] for x in cl) / wsum
        cy = float(np.median([x.cy for x in cl]))      # y 用中位数，更抗离群
        rep = max(cl, key=lambda z: z.extra["vote"])
        h = int(np.median([x.h for x in cl]))                 # 高度也用中位数
        w = rep.w
        fused.append(Candidate(int(round(cx - w / 2)), int(round(cy - h / 2)),
                               w, h, score=total,
                               method="+".join(sorted({x.method for x in cl})),
                               extra={"votes": [(x.method, round(x.extra["vote"], 3)) for x in cl]}))
    return fused


def _refine(c: Candidate, gx: np.ndarray, gy: np.ndarray,
            search_ratio: float = 0.30) -> Candidate:
    """
    亚像素精修（只精修 x）：在候选附近找缺口左右两条竖边的最强梯度峰，
    用其中点作为精确 cx。滑块验证码只用水平位移，y 修了没收益反而引入噪声。
    能把 4~8px 的偏差压到 1~2px，是提升"一次过"的关键一步（多数站点容差 5px）。
    """
    H, W = gx.shape
    pad = max(3, int(c.w * search_ratio))
    x0, x1 = max(0, c.x - pad), min(W, c.x + c.w + pad)
    y0, y1 = max(0, c.y), min(H, c.y + c.h)
    if x1 - x0 < 8 or y1 - y0 < 4:
        return c

    # 只统计缺口上下边之间的行，避免背景水平纹理干扰
    col = np.abs(gx[y0:y1, x0:x1]).sum(axis=0)
    mid = max(1, min(col.size - 1, int(c.cx) - x0))
    xl = int(np.argmax(col[:mid + 1])) + x0
    xr = int(np.argmax(col[mid:])) + mid + x0
    new_w = max(8, xr - xl)
    # 边长突变说明峰找错了（抓到背景纹理），保留原框
    if not (0.7 * c.w <= new_w <= 1.45 * c.w):
        return c
    return Candidate(int(round((xl + xr) / 2 - new_w / 2)), c.y, new_w, c.h,
                     score=c.score, method=c.method + "*", extra=c.extra)


def locate(bg: np.ndarray,
           piece: Optional[np.ndarray] = None,
           full_bg: Optional[np.ndarray] = None,
           topk: int = 3) -> List[Candidate]:
    """返回 top-k 候选缺口，按置信度降序。"""
    bg = to_bgr(bg)
    cands: List[Candidate] = []
    cands += _strategy_box_energy(bg)
    cands += _strategy_canny(bg)
    cands += _strategy_shadow(bg)
    if piece is not None:
        cands += _strategy_template(bg, piece)
    if full_bg is not None:
        cands += _strategy_diff(bg, full_bg)

    # 分组归一化 + Borda 加权投票融合
    fused = _borda_fuse(cands)

    edges = _edges(bg)
    H, W = bg.shape[:2]
    for c in fused:
        s = c.score
        s += 0.25 * _edge_density(edges, c.rect)
        ar = c.w / max(c.h, 1)
        s += 0.20 * (1.0 - min(abs(ar - 1.0), 1.35) / 1.35)
        s += 0.12 * (c.cx / W)               # 缺口偏右的先验
        if "diff" in c.method:
            s += 0.60
        c.score = s
    fused.sort(key=lambda z: -z.score)
    top = fused[:topk]

    # 亚像素精修（只对 top-k 做，成本极低）
    gray = cv2.medianBlur(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 3)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return [_refine(c, gx, gy) for c in top]


def distance_to_drag(c: Candidate, display_width: int, orig_width: int,
                     slider_start_x: float = 0.0) -> float:
    """
    CSS 像素下的拖动距离。
      display_width : 页面里背景图元素宽度（clientWidth）
      orig_width    : 背景图真实像素宽度
      slider_start_x: 滑块手柄初始中心 x（相对图片左边缘，通常是 0）
    """
    scale = display_width / float(orig_width)
    return c.cx * scale - slider_start_x


def debug_draw(bg: np.ndarray, cands: List[Candidate], out_path: str) -> None:
    vis = to_bgr(bg).copy()
    colors = [(0, 0, 255), (0, 200, 255), (0, 255, 0), (255, 0, 255)]
    for i, c in enumerate(cands):
        col = colors[i % len(colors)]
        cv2.rectangle(vis, (c.x, c.y), (c.x + c.w, c.y + c.h), col, 2)
        cv2.putText(vis, f"{i}:{c.method} {c.score:.2f}", (c.x, max(12, c.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
        cv2.line(vis, (int(c.cx), 0), (int(c.cx), vis.shape[0]), col, 1)
    cv2.imencode(".png", vis)[1].tofile(out_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python gap.py BG.png [PIECE.png] [FULL_BG.png]")
        raise SystemExit(1)
    bg = imread_any(sys.argv[1])
    piece = imread_any(sys.argv[2]) if len(sys.argv) > 2 else None
    full = imread_any(sys.argv[3]) if len(sys.argv) > 3 else None
    cs = locate(bg, piece, full, topk=5)
    for i, c in enumerate(cs):
        print(f"[{i}] x={c.x} y={c.y} w={c.w} h={c.h} cx={c.cx:.1f} "
              f"score={c.score:.3f} method={c.method}")
    debug_draw(bg, cs, "gap_debug.png")
    print("debug -> gap_debug.png")
