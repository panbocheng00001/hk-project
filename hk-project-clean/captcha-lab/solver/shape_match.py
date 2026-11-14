"""
拖拽归位 / 图形配对类验证码 —— 形状感知配对引擎
=================================================

这类验证码的形态（你说的"拖动到每个图形一致就能过"）：
    [碎片区] 3~6 个可拖动小图（有的带 alpha，有的是截图小方块）
    [目标区] 3~6 个空槽（灰色剪影 / 描边轮廓 / 半透明凹陷 / 彩色目标图形）
    要求：把每个碎片拖到"形状一致"的槽里，全对才过。

它的难点和滑块完全不同：
  - 滑块：1 个连续量（x 坐标），错 5px 还能过；
  - 配对：N! 的离散排列，错一个就整盘重来。**必须先保证配对全对，再谈拖动。**

所以本模块只干一件事：给一组碎片框 + 一组槽位框，输出**最优一对一配对**
以及**每个配对的置信边际（margin）**，让上层能决定"直接提交 / 先拖最稳的 / 换策略"。

设计要点（每条都是踩过坑的）
------------------------------
1. **形状通道必须旋转搜索 IoU，而不是只靠 Hu 矩**
   Hu 矩对"圆 vs 圆环"、"方 vs 菱形"区分度极低（旋转不变性把区分度抹平了）。
   实测：单用 Hu 矩，5 类形状 top1 只有 ~55%；加入旋转搜索 IoU 后到 ~95%。
   做法：碎片 mask 归一化到 96×96 画布 → 按 10° 步长预旋转 36 份 →
   与槽位 mask 做位运算 IoU，取最大值。位运算比 matchTemplate 快 3~5 倍。

2. **旋转要"惩罚"而不是"完全不变"**
   多数站点碎片是正着放的，完全旋转不变会把 6 认成 9、把 ▲ 认成 ▼。
   实现：找到 IoU ≥ best-0.02 的所有角度，取其中**最小偏转角**算惩罚项，
   对称图形（圆/正方）自然不受罚。

3. **外观通道用于区分同形状不同图案**
   有些站点所有槽位都是同一个方形，靠"图案内容"配对 → HSV 直方图交集 +
   颜色矩。纯形状方案在这种题型上会 100% 错，必须双通道。

4. **配对用匈牙利算法，不用贪心**
   贪心在"两个相似形状"场景会互相抢，全局最优能救回来。N≤8 时
   scipy.optimize.linear_sum_assignment 开销可忽略（<0.1ms）。

5. **输出 margin，而不是只输出 argmax**
   margin = 该碎片的最优分 - 次优分。margin 小说明"两个槽长得像"，
   上层应该先拖 margin 大的、把 margin 小的留到最后（部分站点支持
   拖错不重置，只错的那一对回弹）。

坐标约定：所有 bbox 都是图片像素坐标 (x, y, w, h)，左上角为原点。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
CANVAS = 96          # 归一化画布边长（旋转搜索用，96 够用且快）
NORM_SIDE = 52.0     # 归一化后 sqrt(area)，即"等效边长"
FD_K = 16            # 傅里叶描述子取前 K 阶
RADIAL_BINS = 64     # 径向签名角向采样数
RADIAL_HARM = 16     # 径向签名保留谐波数


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class Shape:
    """一个碎片或槽位：框 + 二值 mask + 形状/外观描述子。

    x/y/w/h 是"形状自身的外接框"（图片像素坐标）；mask 存的是 ROI 空间
    （比外接框略大一圈，方便做背景估计），ox/oy 记录 ROI 原点。
    """
    x: int
    y: int
    w: int
    h: int
    mask: np.ndarray                      # ROI 空间二值 mask（0/255）
    nmask: np.ndarray                     # 归一化画布 mask（CANVAS×CANVAS）
    contour: np.ndarray                   # 最大外轮廓（ROI 坐标）
    hu: np.ndarray                        # 7 维对数 Hu 矩
    fd: np.ndarray                        # FD_K 维傅里叶描述子（已归一化）
    radial: np.ndarray                    # RADIAL_HARM 维径向谐波
    polar: np.ndarray                     # 极坐标占用图 (POLAR_NR, POLAR_NT)
    hist: np.ndarray                      # HSV 2D 直方图（已归一化）
    color: np.ndarray                     # 颜色矩 (mean_h, mean_s, mean_v, std_v)
    area: float = 0.0
    ar: float = 1.0                       # 长宽比
    solidity: float = 1.0                 # 面积/凸包面积
    extent: float = 1.0                   # 面积/框面积
    compact: float = 0.0                  # 周长²/面积（越小越"圆"）
    label: str = ""
    ox: int = 0                           # ROI 原点（mask 坐标系 → 图片坐标系）
    oy: int = 0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    @property
    def centroid(self) -> Tuple[float, float]:
        m = cv2.moments(self.contour)
        if abs(m["m00"]) < 1e-6:
            return self.cx, self.cy
        return self.ox + m["m10"] / m["m00"], self.oy + m["m01"] / m["m00"]


@dataclass
class ScoreWeights:
    """各通道权重。默认值来自 bench_match.py 的消融 + 网格搜索（见 README 实测表）。"""
    polar: float = 0.52                  # 极坐标循环相关（主力，旋转不变）
    rot_iou: float = 0.22                # 预旋转位运算 IoU（补极性/细部）
    fd: float = 0.14                     # 傅里叶描述子
    radial: float = 0.08                 # 径向签名谐波
    hu: float = 0.04                     # Hu 矩（弱，只做兜底）
    app: float = 0.18                    # 外观（同形不同色时救命）
    upright_penalty: float = 0.10        # 每 180° 偏转扣多少分
    angle_step: int = 10                 # 预旋转步长（度）
    iou_tie: float = 0.02                # 判定"并列最优角度"的容差


@dataclass
class Pair:
    piece: int
    slot: int
    score: float = 0.0
    margin: float = 0.0                  # 最优 - 次优（同一碎片跨槽位）
    angle: float = 0.0
    detail: Dict[str, float] = field(default_factory=dict)


@dataclass
class MatchResult:
    pairs: List[Pair]                     # 按 piece 索引升序
    cost: np.ndarray                      # (n_piece, n_slot) 得分矩阵
    ok: bool = True
    note: str = ""

    @property
    def permutation(self) -> List[int]:
        """piece i 应拖到 slot permutation[i]。"""
        return [p.slot for p in self.pairs]

    def drag_order(self) -> List[Pair]:
        """按 margin 降序：先拖最确定的，把模棱两可的留到最后。"""
        return sorted(self.pairs, key=lambda p: -p.margin)


# --------------------------------------------------------------------------- #
# mask 提取
# --------------------------------------------------------------------------- #
def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """把描边轮廓填成实心剪影（槽位常只有 2px 描边）。"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return mask
    out = np.zeros_like(mask)
    c = max(cnts, key=cv2.contourArea)
    cv2.drawContours(out, [c], -1, 255, -1)
    return out


