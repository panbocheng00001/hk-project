# -*- coding: utf-8 -*-
"""captcha_lab_bridge.py —— 把 captcha-lab 识别内核接进 booking-lab
=====================================================================
职责（只做"胶水"，判据与轨迹算法全部复用 captcha-lab，不在这里重写）：

    1) solve_canvas()  画布位图 → {ok, dx, slider_dx, current_x, target_x, pairs, level}
                       —— 复用 solver.overlap（与 captcha_fixture/analyze.js 同一套判据）
    2) make_track()    slider_dx → [(x, y, t), ...] 拟人轨迹
                       —— 复用 solver.track.generate_track（env_consistency 档 + 访问频率控制自检）

导入策略（两级回退）：
    · 先按"已 pip 安装的 captcha-lab"直接 import solver / solver.track；
    · 失败则把本机的 captcha-lab 源码目录塞进 sys.path 再 import
      （目录取 env CAPTCHA_LAB_PATH，缺省为 ../captcha-lab 兄弟目录）；
    · 两级都失败时抛 BridgeUnavailable，交由调用方（drag_puzzle.py）回退到旧实现。

约定：本桥**绝不静默吞错**。识别/轨迹内核不可用时只会抛 BridgeUnavailable，
      由上层决定"打日志 + 回退"，不在这里偷偷返回 legacy 结果。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "BridgeUnavailable",
    "solve_canvas",
    "make_track",
    "analyze_track",
    "captcha_lab_path",
    "is_available",
]

# 轨迹点：x、y、t（毫秒，相对按下时刻），与 solver.track.Point 对齐
Point = Tuple[float, float, float]
ImageLike = Union[bytes, bytearray, str]


class BridgeUnavailable(RuntimeError):
    """captcha-lab 识别内核无法导入时抛出；调用方应据此回退到 drag_puzzle.py 原实现。"""


# --------------------------------------------------------------------------- #
# 内核定位与惰性导入
# --------------------------------------------------------------------------- #
def captcha_lab_path() -> Path:
    """返回 captcha-lab 源码目录。

    优先取环境变量 ``CAPTCHA_LAB_PATH``；缺省回退到与 booking-lab 同级的
    ``../captcha-lab`` 兄弟目录（即 ``<hk-project>/captcha-lab``）。
    """
    env = os.environ.get("CAPTCHA_LAB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "captcha-lab").resolve()


# 缓存已加载的内核函数，避免每次调用都走 import 机制
_KERNEL: Optional[Dict[str, Any]] = None


def _load_kernel() -> Dict[str, Any]:
    """惰性导入 captcha-lab 内核，返回所需函数表；失败抛 BridgeUnavailable。"""
    global _KERNEL
    if _KERNEL is not None:
        return _KERNEL

    last_err: Optional[BaseException] = None

    # ① 先试"已安装的 captcha-lab"（import solver 直接可用）
    for attempt in ("installed", "source"):
        if attempt == "source":
            # ② 回退：把源码目录塞进 sys.path 再试
            src = captcha_lab_path()
            if not src.is_dir():
                last_err = FileNotFoundError(f"captcha-lab 目录不存在：{src}")
                continue
            if str(src) not in sys.path:
                sys.path.insert(0, str(src))
        try:
            from solver.overlap import solve_canvas as _overlap_solve  # type: ignore
            from solver.overlap import to_slider_dx as _to_slider_dx  # type: ignore
            from solver.track import analyze as _analyze_track  # type: ignore
            from solver.track import generate_track as _generate_track  # type: ignore

            _KERNEL = {
                "solve_canvas": _overlap_solve,
                "to_slider_dx": _to_slider_dx,
                "generate_track": _generate_track,
                "analyze_track": _analyze_track,
                "source": attempt,
            }
            return _KERNEL
        except Exception as e:  # noqa: BLE001 - 两级都失败才放弃
            last_err = e

    raise BridgeUnavailable(
        f"无法导入 captcha-lab 识别内核（已尝试 pip 包与源码目录 {captcha_lab_path()}）：{last_err}"
    ) from last_err


def is_available() -> bool:
    """探测内核是否可用；不抛异常，仅返回布尔值（供日志/诊断用）。"""
    try:
        _load_kernel()
        return True
    except BridgeUnavailable:
        return False


# --------------------------------------------------------------------------- #
# A-1 画布识别：位图 → dx / slider_dx
# --------------------------------------------------------------------------- #
def solve_canvas(image: ImageLike,
                 track_w: float = 400.0,
                 handle_w: float = 34.0,
                 bmp_w: Optional[float] = None) -> Dict[str, Any]:
    """识别"几何重合滑块"画布，算出水平位移。

    参数
        image    : 画布位图，可为 PNG ``bytes``、``data:image/png;base64,...`` data-url，
                   或 base64 字符串、文件路径（全部交给 solver.overlap 统一解码）。
        track_w  : 滑轨宽度（CSS px），华为 WAF 实测 400。
        handle_w : 手柄宽度（CSS px），实测 34。
        bmp_w    : 画布位图宽度；None 时由 solver 取解码后的真实宽度。

    返回
        ``{ok, dx, slider_dx, current_x, target_x, pairs, level}``。
        ``dx`` 为画布像素差，``slider_dx = dx * max(10, track_w-handle_w) / bmp_w``，
        即前端实际提交的滑轨像素位移。另附 ``error``（失败原因）与 ``elapsed_ms``。

    内核不可用时抛 BridgeUnavailable，不做任何回退。
    """
    kernel = _load_kernel()
    t0 = time.perf_counter()
    raw: Dict[str, Any] = kernel["solve_canvas"](
        image, bmp_w=bmp_w, track_w=float(track_w), handle_w=float(handle_w),
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": bool(raw.get("ok")),
        "dx": raw.get("dx", 0.0),
        "slider_dx": raw.get("slider_dx", 0.0),
        "current_x": raw.get("current_x", 0.0),
        "target_x": raw.get("target_x", 0.0),
        "pairs": raw.get("pairs", 0),
        "level": raw.get("level", -1),
        "bmp_w": raw.get("bmp_w"),
        "error": raw.get("error", ""),
        "elapsed_ms": round(elapsed_ms, 2),
    }


# --------------------------------------------------------------------------- #
# A-2 轨迹生成：slider_dx → 拟人 (x, y, t)
# --------------------------------------------------------------------------- #
def make_track(slider_dx: float,
               profile: str = "env_consistency",
               seed: Optional[int] = None,
               risk_threshold: float = 0.5) -> List[Point]:
    """生成一条拖到 ``slider_dx`` 的拟人轨迹（复用 solver.track.generate_track）。

    参数
        slider_dx      : 目标水平位移（滑轨像素），即 solve_canvas 的 slider_dx。
        profile        : 轨迹档案，默认 ``env_consistency``（最像人：噪声足、停顿多、全精度浮点）。
        seed           : 随机种子；None 为每次不同。
        risk_threshold : 访问频率控制自检阈值，低于它才算合格（默认 0.5）。

    返回
        ``[(x, y, t), ...]``，``t`` 为毫秒且单调递增，**末点 x 精确等于 slider_dx**，
        这样"差 1px 永远过不去"的问题不会发生。内核自带访问频率控制自检（audit_risk），
        不达标会重采样，最终返回风险最低的一条。

    内核不可用时抛 BridgeUnavailable。
    """
    kernel = _load_kernel()
    pts = kernel["generate_track"](
        float(slider_dx),
        profile=profile,
        seed=seed,
        audit_risk=True,
        risk_threshold=float(risk_threshold),
    )
    return [(float(x), float(y), float(t)) for x, y, t in pts]


def analyze_track(points: List[Point]) -> Dict[str, float]:
    """轨迹体检（点数 / 时长 / 速度 / 风险分），复用 solver.track.analyze。

    主要供测试与排障：用它拿到 ``risk`` 验证 make_track 的产物确实 < 0.5。
    内核不可用时抛 BridgeUnavailable。
    """
    kernel = _load_kernel()
    return dict(kernel["analyze_track"](points))


if __name__ == "__main__":
    # 自测：python captcha_lab_bridge.py <画布png路径>
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(f"captcha-lab 可用: {is_available()}  源目录: {captcha_lab_path()}")
        raise SystemExit(0)
    img = Path(sys.argv[1]).read_bytes()
    res = solve_canvas(img, track_w=400, handle_w=34)
    print("solve_canvas:", res)
    if res["ok"]:
        tr = make_track(res["slider_dx"], seed=1)
        feats = analyze_track(tr)
        print(f"make_track: {len(tr)} 点, 末点={tr[-1]}, 风险={feats.get('risk')}")
