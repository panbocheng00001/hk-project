"""captcha-lab：滑块验证码识别工具包（public API）。"""
from .engine import (
    Answer, SolveError, SolveResult, SliderSolver, encode_image_b64, load_image,
)
from .gap import Candidate, debug_draw, distance_to_drag, imread_any, locate
from .overlap import OverlapResult, solve_canvas, to_slider_dx
from .profiles import SITE_PROFILES, SiteProfile, build_payload, get_site
from .track import (
    PROFILES, Point, TrackProfile, analyze, audit, generate_track, human_track,
    to_payload,
)

__version__ = "2.0.0"

__all__ = [
    # 引擎
    "SliderSolver", "SolveResult", "Answer", "SolveError",
    "load_image", "encode_image_b64",
    # 识别
    "locate", "Candidate", "distance_to_drag", "imread_any", "debug_draw",
    # 轨迹
    "generate_track", "human_track", "to_payload", "analyze", "audit",
    "PROFILES", "TrackProfile", "Point",
    # 站点
    "SITE_PROFILES", "SiteProfile", "get_site", "build_payload",
    # 几何重合滑块（华为 WAF 形态）
    "solve_canvas", "to_slider_dx", "OverlapResult",
    "__version__",
]