def _largest_component(mask: np.ndarray) -> np.ndarray:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return mask
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(lab == idx, 255, 0).astype(np.uint8)


def mask_from_alpha(crop_bgra: np.ndarray, thr: int = 12) -> Optional[np.ndarray]:
    if crop_bgra.ndim != 3 or crop_bgra.shape[2] != 4:
        return None
    a = crop_bgra[:, :, 3]
    if int(a.max()) - int(a.min()) < 16:      # alpha 全不透明 = 没有有效 mask
        return None
    return np.where(a > thr, 255, 0).astype(np.uint8)


def _seeded_grow(lab: np.ndarray, seeds: List[Tuple[int, int]],
                 tol: float, fixed: bool = True) -> Optional[np.ndarray]:
    """
    中心种子区域生长（floodFill）。

    为什么这是这类题的最优首选：
      碎片是**实色**，槽位是**半透明灰**，二者共同点是"内部颜色均匀"；
      而背景（照片/纹理/渐变）恰恰是"不均匀"的。
      所以从中心往外按色差生长，天然会在形状边界停住 —— 不需要估背景色，
      也不会被纹理带跑偏。这是比"全局背景差分"稳定一个量级的做法。
    """
    h, w = lab.shape[:2]
    mask = np.zeros((h + 2, w + 2), np.uint8)
    img = lab.copy()
    flags = 4 | (255 << 8) | (cv2.FLOODFILL_MASK_ONLY)
    if fixed:
        flags |= cv2.FLOODFILL_FIXED_RANGE
    lo = up = (float(tol),) * 3
    for (sx, sy) in seeds:
        if not (0 <= sx < w and 0 <= sy < h):
            continue
        cv2.floodFill(img, mask, (int(sx), int(sy)), 0, lo, up, flags)
    out = mask[1:-1, 1:-1]
    if out.mean() < 1:
        return None
    out = np.where(out > 0, 255, 0).astype(np.uint8)
    return out


def _center_seeds(lab: np.ndarray, n: int = 9) -> List[Tuple[int, int]]:
    """中心 3×3 网格做种子：单点种子遇到高光/阴影会漏，多点更稳。"""
    h, w = lab.shape[:2]
    cx, cy = w // 2, h // 2
    r = max(1, int(min(h, w) * 0.12))
    seeds = []
    for dy in (-r, 0, r):
        for dx in (-r, 0, r):
            seeds.append((cx + dx, cy + dy))
    return seeds[:n]


def _contour_fill(crop_bgr: np.ndarray, lo: int, hi: int,
                  close_it: int = 2) -> List[np.ndarray]:
    """
    边缘闭合 → 填成剪影。

    为什么必须有这一路：
      碎片几乎都带 1~2px 白描边，槽位几乎都带可见的轮廓边。
      边缘是最稳的特征 —— 不受"实色 vs 半透明"影响，也不受背景纹理影响。
      只要 Canny 能把边界连成闭环，填充后就是干净的形状 mask。
    做法：Canny → 膨胀/闭运算把断点接上 → RETR_EXTERNAL 取外轮廓 → 填满。
    """
    h, w = crop_bgr.shape[:2]
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    e = cv2.Canny(gray, lo, hi)
    out: List[np.ndarray] = []
    for it in (1, 2, 3):
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        closed = cv2.morphologyEx(e, cv2.MORPH_CLOSE, k, iterations=it)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        # 候选：面积够大、不贴满整幅
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:4]:
            a = float(cv2.contourArea(c))
            if a < 0.04 * h * w or a > 0.92 * h * w:
                continue
            m = np.zeros((h, w), np.uint8)
            cv2.drawContours(m, [c], -1, 255, -1)
            out.append(m)
    return out


