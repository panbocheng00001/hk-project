"""防守方：可配置出题、抗识别评测、轨迹风控、验题服务。"""
from .challenge import Challenge, ChallengeConfig, generate
from .curve import CurveChallenge, CurveConfig
from .robustness import CURVE_PRESETS, PRESETS, RobustnessReport, evaluate, run_presets
from .trajectory import RiskAssessment, TrajectoryFeatures, extract_features, score_risk

__all__ = [
    "Challenge", "ChallengeConfig", "generate",
    "CurveChallenge", "CurveConfig",
    "PRESETS", "CURVE_PRESETS", "RobustnessReport", "evaluate", "run_presets",
    "RiskAssessment", "TrajectoryFeatures", "extract_features", "score_risk",
]
