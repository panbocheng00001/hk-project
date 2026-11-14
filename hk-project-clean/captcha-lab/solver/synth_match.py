"""
拖拽归位类验证码 —— 合成样本生成器（含真值配对）
==================================================

为什么要合成：
  这类验证码没有公开数据集，人工标注 1000 张要两天。合成能把"冷启动"压到半小时，
  而且能**定向造难样本**：同形状不同颜色、旋转 180°、粘连、低对比槽位、描边槽位。
  真实的失败模式你事先想不到，但可以按"物理上会出现的情况"枚举出来。

生成的真实性细节（每一条都对应线上真实形态）
  - 碎片：彩色图形 + 白描边（1~2px）+ 可选 alpha 透明边
  - 槽位：灰色剪影（α 0.25~0.5 混合背景）或纯描边（2px），二者都真实存在
  - 尺度：碎片与槽位尺寸差 ±25%（拖拽时自动缩放，视觉大小不一致）
  - 旋转：碎片 -25°~+25°，槽位 0°~360°（部分站点槽位会转）
  - 干扰：背景纹理、高斯噪声、JPEG 压缩、轻微运动模糊
  - 难样本：两类同形不同色 / 同类旋转 180° / 两槽位外观几乎一样

用法：
    python solver/synth_match.py dataset 400        # 存盘 + 真值 jsonl
    from solver.synth_match import make_board       # 内存里直接用
"""
from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# --------------------------------------------------------------------------- #
# 形状定义：统一在 100×100 画布上画，后续自由缩放
# --------------------------------------------------------------------------- #
SHAPE_NAMES = [
    "circle", "triangle", "square", "diamond", "pentagon",
    "hexagon", "star5", "star6", "cross", "heart",
    "arrow", "trapezoid", "oval", "crescent", "sector", "plus_round",
]


def draw_shape(kind: str, size: int = 100) -> np.ndarray:
    """返回 size×size 的二值 mask（255=形状内）。"""
    m = np.zeros((size, size), np.uint8)
    c = size // 2
    r = int(size * 0.38)
    if kind == "circle":
        cv2.circle(m, (c, c), r, 255, -1)
    elif kind == "oval":
        cv2.ellipse(m, (c, c), (int(r * 1.25), int(r * 0.72)), 0, 0, 360, 255, -1)
    elif kind == "triangle":
        pts = np.array([[c, c - r], [c - int(r * 0.92), c + int(r * 0.75)],
                        [c + int(r * 0.92), c + int(r * 0.75)]])
        cv2.fillPoly(m, [pts], 255)
    elif kind == "square":
        cv2.rectangle(m, (c - r, c - r), (c + r, c + r), 255, -1)
    elif kind == "diamond":
        pts = np.array([[c, c - r], [c + r, c], [c, c + r], [c - r, c]])
        cv2.fillPoly(m, [pts], 255)
    elif kind == "pentagon":
        pts = _regular(c, c, r, 5, -90)
        cv2.fillPoly(m, [pts], 255)
    elif kind == "hexagon":
        pts = _regular(c, c, r, 6, 0)
        cv2.fillPoly(m, [pts], 255)
    elif kind == "star5":
        pts = _star(c, c, r, r * 0.42, 5, -90)
        cv2.fillPoly(m, [pts], 255)
    elif kind == "star6":
        pts = _star(c, c, r, r * 0.52, 6, -90)
        cv2.fillPoly(m, [pts], 255)
    elif kind == "cross":
        a, b = int(r * 0.38), r
        cv2.rectangle(m, (c - a, c - b), (c + a, c + b), 255, -1)
        cv2.rectangle(m, (c - b, c - a), (c + b, c + a), 255, -1)
    elif kind == "plus_round":
        a, b = int(r * 0.42), r
        cv2.rectangle(m, (c - a, c - b), (c + a, c + b), 255, -1)
        cv2.rectangle(m, (c - b, c - a), (c + b, c + a), 255, -1)
        m = cv2.GaussianBlur(m, (9, 9), 0)
        m = np.where(m > 127, 255, 0).astype(np.uint8)
    elif kind == "heart":
        # 两个圆 + 一个三角，经典心形
        rr = int(r * 0.55)
        cv2.circle(m, (c - int(r * 0.45), c - int(r * 0.30)), rr, 255, -1)
        cv2.circle(m, (c + int(r * 0.45), c - int(r * 0.30)), rr, 255, -1)
        pts = np.array([[c - int(r * 0.95), c - int(r * 0.10)],
                        [c + int(r * 0.95), c - int(r * 0.10)],
                        [c, c + r]])
        cv2.fillPoly(m, [pts], 255)
    elif kind == "arrow":
        pts = np.array([[c, c - r], [c + r, c + int(r * 0.15)],
                        [c + int(r * 0.35), c + int(r * 0.15)],
                        [c + int(r * 0.35), c + r],
                        [c - int(r * 0.35), c + r],
                        [c - int(r * 0.35), c + int(r * 0.15)],
                        [c - r, c + int(r * 0.15)]])
        cv2.fillPoly(m, [pts], 255)
    elif kind == "trapezoid":
        pts = np.array([[c - int(r * 0.55), c - r], [c + int(r * 0.55), c - r],
                        [c + r, c + r], [c - r, c + r]])
        cv2.fillPoly(m, [pts], 255)
    elif kind == "crescent":
        cv2.circle(m, (c, c), r, 255, -1)
        m2 = np.zeros_like(m)
        cv2.circle(m2, (c + int(r * 0.45), c - int(r * 0.20)), int(r * 0.85), 255, -1)
        m = cv2.subtract(m, m2)
    elif kind == "sector":
        cv2.ellipse(m, (c, c), (r, r), 0, 200, 340, 255, -1)
        cv2.ellipse(m, (c, c), (r, r), 0, 200, 340, 255, -1)
        cv2.fillPoly(m, [np.array([[c, c],
                                   [c + int(r * math.cos(math.radians(200))),
                                    c + int(r * math.sin(math.radians(200)))],
                                   [c + int(r * math.cos(math.radians(340))),
                                    c + int(r * math.sin(math.radians(340)))]]), ], 255)
    else:
        cv2.circle(m, (c, c), r, 255, -1)
    return m