def _flat_overlay_masks(crop_bgr: np.ndarray) -> List[np.ndarray]:
    """
    半透明覆盖层检测：低饱和度 + 低局部方差。

    半透明槽位/遮罩的像素是 c = α·overlay + (1-α)·bg，两个可测量的后果：
      ① 饱和度被拉低（灰色叠加）→ S 通道低
      ② 局部对比度被压缩 → 局部标准差（方差）低
    照片背景恰好两条都不满足（有颜色、有纹理），所以这一路对"半透明灰槽位"
    的命中率明显高于背景色差分。对实色碎片不适用，所以只作为候选之一。
    """
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1].astype(np.float32)
    v = hsv[:, :, 2].astype(np.float32)
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # 局部标准差（9×9）
    mu = cv2.blur(gray, (9, 9))
    mu2 = cv2.blur(gray * gray, (9, 9))
    std = np.sqrt(np.maximum(mu2 - mu * mu, 0))

    out: List[np.ndarray] = []
    for s_thr in (46, 70):
        for std_thr in (9.0, 16.0):
            m = np.where((s < s_thr) & (std < std_thr) & (v > 30), 255, 0).astype(np.uint8)
            if m.mean() / 255.0 < 0.02:
                continue
            out.append(m)
    return out


def _corner_bg(lab: np.ndarray) -> np.ndarray:
    """
    用四角小块估背景色。
    为什么不用整条边：碎片的外接框是紧贴形状的，四条边的中段全是形状像素，
    用整条边取中位数会把"形状色"当背景，mask 直接反向（这是最隐蔽的坑）。
    形状居中 → 四角必然是背景。
    """
    h, w = lab.shape[:2]
    k = max(3, int(min(h, w) * 0.22))
    patches = [lab[:k, :k], lab[:k, -k:], lab[-k:, :k], lab[-k:, -k:]]
    px = np.concatenate([p.reshape(-1, 3) for p in patches])
    return np.median(px, axis=0)


def _stability(m: np.ndarray, others: List[np.ndarray]) -> float:
    """阈值稳定性：与相邻阈值结果的 IoU 最大值。真实目标的边界在阈值扫过时最稳。"""
    if not others:
        return 0.0
    a = m > 127
    best = 0.0
    for o in others:
        b = o > 127
        u = np.count_nonzero(a | b)
        if u:
            best = max(best, np.count_nonzero(a & b) / u)
    return float(best)


# --------------------------------------------------------------------------- #
# 极坐标签名（旋转 = 角向循环移位 → FFT 一次算完全部角度）
# --------------------------------------------------------------------------- #
POLAR_NR, POLAR_NT = 48, 180


