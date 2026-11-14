"""
抗识别评测：用现成的自动识别器当"红队"，量化每种出题配置有多难被机器解开
=========================================================================

出题方真正关心的不是"我的验证码看起来多复杂"，而是：
    **在给定容差下，一个现成的开源级识别器一次能蒙对的比例是多少？**

攻击基线（都代表"公开可得、无需针对你单独训练"的水平，连它都能高比例解开，
说明这个配置几乎没有抗识别能力）：
  直线拼图  `solver.gap.locate`             多策略融合缺口定位
  曲线重合  `solver.curve_match.contour_align`  只用客户端可见信息的轮廓对齐扫描

核心指标（越低越好，对出题方而言）：
  attack_top1   一次提交即通过的比例         <- 最关键，等于自动化的"免费通过率"
  attack_top3   允许重试 3 次的累计通过比例   <- 反映"不限重试"时的真实风险
  resistance    = 1 - attack_top1             <- 抗识别得分，越高越好

注意：抗识别和人类可用性是一对矛盾。描边/阴影调到 0、假缺口加满，机器难了，
人也看不清了。这里只量化机器侧，人类可用性需要真人测试（见 README）。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Union

from . import curve
from .challenge import Challenge, ChallengeConfig, generate
from .curve import CurveChallenge, CurveConfig

# 攻击函数签名：给一道题，返回按置信度排序的候选答案列表
#   直线拼图：缺口中心 x；曲线重合：滑块值 s ∈ [0, 1]
Attacker = Callable[[Any], List[float]]
AnyConfig = Union[ChallengeConfig, CurveConfig]


def gap_locate_attacker(ch: Challenge, topk: int = 3) -> List[float]:
    """直线拼图默认红队：仓库自带的多策略缺口定位，背景图 + 滑块图一起喂。"""
    from solver import gap
    cands = gap.locate(ch.bg, piece=ch.piece, topk=topk)
    return [c.cx for c in cands]


def contour_align_attacker(ch: CurveChallenge, topk: int = 3) -> List[float]:
    """曲线重合默认红队：只用客户端可见的信息做轮廓对齐扫描。"""
    from solver.curve_match import contour_align
    return contour_align(ch.bg, ch.piece, ch.path, ch.angle0,
                         ch.config.rotate_deg, topk=topk)


def _backend(cfg: AnyConfig):
    if isinstance(cfg, CurveConfig):
        return curve.generate, contour_align_attacker
    return generate, gap_locate_attacker


@dataclass
class RobustnessReport:
    name: str
    samples: int
    attack_top1: float
    attack_top3: float
    ms_per_sample: float
    config: dict

    @property
    def resistance(self) -> float:
        return 1.0 - self.attack_top1

    def row(self) -> str:
        return (f"{self.name:<22s} top1={self.attack_top1:6.1%}  "
                f"top3={self.attack_top3:6.1%}  "
                f"resist={self.resistance:6.1%}  "
                f"({self.ms_per_sample:.0f} ms)")


def evaluate(cfg: AnyConfig, samples: int = 100,
             attacker: Optional[Attacker] = None, name: str = "",
             seed0: int = 0) -> RobustnessReport:
    """对一个配置生成 samples 道题，用 attacker 攻击，统计通过率。"""
    gen, default_attacker = _backend(cfg)
    attacker = attacker or default_attacker
    hit1 = hit3 = 0
    t0 = time.perf_counter()
    for i in range(samples):
        ch = gen(cfg, seed=seed0 + i)
        cxs = attacker(ch)
        if not cxs:
            continue
        if ch.is_correct(cxs[0]):
            hit1 += 1
        if any(ch.is_correct(x) for x in cxs[:3]):
            hit3 += 1
    elapsed = time.perf_counter() - t0
    n = max(1, samples)
    return RobustnessReport(
        name=name or type(cfg).__name__,
        samples=samples,
        attack_top1=hit1 / n,
        attack_top3=hit3 / n,
        ms_per_sample=elapsed / n * 1000,
        config=asdict(cfg),
    )


# 一组有代表性的预设：从"几乎不设防"到"强对抗"，便于横向比较每个旋钮的收益
PRESETS: Dict[str, ChallengeConfig] = {
    "baseline_square": ChallengeConfig(notch_shape="square", num_decoys=0,
                                       texture="noise"),
    "puzzle": ChallengeConfig(notch_shape="puzzle", num_decoys=0,
                              texture="noise"),
    "puzzle+decoy1": ChallengeConfig(notch_shape="puzzle", num_decoys=1,
                                     texture="noise"),
    "puzzle+decoy2": ChallengeConfig(notch_shape="puzzle", num_decoys=2,
                                     texture="noise"),
    "faint_edges": ChallengeConfig(notch_shape="puzzle", num_decoys=1,
                                   texture="noise", edge_strength=0.15,
                                   shadow_strength=0.15, dim_factor=0.9),
    "adversarial_tex": ChallengeConfig(notch_shape="puzzle", num_decoys=1,
                                       texture="adversarial"),
    "hardened": ChallengeConfig(notch_shape="puzzle", num_decoys=2,
                                texture="adversarial", edge_strength=0.2,
                                shadow_strength=0.2, dim_factor=0.88,
                                tolerance_px=4.0),
}


CURVE_PRESETS: Dict[str, CurveConfig] = {
    "curve_basic": CurveConfig(num_path_decoys=0, num_free_decoys=3),
    "curve_free_decoys6": CurveConfig(num_path_decoys=0, num_free_decoys=6),
    "curve_path_decoys2": CurveConfig(num_path_decoys=2),
    "curve_same_kind2": CurveConfig(num_path_decoys=2, same_kind_decoys=True),
    "curve_rotate": CurveConfig(num_path_decoys=2, same_kind_decoys=True,
                                rotate_deg=180),
    "curve_near_miss": CurveConfig(num_path_decoys=3, same_kind_decoys=True,
                                   decoy_angle=(25.0, 40.0)),
    "curve_faint_clutter": CurveConfig(num_path_decoys=2, same_kind_decoys=True,
                                       contrast=0.45, num_clutter=6,
                                       noise_sigma=8),
    "curve_hardened": CurveConfig(num_path_decoys=3, same_kind_decoys=True,
                                  decoy_angle=(25.0, 40.0), rotate_deg=180,
                                  contrast=0.45, num_clutter=6,
                                  noise_sigma=8, tolerance_px=6),
}


def run_presets(samples: int = 100, attacker: Optional[Attacker] = None,
                presets: Optional[Dict[str, AnyConfig]] = None
                ) -> List[RobustnessReport]:
    presets = PRESETS if presets is None else presets
    return [evaluate(cfg, samples, attacker, name=name)
            for name, cfg in presets.items()]
