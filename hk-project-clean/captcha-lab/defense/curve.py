"""
曲线滑块 · 图形重合验证码生成器（防守方）
==========================================

题型：拖动滑块，实心图形沿一条曲线移动（可选同时旋转），与匹配的空心图形完全重合即通过。

和直线拼图最大的不同：**前端必须知道曲线**才能实时渲染实心图形的位置，所以路径本身
不可能对客户端保密。真正要留在服务端的是"路径上哪个位置是答案"。因此这类题的
抗识别能力只能来自：

  num_path_decoys   路径上的假目标 —— 攻击者沿路径扫描时会遇到多个"看起来能重合"的位置
  same_kind_decoys  假目标与实心图形同形状、但角度错开 —— 只看形状轮廓分不出，必须比对角度
  rotate_deg        实心图形沿路径同时旋转 —— 位置和角度要同时对上
  contrast/clutter  弱化空心描边、加干扰笔画 —— 压低轮廓对齐的信噪比

路径外的空心图形（num_free_decoys）对"知道路径"的攻击者无效，只用来干扰人眼粗看，
保留它是为了在评测里把这一点量化出来。show_path 只影响用户体验，不影响安全性。

坐标约定：背景图像素坐标，左上角为原点；角度单位为度，顺时针为正（与 CSS rotate 一致）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from solver.synth_match import draw_shape

PATH_POINTS = 201                  # 滑块 0~1 离散成的路径采样点数，前后端共用同一份
CURVE_SHAPES = ("circle", "triangle", "square", "diamond", "pentagon",
                "hexagon", "star5", "heart", "arrow", "trapezoid")
# 只有 360° 旋转对称的形状：同形状假目标必须从这里选，否则转一个对称角度就和真目标一样了
ASYMMETRIC_SHAPES = ("heart", "arrow", "trapezoid")
PATH_KINDS = ("bezier", "sine")
OUTLINE_PALETTE = ((214, 120, 150), (230, 160, 90), (120, 110, 220),
                   (90, 170, 230), (150, 200, 120), (200, 120, 220))   # BGR


@dataclass
class CurveConfig:
    width: int = 400
    height: int = 300
    shape_size: int = 36             # 图形外接直径（像素）
    path_kind: str = "bezier"
    num_path_decoys: int = 2
    num_free_decoys: int = 3
    same_kind_decoys: bool = False
    decoy_angle: Tuple[float, float] = (70.0, 180.0)   # 同形假目标与正确角度的偏差范围（度）
    rotate_deg: float = 0.0          # 滑块从 0 拖到 1，实心图形累计旋转角度
    show_path: bool = True
    num_clutter: int = 2             # 干扰笔画数
    outline_px: int = 2
    contrast: float = 1.0            # 空心描边相对背景的对比度，0~1
    noise_sigma: float = 4.0
    tolerance_px: float = 8.0

    def validate(self) -> None:
        if self.path_kind not in PATH_KINDS:
            raise ValueError(f"path_kind must be one of {PATH_KINDS}")
        if not (0.1 <= self.contrast <= 1.0):
            raise ValueError("contrast must be in [0.1, 1]")
        if not (16 <= self.shape_size <= min(self.width, self.height) * 0.25):
            raise ValueError("shape_size out of sane range for the canvas")
        if self.num_path_decoys < 0 or self.num_free_decoys < 0:
            raise ValueError("decoy counts must be >= 0")
        lo, hi = self.decoy_angle
        if not (10.0 <= lo <= hi <= 180.0):
            raise ValueError("decoy_angle must satisfy 10 <= lo <= hi <= 180")


@dataclass
class CurveChallenge:
    bg: np.ndarray                   # (H, W, 3) BGR，含所有空心图形
    piece: np.ndarray                # (S, S, 4) BGRA，未旋转的实心图形
    path: np.ndarray                 # (PATH_POINTS, 2) float，实心图形中心轨迹
    target_idx: int
    angle0: float
    kind: str
    config: CurveConfig
    path_decoy_idx: List[int] = field(default_factory=list)
    free_decoys: List[Tuple[float, float]] = field(default_factory=list)

    @staticmethod
    def index_of(s: float) -> int:
        return int(round(min(1.0, max(0.0, float(s))) * (PATH_POINTS - 1)))

    @property
    def target_s(self) -> float:
        return self.target_idx / (PATH_POINTS - 1)

    def pose(self, s: float) -> Tuple[float, float, float]:
        """滑块值 s 对应的实心图形中心与角度。"""
        x, y = self.path[self.index_of(s)]
        return float(x), float(y), self.angle0 + self.config.rotate_deg * float(s)

    def is_correct(self, s: float) -> bool:
        x, y, _ = self.pose(s)
        tx, ty = self.path[self.target_idx]
        return math.hypot(x - tx, y - ty) <= self.config.tolerance_px


# --------------------------------------------------------------------------- #
# 几何
# --------------------------------------------------------------------------- #
def _resample(pts: np.ndarray, n: int) -> np.ndarray:
    """按弧长均匀重采样：滑块匀速拖动时，图形沿曲线也匀速移动。"""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    u = np.linspace(0, cum[-1], n)
    return np.stack([np.interp(u, cum, pts[:, 0]), np.interp(u, cum, pts[:, 1])], 1)


def _make_path(cfg: CurveConfig, rng: np.random.Generator, margin: float) -> np.ndarray:
    w, h, m = cfg.width, cfg.height, margin
    u = np.linspace(0, 1, 2000)[:, None]
    if cfg.path_kind == "bezier":
        p0 = np.array([m, rng.uniform(m, h - m)])
        p1 = np.array([rng.uniform(0.2, 0.5) * w, rng.uniform(m, h - m)])
        p2 = np.array([rng.uniform(0.5, 0.8) * w, rng.uniform(m, h - m)])
        p3 = np.array([w - m, rng.uniform(m, h - m)])
        pts = ((1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1
               + 3 * (1 - u) * u ** 2 * p2 + u ** 3 * p3)
    else:
        amp = rng.uniform(0.15, 0.3) * h
        freq, phase = rng.uniform(0.6, 1.4), rng.uniform(0, 2 * math.pi)
        x = m + u[:, 0] * (w - 2 * m)
        y = h / 2 + amp * np.sin(2 * math.pi * freq * u[:, 0] + phase)
        pts = np.stack([x, y], 1)
    pts[:, 1] = np.clip(pts[:, 1], m, h - m)
    return _resample(pts, PATH_POINTS)


def _shape_mask(kind: str, size: int, deg_cw: float = 0.0) -> np.ndarray:
    """size×size 的 0/255 mask，图形外接直径约等于 size，按顺时针 deg_cw 旋转。"""
    canvas = int(math.ceil(size / 0.76)) | 1
    m = draw_shape(kind, canvas)
    if abs(deg_cw) > 0.01:
        M = cv2.getRotationMatrix2D((canvas / 2, canvas / 2), -deg_cw, 1.0)
        m = cv2.warpAffine(m, M, (canvas, canvas), flags=cv2.INTER_LINEAR)
        m = np.where(m > 127, 255, 0).astype(np.uint8)
    return m


def _stamp_outline(img: np.ndarray, kind: str, cx: float, cy: float, deg: float,
                   cfg: CurveConfig, color: Tuple[int, int, int]) -> None:
    """在 (cx, cy) 画一个空心图形描边，颜色按 contrast 向局部背景色收敛。原地修改。"""
    mask = _shape_mask(kind, cfg.shape_size, deg)
    s = mask.shape[0]
    x0, y0 = int(round(cx - s / 2)), int(round(cy - s / 2))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    local = img[max(0, y0):y0 + s, max(0, x0):x0 + s].reshape(-1, 3).mean(0)
    col = tuple(int(b + cfg.contrast * (c - b)) for c, b in zip(color, local))
    cv2.drawContours(img, [c + (x0, y0) for c in cnts], -1, col,
                     cfg.outline_px, lineType=cv2.LINE_AA)


def _background(cfg: CurveConfig, rng: np.random.Generator) -> np.ndarray:
    """深色网格底，和常见的图形重合类验证码风格一致。"""
    w, h = cfg.width, cfg.height
    base = np.array([42, 26, 20], np.float32) + rng.integers(-6, 7, 3)
    img = np.empty((h, w, 3), np.float32)
    img[:] = base
    step = int(rng.integers(28, 40))
    line = tuple(float(v) for v in base + 14)
    for x in range(int(rng.integers(0, step)), w, step):
        cv2.line(img, (x, 0), (x, h), line, 1)
    for y in range(int(rng.integers(0, step)), h, step):
        cv2.line(img, (0, y), (w, y), line, 1)
    return img.astype(np.uint8)


def _clutter(img: np.ndarray, cfg: CurveConfig, rng: np.random.Generator) -> None:
    """干扰笔画：随机粗曲线 + 小实心块，制造与描边相似的边缘。"""
    w, h = cfg.width, cfg.height
    for _ in range(cfg.num_clutter):
        pts = np.stack([rng.uniform(0, w, 4), rng.uniform(0, h, 4)], 1)
        u = np.linspace(0, 1, 60)[:, None]
        p0, p1, p2, p3 = pts
        curve = ((1 - u) ** 3 * p0 + 3 * (1 - u) ** 2 * u * p1
                 + 3 * (1 - u) * u ** 2 * p2 + u ** 3 * p3).astype(np.int32)
        col = tuple(int(v) for v in rng.integers(50, 110, 3))
        cv2.polylines(img, [curve], False, col, int(rng.integers(2, 6)), cv2.LINE_AA)
    for _ in range(max(0, cfg.num_clutter // 2)):
        s = int(rng.integers(14, 26))
        x, y = int(rng.integers(0, w - s)), int(rng.integers(0, h - s))
        col = tuple(int(v) for v in rng.integers(60, 120, 3))
        cv2.rectangle(img, (x, y), (x + s, y + s), col, -1)


def _place_on_path(path: np.ndarray, k: int, min_gap: float, start_gap: float,
                   rng: np.random.Generator) -> List[int]:
    """在路径上挑 k 个两两相距 ≥ min_gap（欧氏距离）、且离起点 ≥ start_gap 的采样点。"""
    cand = [i for i in range(len(path))
            if np.hypot(*(path[i] - path[0])) >= start_gap and i <= len(path) - 3]
    for _ in range(50):
        picked: List[int] = []
        for i in rng.permutation(cand):
            if all(np.hypot(*(path[i] - path[j])) >= min_gap for j in picked):
                picked.append(int(i))
                if len(picked) == k:
                    return sorted(picked)
    return sorted(picked)


def _piece_image(kind: str, size: int, rng: np.random.Generator) -> np.ndarray:
    mask = _shape_mask(kind, size)
    s = mask.shape[0]
    fill = tuple(int(v) for v in rng.integers(60, 200, 3))
    rim = tuple(int(v) for v in rng.integers(150, 250, 3))
    piece = np.zeros((s, s, 4), np.uint8)
    piece[mask > 0, :3] = fill
    piece[:, :, 3] = mask
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(piece, cnts, -1, (*rim, 255), 2, lineType=cv2.LINE_AA)
    return piece


# --------------------------------------------------------------------------- #
# 出题
# --------------------------------------------------------------------------- #
def generate(cfg: Optional[CurveConfig] = None,
             seed: Optional[int] = None) -> CurveChallenge:
    cfg = cfg or CurveConfig()
    cfg.validate()
    rng = np.random.default_rng(seed)
    S = cfg.shape_size
    margin = S / 0.76 / 2 + 4

    path = _make_path(cfg, rng, margin)
    slots = _place_on_path(path, 1 + cfg.num_path_decoys, min_gap=1.5 * S,
                           start_gap=1.6 * S, rng=rng)
    target_idx = slots.pop(int(rng.integers(0, len(slots))))

    angle0 = float(rng.uniform(0, 360)) if cfg.rotate_deg else 0.0
    kinds = ASYMMETRIC_SHAPES if cfg.same_kind_decoys else CURVE_SHAPES
    kind = str(rng.choice(kinds))
    others = [k for k in CURVE_SHAPES if k != kind]

    def angle_at(i: int) -> float:
        return angle0 + cfg.rotate_deg * i / (PATH_POINTS - 1)

    bg = _background(cfg, rng)
    _clutter(bg, cfg, rng)
    if cfg.show_path:
        cv2.polylines(bg, [path.astype(np.int32)], False, (70, 52, 46), 2, cv2.LINE_AA)

    free: List[Tuple[float, float]] = []
    for _ in range(400):
        if len(free) >= cfg.num_free_decoys:
            break
        p = rng.uniform([margin, margin], [cfg.width - margin, cfg.height - margin])
        if np.min(np.hypot(*(path - p).T)) < 1.1 * S:
            continue
        if any(math.hypot(p[0] - q[0], p[1] - q[1]) < 1.4 * S for q in free):
            continue
        free.append((float(p[0]), float(p[1])))
        _stamp_outline(bg, str(rng.choice(CURVE_SHAPES)), p[0], p[1],
                       float(rng.uniform(0, 360)), cfg,
                       OUTLINE_PALETTE[int(rng.integers(len(OUTLINE_PALETTE)))])

    # 真假目标用同一个调色板随机取色：颜色不能成为区分真假的线索
    for i in slots:
        if cfg.same_kind_decoys:
            off = float(rng.uniform(*cfg.decoy_angle)) * (1 if rng.random() < 0.5 else -1)
            dk, deg = kind, angle_at(i) + off
        else:
            dk, deg = str(rng.choice(others)), float(rng.uniform(0, 360))
        _stamp_outline(bg, dk, *path[i], deg, cfg,
                       OUTLINE_PALETTE[int(rng.integers(len(OUTLINE_PALETTE)))])
    _stamp_outline(bg, kind, *path[target_idx], angle_at(target_idx), cfg,
                   OUTLINE_PALETTE[int(rng.integers(len(OUTLINE_PALETTE)))])

    if cfg.noise_sigma > 0:
        noise = rng.normal(0, cfg.noise_sigma, bg.shape)
        bg = np.clip(bg.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    return CurveChallenge(bg=bg, piece=_piece_image(kind, S, rng), path=path,
                          target_idx=target_idx, angle0=angle0, kind=kind,
                          config=cfg, path_decoy_idx=slots, free_decoys=free)


def render(ch: CurveChallenge, s: float) -> np.ndarray:
    """把实心图形按滑块值 s 合成到背景上，得到用户此刻看到的画面（预览/调试用）。"""
    x, y, deg = ch.pose(s)
    piece = ch.piece
    S = piece.shape[0]
    if abs(deg) > 0.01:
        M = cv2.getRotationMatrix2D((S / 2, S / 2), -deg, 1.0)
        piece = cv2.warpAffine(piece, M, (S, S), flags=cv2.INTER_LINEAR)
    out = ch.bg.copy()
    x0, y0 = int(round(x - S / 2)), int(round(y - S / 2))
    ys, xs = slice(max(0, y0), min(out.shape[0], y0 + S)), slice(max(0, x0), min(out.shape[1], x0 + S))
    pp = piece[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
    a = pp[:, :, 3:4].astype(np.float32) / 255.0
    out[ys, xs] = (out[ys, xs] * (1 - a) + pp[:, :, :3] * a).astype(np.uint8)
    return out