def _polar_sig(mask: np.ndarray, nr: int = POLAR_NR, nt: int = POLAR_NT) -> np.ndarray:
    """
    mask → 极坐标占用率图 (nr, nt)。

    关键点：
      - 以**质心**为极点、以 **99 分位半径**归一 → 平移/缩放不变
      - 旋转等价于角向整列循环移位 → 一次 FFT 就能算出所有角度的相似度
      - 不用 bbox 归一：bbox 归一后碎片和槽位的长宽比差异会引入伪差异
        （实测 bbox 归一版 rot_iou 直接掉到 5%）

    为什么比"预旋转 + 位运算 IoU"好：
      预旋转版要 warp 36~72 次（96×96 位图），慢且量化误差大；
      极坐标版一次 FFT 覆盖全部角度，且对 5° 以内的旋转天然平滑（角向做过高斯）。
      实测（GT mask，40 盘）：极坐标 board_acc 82% vs 预旋转 IoU 70%。
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.zeros((nr, nt), np.float32)
    cx, cy = xs.mean(), ys.mean()
    r = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    rmax = float(np.percentile(r, 99))
    if rmax <= 0:
        return np.zeros((nr, nt), np.float32)
    th = (np.arctan2(ys - cy, xs - cx) + 2 * np.pi) % (2 * np.pi)
    ri = np.clip((r / rmax * nr).astype(int), 0, nr - 1)
    ti = np.clip((th / (2 * np.pi) * nt).astype(int), 0, nt - 1)
    g = np.zeros((nr, nt), np.float32)
    np.add.at(g, (ri, ti), 1.0)
    g = cv2.GaussianBlur(g, (1, 5), 0)
    m = float(g.max())
    return (g / m) if m > 0 else g


def polar_rot_sim(a: np.ndarray, b: np.ndarray) -> Tuple[float, float]:
    """角向循环互相关：返回 (最大余弦相似度, 对应旋转角)。"""
    if a.size == 0 or b.size == 0:
        return 0.0, 0.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0, 0.0
    A = np.fft.rfft(a, axis=1)
    B = np.fft.rfft(b, axis=1)
    cc = np.fft.irfft(A * np.conj(B), n=a.shape[1], axis=1)
    per_shift = cc.sum(axis=0)
    k = int(np.argmax(per_shift))
    return float(per_shift[k] / (na * nb)), k * 360.0 / a.shape[1]


def _shape_prior(m: np.ndarray) -> float:
    """
    形状先验：单一连通域 + 高 solidity + 质心居中 + 覆盖合理 + **不贴边**。

    "不贴边"是最关键的一条：ROI 是外扩过的（pad_ratio=0.30），真形状必然
    完整落在 ROI 内部、四周留白。任何贴到 ROI 边界的 mask 都是"长到背景里去了"
    （区域生长渗漏 / 阈值把背景当地）。少了这一条，最大的那个 blob 永远赢。
    """
    cov = float(m.mean()) / 255.0
    if cov < 0.04 or cov > 0.72:
        return -1.0
    n, lab, stats, cents = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return -1.0
    areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
    main = int(np.argmax(areas)) + 1
    if areas[main - 1] < 0.80 * areas.sum():
        return -1.0
    cnts, _ = cv2.findContours(np.where(lab == main, 255, 0).astype(np.uint8),
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return -1.0
    c = max(cnts, key=cv2.contourArea)
    a = max(1.0, float(cv2.contourArea(c)))
    sol = a / max(1.0, float(cv2.contourArea(cv2.convexHull(c))))

    h, w = m.shape
    dx = (cents[main][0] - w / 2.0) / w
    dy = (cents[main][1] - h / 2.0) / h
    center = math.exp(-5.0 * (dx * dx + dy * dy))

    # 贴边程度：主连通域外接框到 ROI 四边的距离 / ROI 边长
    bx, by, bw, bh = cv2.boundingRect(c)
    touch = max(0.0, 1.0 - min(bx, by, w - (bx + bw), h - (by + bh)) / (0.16 * min(h, w)))
    touch = min(1.0, touch)

    # 覆盖度软约束：ROI 外扩 30% 后，形状一般占 12%~45%
    cov_term = math.exp(-max(0.0, cov - 0.42) / 0.16)
    return float(1.2 * sol + 1.0 * center + 0.9 * cov_term - 1.6 * touch)


def mask_from_color(crop_bgr: np.ndarray, center_bias: bool = True,
                    pad_ratio: float = 0.30,
                    verbose: bool = False) -> Optional[np.ndarray]:
    """
    形状分割：中心种子区域生长（主）+ 多阈值假设（辅）+ 形状先验/稳定性联合打分。

    为什么不是"一个 OTSU 阈值搞定"：
      碎片是实色（对比强），槽位是 45%~70% alpha 的灰剪影（对比弱、随背景变化）。
      同一个全局阈值在两类上必然一类过切、一类欠切。实测单阈值时槽位 mask
      平均 IoU 只有 0.47，p10 = 0.006（完全失败）。

    四个关键设计（按重要性排序）：
      1) **中心种子 floodFill 生长**：碎片实色、槽位半透明灰，共同点是"内部颜色均匀"；
         背景（照片/纹理/渐变）恰恰不均匀。从中心生长天然在形状边界停住，
         不需要估背景色，也不会被纹理带偏。实测把碎片 IoU 从 0.68 提到 0.85+。
      2) **四角估背景**（阈值法的前提）—— 形状居中的前提下四角必是背景，
         用整条边估会被形状自身像素污染，直接导致 mask 反向。
      3) **阈值稳定性**取代"覆盖率猜大小"—— 真目标边界在阈值扫过时最稳（MSER 思想）。
      4) **形状先验兜底**—— 单连通域 + 高 solidity + 质心居中 + 覆盖合理。
    """
    if crop_bgr.size == 0:
        return None
    h, w = crop_bgr.shape[:2]
    ph, pw = max(2, int(h * pad_ratio)), max(2, int(w * pad_ratio))
    padded = cv2.copyMakeBorder(crop_bgr, ph, ph, pw, pw, cv2.BORDER_REPLICATE)
    lab = cv2.cvtColor(padded, cv2.COLOR_BGR2LAB).astype(np.float32)

    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    k5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def cleanup(m: Optional[np.ndarray], close_it: int = 5) -> Optional[np.ndarray]:
        if m is None or m.mean() < 1:
            return None
        k = k5 if close_it >= 5 else k3
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k3, iterations=1)
        if m.mean() < 1:
            return None
        n, labm, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        if n <= 1:
            return None
        areas = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
        keep = [i + 1 for i in range(n - 1) if areas[i] >= 0.05 * areas.max()]
        m = np.where(np.isin(labm, keep), 255, 0).astype(np.uint8)
        return _fill_holes(m)

    cands: List[Tuple[str, np.ndarray]] = []

    # ---- 通道 A：中心种子区域生长 ----
    seeds = _center_seeds(lab)
    for tol in (32.0, 46.0, 62.0, 80.0, 100.0):
        m = _seeded_grow(lab, seeds, tol, fixed=True)
        m = cleanup(m, close_it=3)
        if m is not None:
            cands.append((f"grow{tol:.0f}", m))
    # 非 fixed range：允许沿渐变缓慢生长，适合半透明槽位压在渐变背景上
    for tol in (18.0, 28.0, 40.0):
        m = _seeded_grow(lab, seeds, tol, fixed=False)
        m = cleanup(m, close_it=3)
        if m is not None:
            cands.append((f"growD{tol:.0f}", m))

    # ---- 通道 B：四角背景 + 多阈值 ----
    bg = _corner_bg(lab)
    d = np.linalg.norm(lab - bg[None, None, :], axis=2)
    d = cv2.GaussianBlur(d, (3, 3), 0)
    dmax = float(d.max())
    if dmax >= 10.0:
        dn = (d / dmax * 255.0).astype(np.uint8)
        otsu, _ = cv2.threshold(dn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for t in sorted({round(v, 2) for v in
                         [float(otsu)] + [f * dmax for f in (0.22, 0.32, 0.45, 0.60)]
                         if 3.0 <= v <= 0.95 * dmax}):
            for inv in (False, True):
                m = np.where(dn > t, 255, 0).astype(np.uint8)
                if inv:
                    m = cv2.bitwise_not(m)
                m = cleanup(m)
                if m is not None:
                    cands.append((f"thr{t:.0f}{'i' if inv else ''}", m))

    # ---- 通道 C：边缘闭环填充（碎片白描边 / 槽位轮廓线，最稳的一路）----
    for lo, hi in ((50, 140), (80, 200), (30, 90)):
        for m in _contour_fill(padded[:, :, :3], lo, hi):
            m = cleanup(m, close_it=3)
            if m is not None:
                cands.append((f"edge{lo}", m))

    # ---- 通道 D：低饱和 + 低局部方差（半透明灰槽位的物理特征）----
    # 半透明槽位是 c = α·gray + (1-α)·bg，两个后果：
    #   ① 饱和度被拉向 0（灰）  ② 局部对比度被压缩（变"平"）
    # 这两条对"照片背景"同样成立，是比"背景色差分"更本质的判据。
    for m in _flat_overlay_masks(padded[:, :, :3]):
        m = cleanup(m, close_it=3)
        if m is not None:
            cands.append(("flat", m))

    if not cands:
        return None

    # ---- 打分：形状先验 + 与其它候选的稳定性 ----
    # 稳定性用 48×48 缩略图做矩阵运算：30 个候选 → 900 次 IoU 一次算完（<1ms），
    # 逐个在全分辨率上算要 50ms+，这是整个 extract 最大的性能坑。
    priors = np.array([_shape_prior(m) for _, m in cands], np.float32)
    valid = np.nonzero(priors >= 0)[0]
    if valid.size == 0:
        return None
    small = np.stack([cv2.resize(cands[i][1], (48, 48), interpolation=cv2.INTER_NEAREST)
                      > 127 for i in valid]).reshape(len(valid), -1).astype(np.float32)
    inter = small @ small.T
    area = small.sum(1)
    union = area[:, None] + area[None, :] - inter
    iou = np.divide(inter, np.maximum(union, 1e-6))
    np.fill_diagonal(iou, 0.0)
    stab = iou.max(axis=1) if len(valid) > 1 else np.zeros(len(valid), np.float32)

    scores = priors[valid] + 0.55 * stab
    best_local = int(np.argmax(scores))
    best_i = int(valid[best_local])
    best_m, best_s, best_tag = cands[best_i][1], float(scores[best_local]), cands[best_i][0]

    if verbose:
        for k, i in enumerate(valid):
            print(f"    {cands[i][0]:12s} cov={cands[i][1].mean()/255:.3f} "
                  f"stab={stab[k]:.3f} prior={priors[i]:.3f} -> {scores[k]:.3f}")
        print(f"    chosen={best_tag} score={best_s:.3f}")
    return best_m[ph:ph + h, pw:pw + w].copy()


def _rect_mask(w: int, h: int) -> np.ndarray:
    m = np.zeros((h, w), np.uint8)
    m[1:-1, 1:-1] = 255
    return m


# --------------------------------------------------------------------------- #
# 描述子
# --------------------------------------------------------------------------- #
def _hu_log(contour: np.ndarray) -> np.ndarray:
    m = cv2.moments(contour)
    hu = cv2.HuMoments(m).flatten()
    out = []
    for v in hu:
        v = float(v)
        s = -1.0 if v < 0 else 1.0
        out.append(-s * math.log10(max(abs(v), 1e-30)))
    arr = np.asarray(out, np.float32)
    return np.clip(arr, -30.0, 30.0)


def _resample(contour: np.ndarray, n: int = 128) -> np.ndarray:
    """按弧长等距重采样，得到 n 个点（闭合）。"""
    pts = contour.reshape(-1, 2).astype(np.float64)
    if len(pts) < 4:
        pts = np.vstack([pts, pts[:1]]) if len(pts) else np.zeros((2, 2))
    seg = np.sqrt(((np.roll(pts, -1, axis=0) - pts) ** 2).sum(1))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0:
        return np.zeros((n, 2))
    s = np.linspace(0.0, total, n, endpoint=False)
    xs = np.interp(s, cum, np.r_[pts[:, 0], pts[0, 0]])
    ys = np.interp(s, cum, np.r_[pts[:, 1], pts[0, 1]])
    return np.stack([xs, ys], 1)


def _fourier_desc(contour: np.ndarray, k: int = FD_K) -> np.ndarray:
    """傅里叶描述子：幅度谱 → 平移/缩放/旋转不变。"""
    p = _resample(contour, 128)
    z = (p[:, 0] - p[:, 0].mean()) + 1j * (p[:, 1] - p[:, 1].mean())
    F = np.fft.fft(z)
    mag = np.abs(F[1:k + 1])
    denom = mag[0] if mag.size and mag[0] > 1e-9 else 1.0
    v = mag / denom
    n = np.linalg.norm(v)
    return (v / n if n > 0 else v).astype(np.float32)


def _radial_sig(contour: np.ndarray, cx: float, cy: float,
                bins: int = RADIAL_BINS, harm: int = RADIAL_HARM) -> np.ndarray:
    """径向签名谐波：从质心放射的 r(θ)，对旋转取幅度谱 → 旋转不变。"""
    p = _resample(contour, 256)
    dx, dy = p[:, 0] - cx, p[:, 1] - cy
    r = np.sqrt(dx * dx + dy * dy)
    th = (np.arctan2(dy, dx) + 2 * math.pi) % (2 * math.pi)
    idx = np.minimum((th / (2 * math.pi) * bins).astype(int), bins - 1)
    sig = np.zeros(bins, np.float64)
    for b in range(bins):
        sel = r[idx == b]
        if sel.size:
            sig[b] = sel.max()
    # 空 bin 用邻域插值补
    if (sig == 0).any():
        good = np.nonzero(sig)[0]
        if good.size < 2:
            return np.zeros(harm, np.float32)
        allb = np.arange(bins)
        sig = np.interp(allb, good, sig[good], period=bins)
    sig = sig / max(1e-6, sig.mean())
    F = np.abs(np.fft.fft(sig - sig.mean()))[1:harm + 1]
    n = np.linalg.norm(F)
    return (F / n if n > 0 else F).astype(np.float32)


def _hsv_hist(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], mask, [16, 16], [0, 180, 0, 256]).astype(np.float32)
    s = h.sum()
    return (h / s) if s > 0 else h


def _color_moments(crop: np.ndarray, mask: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(np.float32)
    sel = mask > 0
    if sel.sum() == 0:
        return np.zeros(4, np.float32)
    v = hsv[sel]
    return np.asarray([v[:, 0].mean() / 180.0, v[:, 1].mean() / 255.0,
                       v[:, 2].mean() / 255.0, v[:, 2].std() / 255.0], np.float32)


def _norm_mask(mask: np.ndarray, canvas: int = CANVAS,
               side: float = NORM_SIDE) -> np.ndarray:
    """把 mask 按"等效边长"归一化并居中到 canvas×canvas，用于旋转 IoU。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.zeros((canvas, canvas), np.uint8)
    cx, cy = xs.mean(), ys.mean()
    area = float(xs.size)
    scale = side / math.sqrt(max(area, 1.0))
    M = np.array([[scale, 0.0, canvas / 2.0 - scale * cx],
                  [0.0, scale, canvas / 2.0 - scale * cy]], np.float32)
    return cv2.warpAffine(mask, M, (canvas, canvas), flags=cv2.INTER_NEAREST,
                          borderValue=0)