def _regular(cx: int, cy: int, r: int, n: int, start_deg: float) -> np.ndarray:
    return np.array([[int(cx + r * math.cos(math.radians(start_deg + i * 360 / n))),
                      int(cy + r * math.sin(math.radians(start_deg + i * 360 / n)))]
                     for i in range(n)])


def _star(cx: int, cy: int, r_out: float, r_in: float, n: int, start: float) -> np.ndarray:
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(start + i * 180.0 / n)
        pts.append([int(cx + r * math.cos(a)), int(cy + r * math.sin(a))])
    return np.array(pts)


def _rotate(mask: np.ndarray, deg: float) -> np.ndarray:
    if abs(deg) < 0.5:
        return mask
    h, w = mask.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(mask, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)


def random_background(w: int, h: int, seed: int) -> np.ndarray:
    """照片感背景：低频渐变 + 中频斑块 + 高频噪声。比纯噪声更像真实站点。"""
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 215, (max(2, h // 40), max(2, w // 40), 3)).astype(np.float32)
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    # 斑块
    for _ in range(int(rng.integers(2, 6))):
        x1, y1 = int(rng.integers(0, w)), int(rng.integers(0, h))
        rw, rh = int(rng.integers(w // 6, w // 2)), int(rng.integers(h // 6, h // 2))
        col = rng.integers(30, 230, 3)
        cv2.ellipse(img, (x1, y1), (rw, rh), float(rng.uniform(0, 180)),
                    0, 360, tuple(float(v) for v in col), -1)
    img = cv2.GaussianBlur(img, (0, 0), sigmaX=max(1.5, w / 90.0))
    img = img + rng.normal(0, 7, img.shape).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# 合成样本
# --------------------------------------------------------------------------- #
@dataclass
class SynthBoard:
    image: np.ndarray                          # BGR 整图
    pieces: List[Tuple[int, int, int, int]]    # 碎片框
    slots: List[Tuple[int, int, int, int]]     # 槽位框
    truth: List[int]                           # truth[i] = piece i 对应的 slot 索引
    meta: Dict = field(default_factory=dict)
    piece_masks: List[np.ndarray] = field(default_factory=list)   # 整图尺寸真值 mask
    slot_masks: List[np.ndarray] = field(default_factory=list)

    def iou_vs_truth(self, which: str, idx: int, mask_roi: np.ndarray,
                     origin: Tuple[int, int]) -> float:
        """评估提取质量：extracted(ROI) 与真值 mask(整图) 的 IoU。"""
        pool = self.piece_masks if which == "piece" else self.slot_masks
        if idx >= len(pool):
            return -1.0
        g = pool[idx]
        ox, oy = origin
        sub = g[oy:oy + mask_roi.shape[0], ox:ox + mask_roi.shape[1]]
        if sub.shape != mask_roi.shape:
            return -1.0
        a = mask_roi > 127
        b = sub > 127
        u = np.count_nonzero(a | b)
        return float(np.count_nonzero(a & b) / u) if u else 0.0


def _bbox_of(mask: np.ndarray) -> Tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return (0, 0, 0, 0)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    return x0, y0, x1 - x0, y1 - y0


def _paste(img: np.ndarray, mask: np.ndarray, color: Tuple[int, int, int],
           cx: int, cy: int, outline: bool = True,
           outline_col=(250, 250, 250), outline_px: int = 2,
           alpha: float = 1.0) -> Tuple[int, int, int, int]:
    """
    把 mask 用指定颜色贴到图上（alpha<1 时与背景混合），返回贴上后的真实 bbox。
    返回的 bbox 是"形状自身的外接框"，而不是整块画布 —— 这点很关键，
    否则碎片框会带上大量背景，形状提取会被背景干扰。
    """
    h, w = mask.shape
    x0 = cx - w // 2
    y0 = cy - h // 2
    H, W = img.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return (0, 0, 0, 0)
    cw, ch = dx1 - dx0, dy1 - dy0
    sub = mask[sy0:sy0 + ch, sx0:sx0 + cw]
    sel = sub > 127
    roi = img[dy0:dy1, dx0:dx1]

    if alpha >= 0.999:
        roi[sel] = color
    else:
        blended = (roi.astype(np.float32) * (1 - alpha)
                   + np.array(color, np.float32)[None, None, :] * alpha)
        roi[sel] = np.clip(blended, 0, 255).astype(np.uint8)[sel]

    if outline and outline_px > 0:
        edge = cv2.morphologyEx(sub, cv2.MORPH_GRADIENT,
                                cv2.getStructuringElement(
                                    cv2.MORPH_ELLIPSE, (outline_px * 2 + 1,) * 2))
        roi[edge > 127] = outline_col

    bx, by, bw, bh = _bbox_of(sub)
    return dx0 + bx, dy0 + by, bw, bh


def make_board(seed: int = 0, n: int = 4, W: int = 420, H: int = 300,
               shapes: Optional[Sequence[str]] = None,
               hard: bool = False,
               slot_style: str = "mix") -> SynthBoard:
    """
    生成一题"拖拽归位"：上方 slots 一排，下方 pieces 一排。

    hard=True 会刻意制造难样本：
      - 两个槽位用同形状不同颜色（逼你上外观通道）
      - 一个槽位旋转 180°（逼你上角度惩罚）
      - 槽位用纯描边（逼你做 fill_holes）
    """
    rng = random.Random(seed)
    pool = list(shapes or SHAPE_NAMES)
    kinds = rng.sample(pool, n)
    if hard and n >= 3:
        # 制造"同形不同色"的一对
        kinds[-1] = kinds[0]

    img = random_background(W, H, seed)

    slot_h = H * 0.30
    piece_h = H * 0.26
    slot_y = int(H * 0.26)
    piece_y = int(H * 0.74)

    cols = np.linspace(W / (n + 1), W - W / (n + 1), n)
    slot_boxes, piece_boxes = [], []
    slot_masks: List[np.ndarray] = []
    slot_colors: List[Tuple[int, int, int]] = []
    slot_truth: List[np.ndarray] = []
    piece_truth: List[np.ndarray] = []

    # ---- 槽位 ----
    for i, k in enumerate(kinds):
        s = int(slot_h * rng.uniform(0.80, 1.0))
        m = draw_shape(k, s)
        deg = rng.uniform(0, 360) if (hard and i == n - 1) else rng.uniform(-8, 8)
        m = _rotate(m, deg)
        style = slot_style if slot_style != "mix" else rng.choice(
            ["silhouette", "silhouette", "outline"])
        cx = int(cols[i] + rng.uniform(-6, 6))
        if style == "silhouette":
            # 半透明灰剪影：真实站点最常见（凹陷感）
            shade = rng.randint(85, 135)
            b = _paste(img, m, (shade, shade, shade), cx, slot_y,
                       outline=False, alpha=rng.uniform(0.45, 0.70))
            slot_colors.append((shade, shade, shade))
        else:
            # 纯描边：2~3px 白/浅灰轮廓，内部保持背景
            kk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            ring = cv2.subtract(cv2.dilate(m, kk), cv2.erode(m, kk))
            b = _paste(img, ring, (242, 242, 242), cx, slot_y,
                       outline=False, alpha=0.95)
            slot_colors.append((242, 242, 242))
        slot_boxes.append(b)
        slot_masks.append(m)
        g = np.zeros((H, W), np.uint8)
        x0 = max(0, cx - m.shape[1] // 2)
        y0 = max(0, slot_y - m.shape[0] // 2)
        g[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m[:min(m.shape[0], H - y0),
                                                     :min(m.shape[1], W - x0)]
        slot_truth.append(g)

    # ---- 碎片（顺序打乱）----
    order = list(range(n))
    rng.shuffle(order)
    truth = [0] * n
    for pos, src in enumerate(order):
        k = kinds[src]
        s = int(piece_h * rng.uniform(0.85, 1.05))
        m = draw_shape(k, s)
        m = _rotate(m, rng.uniform(-22, 22))
        col = (rng.randint(40, 240), rng.randint(40, 240), rng.randint(40, 240))
        if hard and pos > 0 and kinds[src] == kinds[0]:
            col = (50, 60, 210)                 # 同形不同色（难样本）
        cx = int(cols[pos] + rng.uniform(-6, 6))
        b = _paste(img, m, col, cx, piece_y, outline=True, outline_px=2)
        piece_boxes.append(b)
        truth[pos] = src
        g = np.zeros((H, W), np.uint8)
        x0 = max(0, cx - m.shape[1] // 2)
        y0 = max(0, piece_y - m.shape[0] // 2)
        g[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m[:min(m.shape[0], H - y0),
                                                     :min(m.shape[1], W - x0)]
        piece_truth.append(g)

    # ---- 后处理：噪声 + JPEG + 轻微模糊 ----
    img = img + np.random.default_rng(seed + 7).normal(0, 4, img.shape).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if ok:
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    img = cv2.GaussianBlur(img, (3, 3), 0)

    return SynthBoard(img, piece_boxes, slot_boxes, truth,
                      {"kinds": kinds, "order": order, "seed": seed, "hard": hard,
                       "slot_style": slot_style},
                      piece_truth, slot_truth)


# --------------------------------------------------------------------------- #
# 存盘（给 YOLO / 人工标注复用）
# --------------------------------------------------------------------------- #
def build_dataset(out_dir: str, n: int = 400, hard_ratio: float = 0.4) -> None:
    os.makedirs(out_dir, exist_ok=True)
    meta_path = os.path.join(out_dir, "boards.jsonl")
    with open(meta_path, "w", encoding="utf-8") as f:
        for i in range(n):
            b = make_board(seed=i, n=int(4 + i % 3), hard=(i % 100) < hard_ratio * 100)
            p = os.path.join(out_dir, f"board_{i:05d}.png")
            cv2.imencode(".png", b.image)[1].tofile(p)
            f.write(json.dumps({
                "file": os.path.basename(p),
                "pieces": b.pieces, "slots": b.slots,
                "truth": b.truth, **b.meta,
            }, ensure_ascii=False) + "\n")
    print(f"dataset -> {out_dir} ({n} boards, {meta_path})")


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "dataset_match"
    nn = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    build_dataset(d, nn)
