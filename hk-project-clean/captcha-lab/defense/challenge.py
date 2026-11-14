"""
滑块验证码生成器（防守方）
==========================

这是实验室的"出题"一侧：给定一组可配置的难度旋钮，生成一张带缺口的背景图 +
对应的滑块小图，并记录真值缺口位置。它存在的意义不是"做一个好用的验证码"，
而是让我们能系统地问一个问题：**哪些设计让自动识别更难？**

所以每个旋钮都对应一种常见的"抗识别"手段，可以在 robustness.py 里量化它的收益：

  notch_shape     缺口形状：square / puzzle / rounded —— 非矩形更难被边框能量命中
  num_decoys      假缺口数量 —— 制造多个强候选，稀释 top1
  texture         背景纹理：gradient/noise/stripes/blocks/adversarial —— 对抗纹理压低信噪比
  edge_strength   描边强度 —— 越弱越难被 Canny/梯度抓到（但对人眼也更难）
  shadow_strength 阴影强度 —— 同上
  dim_factor      缺口内部压暗系数 —— 越接近 1 越不明显
  tolerance_px    判定容差 —— 出题方的核心安全参数

坐标约定：所有位置都是背景图像素坐标 (x, y, w, h)，左上角为原点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]
PIECE_PAD = 4                      # 滑块图四周的透明留边，前端换算位置时要扣掉
NOTCH_SHAPES = ("square", "puzzle", "rounded")
TEXTURES = ("gradient", "noise", "stripes", "blocks", "adversarial")


@dataclass
class ChallengeConfig:
    """一道题的难度配置。默认值对应一个"中等强度"的站点。"""
    width: int = 340
    height: int = 212
    notch_side: int = 46
    notch_shape: str = "puzzle"
    num_decoys: int = 1
    texture: str = "noise"
    edge_strength: float = 0.6        # 0~1，描边亮度占比
    shadow_strength: float = 0.6      # 0~1，阴影深度占比
    dim_factor: float = 0.78          # 缺口内部亮度乘子，1=不压暗
    noise_sigma: float = 8.0          # 叠加在整图上的高斯噪声
    tolerance_px: float = 6.0         # 判定容差（像素，真值坐标系）

    def validate(self) -> None:
        if self.notch_shape not in NOTCH_SHAPES:
            raise ValueError(f"notch_shape must be one of {NOTCH_SHAPES}")
        if self.texture not in TEXTURES:
            raise ValueError(f"texture must be one of {TEXTURES}")
        if not (0.0 <= self.edge_strength <= 1.0):
            raise ValueError("edge_strength must be in [0, 1]")
        if not (0.0 <= self.shadow_strength <= 1.0):
            raise ValueError("shadow_strength must be in [0, 1]")
        side = min(self.width, self.height)
        if not (12 <= self.notch_side <= side * 0.6):
            raise ValueError("notch_side out of sane range for the canvas")
        if self.num_decoys and self.notch_shape != "puzzle":
            # 人靠"凸凹形状和滑块对得上"来排除假缺口；方形/圆角的假缺口与真缺口一模一样，人也分不出
            raise ValueError("num_decoys > 0 requires notch_shape='puzzle'")


@dataclass
class Challenge:
    """一道生成好的题：背景图 + 滑块图 + 真值，以及用来对抗评测的干扰项。"""
    bg: np.ndarray                    # 带缺口的背景图 (H, W, 3) BGR
    piece: np.ndarray                 # 带 alpha 的滑块图 (h, w, 4) BGRA
    gap_x: int                        # 真缺口画布左上角 x
    gap_y: int
    side: int                         # 缺口画布边长（puzzle 含凸起）
    config: ChallengeConfig
    decoys: List[Rect] = field(default_factory=list)

    @property
    def gap_cx(self) -> float:
        return self.gap_x + self.side / 2.0

    def is_correct(self, cx: float) -> bool:
        """提交的缺口中心 x 是否落在容差内。"""
        return abs(float(cx) - self.gap_cx) <= self.config.tolerance_px


# --------------------------------------------------------------------------- #
# 背景纹理
# --------------------------------------------------------------------------- #
def _adversarial_texture(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """对抗纹理：大量与缺口同尺度的方块边缘，专门污染"边框能量"这类策略。

    边框能量策略假设"缺口 = 四边梯度强、内部平坦"。我们就铺满同样特征的假方块，
    让它分不清真假。这是最能压低自动识别率的背景之一。
    """
    img = rng.integers(70, 150, (h, w, 3)).astype(np.float32)
    for _ in range(rng.integers(26, 42)):
        s = int(rng.integers(26, 72))
        x = int(rng.integers(0, max(1, w - s)))
        y = int(rng.integers(0, max(1, h - s)))
        shade = int(rng.integers(30, 220))
        cv2.rectangle(img, (x, y), (x + s, y + s), (shade, shade, shade), 1)
        if rng.random() < 0.4:
            inner = img[y:y + s, x:x + s]
            inner[:] = inner * 0.85
    return np.clip(img, 0, 255).astype(np.uint8)


def _background(cfg: ChallengeConfig, rng: np.random.Generator) -> np.ndarray:
    w, h = cfg.width, cfg.height
    if cfg.texture == "gradient":
        c1, c2 = rng.integers(30, 225, 3), rng.integers(30, 225, 3)
        grad = np.linspace(0, 1, w)[None, :, None]
        img = (c1[None, None, :] * (1 - grad) + c2[None, None, :] * grad)
        img = np.repeat(img, h, axis=0)
    elif cfg.texture == "noise":
        base = rng.integers(60, 190, 3).astype(np.float32)
        img = base + rng.normal(0, 26, (h, w, 3))
    elif cfg.texture == "blocks":
        img = np.zeros((h, w, 3), np.float32) + rng.integers(40, 200)
        for _ in range(int(rng.integers(3, 7))):
            x1, y1 = int(rng.integers(0, w)), int(rng.integers(0, h))
            x2 = x1 + int(rng.integers(30, 140))
            y2 = y1 + int(rng.integers(30, 140))
            img[y1:y2, x1:x2] = rng.integers(20, 240, 3)
    elif cfg.texture == "stripes":
        img = np.zeros((h, w, 3), np.float32) + 120
        step = int(rng.integers(6, 22))
        for x in range(0, w, step):
            img[:, x:x + step // 2] += rng.integers(-70, 70)
    else:  # adversarial
        return _adversarial_texture(w, h, rng)
    img = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0)


# --------------------------------------------------------------------------- #
# 缺口形状 mask
# --------------------------------------------------------------------------- #
def _puzzle_bump(side: int) -> int:
    return max(5, side // 6)


def _notch_mask(shape: str, side: int, rng: np.random.Generator) -> np.ndarray:
    """返回 0/255 的正方形 mask（255 = 属于缺口/滑块块）。

    side 始终是缺口"主体"的边长，保证不同形状之间可比；puzzle 的凸起画在主体外侧，
    所以它的画布是 side + 2*bump。
    """
    if shape == "square":
        return np.full((side, side), 255, np.uint8)
    if shape == "rounded":
        m = np.zeros((side, side), np.uint8)
        r = max(4, side // 5)
        cv2.rectangle(m, (r, 0), (side - r, side - 1), 255, -1)
        cv2.rectangle(m, (0, r), (side - 1, side - r), 255, -1)
        for cx, cy in ((r, r), (side - r, r), (r, side - r), (side - r, side - r)):
            cv2.circle(m, (cx, cy), r, 255, -1)
        return m

    # puzzle：经典拼图，四边各一个半圆，随机凸出或凹进
    bump = _puzzle_bump(side)
    size = side + 2 * bump
    m = np.zeros((size, size), np.uint8)
    lo, hi, mid = bump, bump + side - 1, size // 2
    cv2.rectangle(m, (lo, lo), (hi, hi), 255, -1)
    for cx, cy in ((mid, lo), (mid, hi), (lo, mid), (hi, mid)):
        color = 255 if rng.random() < 0.5 else 0
        cv2.circle(m, (cx, cy), bump, color, -1)
    return m


def canvas_side(cfg: ChallengeConfig) -> int:
    """缺口/滑块画布边长（含 puzzle 凸起）。"""
    if cfg.notch_shape == "puzzle":
        return cfg.notch_side + 2 * _puzzle_bump(cfg.notch_side)
    return cfg.notch_side


# --------------------------------------------------------------------------- #
# 合成
# --------------------------------------------------------------------------- #
def _stamp_notch(img: np.ndarray, x: int, y: int, mask: np.ndarray,
                 cfg: ChallengeConfig) -> None:
    """就地把一个缺口画到背景上：压暗 + 描边 + 阴影，强度由 cfg 控制。原地修改 img。"""
    side = mask.shape[0]
    roi = img[y:y + side, x:x + side].astype(np.float32)
    sel = mask > 0

    dim = cfg.dim_factor
    roi[sel] *= dim
    img[y:y + side, x:x + side] = np.clip(roi, 0, 255).astype(np.uint8)

    # 描边：沿 mask 边缘画浅色线，亮度随 edge_strength
    edge_val = int(140 + 115 * cfg.edge_strength)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img[y:y + side, x:x + side], cnts, -1,
                     (edge_val, edge_val, edge_val), 1)

    # 阴影：缺口右/下方向偏移一份暗色 mask
    if cfg.shadow_strength > 0:
        off = max(2, int(3 * cfg.shadow_strength))
        sv = int(70 * (1 - cfg.shadow_strength))
        sy0, sy1 = y + off, min(img.shape[0], y + side + off)
        sx0, sx1 = x + off, min(img.shape[1], x + side + off)
        sh_mask = mask[: sy1 - sy0, : sx1 - sx0]
        region = img[sy0:sy1, sx0:sx1].astype(np.float32)
        region[sh_mask > 0] = region[sh_mask > 0] * 0.4 + sv * 0.6
        img[sy0:sy1, sx0:sx1] = np.clip(region, 0, 255).astype(np.uint8)


def _make_piece(patch: np.ndarray, mask: np.ndarray,
                cfg: ChallengeConfig) -> np.ndarray:
    """用真值区域的像素 + 形状 mask 做一张带 alpha 的滑块图。"""
    side, p = mask.shape[0], PIECE_PAD
    piece = np.zeros((side + 2 * p, side + 2 * p, 4), np.uint8)
    piece[p:p + side, p:p + side, :3] = patch
    piece[p:p + side, p:p + side, 3] = mask
    edge_val = int(150 + 105 * cfg.edge_strength)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    shifted = [c + p for c in cnts]
    cv2.drawContours(piece, shifted, -1, (edge_val, edge_val, edge_val, 255), 1)
    return piece


def _place_row(rng: np.random.Generator, w: int, side: int, k: int,
               min_gap: int) -> List[int]:
    """在 [0.28w, w-side-6] 内放 k 个互不重叠的 x（间距 ≥ min_gap），放不下就少放。

    构造式采样：先按最小间距排好，再把剩余空隙随机撒进去，保证可行时一定成功。
    左侧 28% 留给滑块初始位置，缺口不会出现在那里。
    """
    lo, hi = int(w * 0.28), max(int(w * 0.28), w - side - 6)
    while k > 1 and (hi - lo) < (k - 1) * min_gap:
        k -= 1
    slack = (hi - lo) - (k - 1) * min_gap
    offs = np.sort(rng.integers(0, slack + 1, k))
    return [int(lo + o + i * min_gap) for i, o in enumerate(offs)]


def generate(cfg: Optional[ChallengeConfig] = None,
             seed: Optional[int] = None) -> Challenge:
    """按配置生成一道题。"""
    cfg = cfg or ChallengeConfig()
    cfg.validate()
    rng = np.random.default_rng(seed)
    w, h, side = cfg.width, cfg.height, canvas_side(cfg)

    bg = _background(cfg, rng)
    y = int(rng.integers(4, max(5, h - side - 4)))

    xs = _place_row(rng, w, side, 1 + cfg.num_decoys, min_gap=side + 4)
    gap_x = xs.pop(int(rng.integers(0, len(xs))))

    mask = _notch_mask(cfg.notch_shape, cfg.notch_side, rng)
    patch = bg[y:y + side, gap_x:gap_x + side].copy()
    piece = _make_piece(patch, mask, cfg)

    # 假缺口和真缺口同一行：滑块只能水平移动，前端也会下发 piece_y，
    # 放在别的行等于白送——攻击者按行过滤就排除了。形状必须与真缺口不同，人才能分辨。
    decoys: List[Rect] = []
    for dx in xs:
        dmask = _notch_mask(cfg.notch_shape, cfg.notch_side, rng)
        while np.array_equal(dmask, mask):
            dmask = _notch_mask(cfg.notch_shape, cfg.notch_side, rng)
        _stamp_notch(bg, dx, y, dmask, cfg)
        decoys.append((dx, y, side, side))

    _stamp_notch(bg, gap_x, y, mask, cfg)

    if cfg.noise_sigma > 0:
        noise = rng.normal(0, cfg.noise_sigma, bg.shape)
        bg = np.clip(bg.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    bg = cv2.GaussianBlur(bg, (3, 3), 0)

    return Challenge(bg=bg, piece=piece, gap_x=gap_x, gap_y=y, side=side,
                     config=cfg, decoys=decoys)


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


if __name__ == "__main__":
    import os
    os.makedirs("out", exist_ok=True)
    for shape in NOTCH_SHAPES:
        for tex in TEXTURES:
            decoys = 2 if shape == "puzzle" else 0
            c = generate(ChallengeConfig(notch_shape=shape, texture=tex,
                                         num_decoys=decoys), seed=7)
            name = f"out/challenge_{shape}_{tex}.png"
            cv2.imencode(".png", c.bg)[1].tofile(name)
            print(f"{name}  gap_cx={c.gap_cx:.0f}  decoys={len(c.decoys)}")