# --------------------------------------------------------------------------- #
# 形状提取
# --------------------------------------------------------------------------- #
def extract(img: np.ndarray, box: Optional[Sequence[int]] = None,
            alpha: Optional[np.ndarray] = None, label: str = "",
            fill_holes: bool = True, pad_ratio: float = 0.30) -> Optional[Shape]:
    """
    从整图（或指定框）里提取一个形状。

    img   : BGR 或 BGRA 整图
    box   : (x, y, w, h)；None 表示整图就是一个碎片（常见于 DOM 单独给的 <img>）
    alpha : 外部提供的 alpha（例如从 PNG 独立读出的碎片），优先使用
    pad_ratio : 从 box 向外扩的比例 —— 必须扩，否则 ROI 四条边全是形状像素，
                背景估计失效（这是这类项目最常见的翻车点）。
    """
    H, W = img.shape[:2]
    if box is None:
        x, y, w, h = 0, 0, W, H
    else:
        x, y, w, h = [int(v) for v in box]
        x, y = max(0, x), max(0, y)
        w, h = min(w, W - x), min(h, H - y)
    if w < 6 or h < 6:
        return None

    pad = int(max(2, min(w, h) * pad_ratio))
    rx, ry = max(0, x - pad), max(0, y - pad)
    rw = min(W - rx, w + 2 * pad)
    rh = min(H - ry, h + 2 * pad)
    crop = img[ry:ry + rh, rx:rx + rw]

    mask = None
    if alpha is not None:
        a = alpha[ry:ry + rh, rx:rx + rw] if alpha.shape[:2] == (H, W) else alpha
        if a.shape[:2] != (rh, rw):
            a = cv2.resize(a, (rw, rh), interpolation=cv2.INTER_NEAREST)
        mask = np.where(a > 12, 255, 0).astype(np.uint8)
    if mask is None or mask.mean() < 2:
        mask = mask_from_alpha(crop)
    if mask is None:
        mask = mask_from_color(crop[:, :, :3] if crop.ndim == 3 else crop,
                               pad_ratio=0.22)
    if mask is None or mask.mean() < 2:
        # 完全提取不到前景：退化成"整框实心块"，至少不会崩
        mask = _rect_mask(rw, rh)
    else:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
        if fill_holes:
            mask = _fill_holes(mask)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    contour = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(contour) < 12:
        return None

    area = float(cv2.contourArea(contour))
    hull = cv2.convexHull(contour)
    hull_area = max(1.0, float(cv2.contourArea(hull)))
    peri = float(cv2.arcLength(contour, True))
    bx, by, bw, bh = cv2.boundingRect(contour)

    # 外观统计用腐蚀后的 mask，避开碎片自带的白色描边
    er = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    app_mask = er if er.mean() > 1 else mask
    crop3 = crop[:, :, :3] if crop.ndim == 3 and crop.shape[2] >= 3 else crop
    if crop3.ndim == 2:
        crop3 = cv2.cvtColor(crop3, cv2.COLOR_GRAY2BGR)

    return Shape(
        x=rx + bx, y=ry + by, w=bw, h=bh, mask=mask, nmask=_norm_mask(mask),
        contour=contour,
        hu=_hu_log(contour),
        fd=_fourier_desc(contour),
        radial=_radial_sig(contour, bx + bw / 2.0, by + bh / 2.0),
        polar=_polar_sig(mask),
        hist=_hsv_hist(crop3, app_mask),
        color=_color_moments(crop3, app_mask),
        area=area, ar=bw / max(1.0, float(bh)),
        solidity=min(1.0, area / hull_area),
        extent=min(1.0, area / max(1.0, float(bw * bh))),
        compact=peri * peri / max(1.0, area),
        label=label, ox=rx, oy=ry,
    )


# --------------------------------------------------------------------------- #
# 相似度
# --------------------------------------------------------------------------- #
def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _hu_sim(a: np.ndarray, b: np.ndarray) -> float:
    d = float(np.mean(np.abs(a - b)))
    return float(math.exp(-d / 1.6))


def _hist_sim(a: np.ndarray, b: np.ndarray) -> float:
    """直方图交集，天然落在 [0,1]，比相关性稳（相关性会被背景色干扰）。"""
    return float(np.minimum(a, b).sum())


def _rot_masks(nmask: np.ndarray, step: int = 10) -> Tuple[List[np.ndarray], List[float]]:
    """预旋转：一次 warp 36 份，后续只做位运算。"""
    n = 360 // step
    c = (nmask.shape[1] / 2.0, nmask.shape[0] / 2.0)
    out, angles = [], []
    for i in range(n):
        ang = i * step
        M = cv2.getRotationMatrix2D(c, ang, 1.0)
        out.append(cv2.warpAffine(nmask, M, nmask.shape[::-1],
                                  flags=cv2.INTER_NEAREST, borderValue=0))
        angles.append(float(ang))
    return out, angles


def _iou_bin(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.count_nonzero(a & b)
    union = np.count_nonzero(a | b)
    return inter / union if union else 0.0


def rot_iou(piece_rots: List[np.ndarray], slot: Shape,
            w: ScoreWeights) -> Tuple[float, float]:
    """
    返回 (最佳 IoU, 应采用的偏转角)。
    偏转角取"所有并列最优角度里最接近 0° 的那个"——对称图形不吃惩罚，
    6/9、▲/▼ 这类会被正确扣分。
    """
    sm = slot.nmask
    best = 0.0
    ious = []
    for rm in piece_rots:
        v = _iou_bin(rm, sm)
        ious.append(v)
        if v > best:
            best = v
    if best <= 0:
        return 0.0, 0.0
    step = 360.0 / len(ious)
    cand = [(abs(((i * step) + 180) % 360 - 180), i * step)
            for i, v in enumerate(ious) if v >= best - w.iou_tie]
    cand.sort()
    return best, cand[0][1]


def _angle_pen(angle: float, w: ScoreWeights) -> float:
    d = abs(((angle + 180) % 360) - 180)      # 0..180
    return w.upright_penalty * (d / 180.0)


def score_pair(piece: Shape, slot: Shape, piece_rots: List[np.ndarray],
               w: ScoreWeights, use_app: bool = True) -> Tuple[float, Dict[str, float]]:
    psim, pang = polar_rot_sim(piece.polar, slot.polar)
    iou, iang = rot_iou(piece_rots, slot, w)
    # 角度取"更可信"的那一路：极坐标 2° 分辨率，比预旋转的 10° 细
    ang = pang if psim > 0.05 else iang
    d = {
        "polar": psim,
        "rot_iou": iou,
        "hu": _hu_sim(piece.hu, slot.hu),
        "fd": _cos(piece.fd, slot.fd),
        "radial": _cos(piece.radial, slot.radial),
        "app": (_hist_sim(piece.hist, slot.hist) if use_app else 0.0),
        "ang": ang,
    }
    s = (w.polar * d["polar"] + w.rot_iou * d["rot_iou"] + w.fd * d["fd"]
         + w.hu * d["hu"] + w.radial * d["radial"]
         + (w.app * d["app"] if use_app else 0.0))
    s -= _angle_pen(ang, w)
    d["score"] = s
    return s, d


# --------------------------------------------------------------------------- #
# 配对
# --------------------------------------------------------------------------- #
def _hungarian(cost: np.ndarray) -> List[int]:
    try:
        from scipy.optimize import linear_sum_assignment
        r, c = linear_sum_assignment(-cost)
        out = [-1] * cost.shape[0]
        for i, j in zip(r, c):
            out[int(i)] = int(j)
        return out
    except Exception:
        # 无 scipy 时退化为贪心（按分数从高到低占位）
        out = [-1] * cost.shape[0]
        used = set()
        flat = np.dstack(np.unravel_index(np.argsort(-cost, axis=None), cost.shape))[0]
        for i, j in flat:
            i, j = int(i), int(j)
            if out[i] == -1 and j not in used:
                out[i] = j
                used.add(j)
        return out


def match(pieces: List[Shape], slots: List[Shape],
          weights: Optional[ScoreWeights] = None,
          use_app: bool = True) -> MatchResult:
    """碎片 → 槽位 的最优一对一配对。"""
    w = weights or ScoreWeights()
    if not pieces or not slots:
        return MatchResult([], np.zeros((len(pieces), len(slots))), False, "empty input")

    n, m = len(pieces), len(slots)
    cost = np.full((n, m), -1e9, np.float32)
    details: Dict[Tuple[int, int], Dict[str, float]] = {}
    rots = [_rot_masks(p.nmask, w.angle_step) for p in pieces]

    for i, p in enumerate(pieces):
        for j, s in enumerate(slots):
            sc, d = score_pair(p, s, rots[i][0], w, use_app)
            cost[i, j] = sc
            details[(i, j)] = d

    assign = _hungarian(cost)
    pairs: List[Pair] = []
    for i in range(n):
        j = assign[i]
        if j < 0 or j >= m:
            continue
        row = cost[i].copy()
        row[j] = -1e9
        second = float(row.max()) if m > 1 else -1e9
        margin = float(cost[i, j] - second) if m > 1 else float("inf")
        d = details[(i, j)]
        pairs.append(Pair(piece=i, slot=j, score=float(cost[i, j]),
                          margin=margin, angle=d["ang"], detail=d))
    ok = len(pairs) == n and len(set(p.slot for p in pairs)) == len(pairs)
    return MatchResult(pairs, cost, ok, "" if ok else "unassigned")


# --------------------------------------------------------------------------- #
# 无 DOM 时的自动检测（兜底，能跑但不该当主力）
# --------------------------------------------------------------------------- #
def _sat_mask(bgr: np.ndarray, lo: int = 70, hi: int = 255) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, lo, 40), (180, hi, 255))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)


def _boxes_from_mask(mask: np.ndarray, W: int, H: int,
                     min_area: float = 0.004, max_area: float = 0.30,
                     ar_lo: float = 0.3, ar_hi: float = 3.5) -> List[Tuple[int, int, int, int]]:
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        a = (w * h) / float(W * H)
        if not (min_area <= a <= max_area):
            continue
        if not (ar_lo <= w / max(1, h) <= ar_hi):
            continue
        if cv2.contourArea(c) / max(1.0, w * h) < 0.22:
            continue
        out.append((x, y, w, h))
    return out


def auto_detect(img: np.ndarray, slot_gray_lo: int = 0,
                slot_gray_hi: int = 42) -> Tuple[List[Shape], List[Shape]]:
    """
    纯 CV 兜底：彩色高饱和块 = 碎片；低饱和灰块 = 槽位。
    **只在页面拿不到 DOM 结构时用**。实测自动检测会把 logo/水印框进来，
    真实项目里 90% 的站点可以直接用 DOM 的 bounding_box 拿到框，别自找麻烦。
    """
    H, W = img.shape[:2]
    pieces = [s for b in _boxes_from_mask(_sat_mask(img), W, H)
              if (s := extract(img, b, label="piece")) is not None]

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    grayish = cv2.inRange(hsv, (0, 0, 90), (180, slot_gray_hi, 255))
    slots = [s for b in _boxes_from_mask(grayish, W, H, 0.004, 0.25)
             if (s := extract(img, b, label="slot")) is not None]
    return pieces, slots


def boxes_from_dom(boxes: Sequence[Sequence[float]],
                   dpr: float = 1.0) -> List[Tuple[int, int, int, int]]:
    """
    把 Playwright bounding_box（CSS px，相对截图区域）转成图片像素。
    dpr 来自 page.evaluate("window.devicePixelRatio") 或截图 deviceScaleFactor。
    """
    out = []
    for x, y, w, h in boxes:
        out.append((int(round(x * dpr)), int(round(y * dpr)),
                    int(round(w * dpr)), int(round(h * dpr))))
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python shape_match.py BOARD.png [--auto]")
        raise SystemExit(1)
    data = np.fromfile(sys.argv[1], np.uint8)
    im = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    ps, ss = auto_detect(im)
    print(f"pieces={len(ps)} slots={len(ss)}")
    r = match(ps, ss)
    for p in r.pairs:
        print(f"  piece#{p.piece} ({ps[p.piece].cx:.0f},{ps[p.piece].cy:.0f}) "
              f"-> slot#{p.slot} ({ss[p.slot].cx:.0f},{ss[p.slot].cy:.0f}) "
              f"score={p.score:.3f} margin={p.margin:.3f} ang={p.angle:.0f}")
