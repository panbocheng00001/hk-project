"""
拟人轨迹生成器
==============

把"拖动距离（CSS px）"变成一条可直接提交的 `(x, y, t)` 序列。

三个层次，从下到上：

1. **运动学**：最小加加速度（minimum-jerk）位移曲线 `10u³-15u⁴+6u⁵`，
   这是人手点到点运动的经典模型，天然是"慢起—快中—慢收"。
2. **人类噪声**：逐点手颤、低频 y 漂移、随机停顿、末段过冲回拉。
   没有这一层，速度曲线会过于光滑，规则型风控一眼就能认出来。
3. **产物格式**：`to_payload()` 负责把点序列转成各家前端实际提交的字段
   （裸数组 / 整数化 / 带 passtime 的对象等）。

`generate_track(..., audit_risk=True)` 会调用 `defense.trajectory.score_risk`
做自检：不达标的轨迹重新采样，最多 `max_tries` 次。这样"生成的轨迹能不能过
行为风控"就变成一个可度量、可回归的指标，而不是靠感觉。

保持向后兼容：`human_track()` 是旧接口，签名与行为不变。
"""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

Point = Tuple[float, float, float]  # x, y, t_ms（相对按下时刻）


# --------------------------------------------------------------------------- #
# 档案
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TrackProfile:
    """一类输入设备的轨迹特征。数值来自公开的人机交互研究区间，可按站点再调。"""

    name: str
    points: Tuple[int, int] = (45, 72)      # 采样点数量区间
    duration_base: float = 620.0            # 基础耗时 ms
    duration_per_px: float = 2.9            # 每像素额外耗时
    duration_jitter: float = 90.0           # 总耗时抖动
    overshoot: Tuple[float, float] = (2.5, 6.0)
    y_jitter: float = 1.2                   # y 轴抖动幅度（px）
    tremor: float = 0.75                    # 逐点手颤标准差（px）
    pause_prob: float = 0.25                # 相邻点之间插入停顿的概率
    pause_ms: Tuple[float, float] = (18.0, 70.0)
    decimals: Optional[int] = None          # None = 保留全精度浮点
    dt_sigma: float = 0.5                   # 采样间隔对数正态 sigma
    max_duration: float = 1800.0
    min_duration: float = 420.0

    def describe(self) -> Dict[str, Union[str, float, int, None]]:
        return asdict(self)


PROFILES: Dict[str, TrackProfile] = {
    # 桌面鼠标：采样密、手颤明显、常有过冲后回拉
    "mouse": TrackProfile(name="mouse"),
    # 触屏：手指接触面积大、采样稀一些、y 漂移更大
    "touch": TrackProfile(
        name="touch", points=(28, 46), duration_base=520.0, duration_per_px=2.4,
        overshoot=(1.5, 4.0), y_jitter=1.8, tremor=1.05, pause_prob=0.18,
        dt_sigma=0.62,
    ),
    # 手快的人：点少、时长短，仍保留噪声
    "fast": TrackProfile(
        name="fast", points=(16, 28), duration_base=280.0, duration_per_px=1.6,
        overshoot=(0.0, 2.0), y_jitter=1.0, tremor=0.45, pause_prob=0.08,
        dt_sigma=0.55, min_duration=220.0,
    ),
    # 行为风控自检用的"最像人"档案：噪声更足、停顿更多、全精度浮点坐标
    "stealth": TrackProfile(
        name="stealth", points=(46, 70), duration_base=700.0, duration_per_px=3.0,
        overshoot=(3.0, 7.0), y_jitter=1.6, tremor=0.95, pause_prob=0.35,
        pause_ms=(25.0, 90.0), dt_sigma=0.55,
    ),
}


def get_profile(name: Union[str, TrackProfile]) -> TrackProfile:
    if isinstance(name, TrackProfile):
        return name
    try:
        return PROFILES[name]
    except KeyError as e:
        raise KeyError(f"未知轨迹档案 {name!r}，可选：{', '.join(PROFILES)}") from e


# --------------------------------------------------------------------------- #
# 运动学
# --------------------------------------------------------------------------- #
def _min_jerk(u: float) -> float:
    u = 0.0 if u < 0 else (1.0 if u > 1 else u)
    return u * u * u * (10.0 - 15.0 * u + 6.0 * u * u)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _risk_of(points: Sequence[Point]) -> float:
    """用仓库自带的行为风控给轨迹打分；defense 不可用时返回 0（视为无约束）。"""
    try:
        from defense.trajectory import score_risk
    except Exception:                     # pragma: no cover - defense 是可选依赖
        return 0.0
    try:
        return float(score_risk(points).risk)
    except Exception:
        return 0.0


def generate_track(distance: float,
                   profile: Union[str, TrackProfile] = "mouse",
                   y0: float = 0.0,
                   duration_ms: Optional[float] = None,
                   overshoot_px: Optional[float] = None,
                   jitter_px: Optional[float] = None,
                   seed: Optional[int] = None,
                   audit_risk: bool = False,
                   risk_threshold: float = 0.5,
                   max_tries: int = 8) -> List[Point]:
    """
    生成一条可直接提交的拖动轨迹。

    distance      : 目标位移（CSS px）
    profile       : 档案名（mouse/touch/fast/stealth）或自定义 TrackProfile
    duration_ms   : 指定总耗时；None 时按档案 + 距离自适应
    overshoot_px  : 覆盖档案的过冲量；0 表示不过冲
    jitter_px     : 覆盖档案的 y 轴抖动幅度
    audit_risk    : True 时用 defense.trajectory 自检，不达标就重采样
    max_tries     : 自检重采样上限；全部失败时返回风险最低的一条

    返回 `[(x, y, t), ...]`，t 为毫秒，单调递增，最后一点精确落在 distance。
    """
    prof = get_profile(profile)
    rng = random.Random(seed)

    best: Optional[Tuple[float, List[Point]]] = None
    tries = max(1, max_tries) if audit_risk else 1
    for attempt in range(tries):
        pts = _generate_once(
            float(distance), prof, rng, y0=y0, duration_ms=duration_ms,
            overshoot_px=overshoot_px, jitter_px=jitter_px, attempt=attempt,
        )
        if not audit_risk:
            return pts
        risk = _risk_of(pts)
        if best is None or risk < best[0]:
            best = (risk, pts)
        if risk < risk_threshold:
            return pts
    return best[1] if best else _generate_once(
        float(distance), prof, rng, y0=y0, duration_ms=duration_ms,
        overshoot_px=overshoot_px, jitter_px=jitter_px, attempt=0,
    )


def _generate_once(distance: float, prof: TrackProfile, rng: random.Random,
                   y0: float, duration_ms: Optional[float],
                   overshoot_px: Optional[float], jitter_px: Optional[float],
                   attempt: int = 0) -> List[Point]:
    if distance < 0:
        raise ValueError("distance 不能为负")

    if duration_ms is None:
        duration = prof.duration_base + prof.duration_per_px * min(distance, 420.0)
        duration += rng.uniform(-prof.duration_jitter, prof.duration_jitter)
        duration = _clamp(duration, prof.min_duration, prof.max_duration)
    else:
        duration = max(float(duration_ms), 60.0)

    ov = prof.overshoot[0] + (prof.overshoot[1] - prof.overshoot[0]) * rng.random() \
        if overshoot_px is None else max(0.0, float(overshoot_px))

    n = rng.randint(prof.points[0], prof.points[1])
    if distance < 40:                       # 短距离不需要那么多点
        n = max(12, int(n * 0.6))
    # 自检失败重采样时换一组噪声参数，避免陷入同一个局部
    tremor = prof.tremor * (1.0 + 0.25 * attempt)
    y_amp = (prof.y_jitter if jitter_px is None else float(jitter_px)) * (1.0 + 0.2 * attempt)

    # --- 时间轴：对数正态采样间隔 + 随机停顿 -------------------------------- #
    gaps = [rng.lognormvariate(0.0, prof.dt_sigma) for _ in range(n - 1)]
    # 起手停顿：按住之后先不动，真人常见
    gaps[0] = gaps[0] * 0.5 + rng.uniform(35.0, 110.0)
    for i in range(1, len(gaps)):
        if rng.random() < prof.pause_prob:
            gaps[i] += rng.uniform(*prof.pause_ms)
    scale = duration / sum(gaps) if sum(gaps) > 0 else 1.0
    gaps = [g * scale for g in gaps]

    ts: List[float] = [0.0]
    for g in gaps:
        ts.append(ts[-1] + g)

    # --- 位移曲线：过冲 + 回拉 ----------------------------------------------- #
    t_end = ts[-1]
    target = distance + ov
    phase = 0.88                            # 88% 时间到达过冲点，剩下回拉
    xs: List[float] = []
    drift_freq = rng.uniform(0.8, 1.6)
    drift_phase = rng.uniform(0.0, math.pi)
    last = 0.0
    for t in ts:
        u = t / t_end if t_end > 0 else 1.0
        if u <= phase:
            x = target * _min_jerk(u / phase)
            # 手颤：幅度随接近终点略降
            x += rng.gauss(0.0, tremor) * (1.0 - 0.55 * u)
            x = max(x, last)                # 主段基本单调，不做倒退
        else:
            v = (u - phase) / (1.0 - phase)
            x = target - ov * _min_jerk(v) + rng.gauss(0.0, tremor * 0.5) * (1.0 - v)
        last = x
        xs.append(x)

    # --- y 轴：低频漂移 + 逐点噪声 ------------------------------------------- #
    ys: List[float] = []
    for i, t in enumerate(ts):
        u = t / t_end if t_end > 0 else 1.0
        y = y0 + math.sin(math.pi * drift_freq * u + drift_phase) * y_amp * (1.0 - 0.6 * u)
        y += rng.gauss(0.0, tremor * 0.45)
        ys.append(y)

    # --- 组装并收尾 ----------------------------------------------------------- #
    pts: List[Point] = []
    for i, (x, y, t) in enumerate(zip(xs, ys, ts)):
        if prof.decimals is None:
            px, py = round(x, 4 + rng.randint(0, 2)), round(y, 4 + rng.randint(0, 2))
        else:
            px, py = round(x, prof.decimals), round(y, prof.decimals)
        pts.append((px, py, round(t, 1 + rng.randint(0, 2))))

    # 终点必须精确，否则逻辑上"差 1px 永远过不去"
    pts.append((float(distance), float(y0), round(t_end + rng.uniform(30.0, 110.0), 1)))
    # 抬手前的微小残留（真实设备在 mouseup 前还会上报 0~2 个点）
    if rng.random() < 0.5:
        pts.append((float(distance), float(y0),
                    round(pts[-1][2] + rng.uniform(8.0, 40.0), 1)))
    return pts


# --------------------------------------------------------------------------- #
# 旧接口：数学拟人轨迹，保留作为风控的负样本
# --------------------------------------------------------------------------- #
def _ease_out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def human_track(distance: float,
                y0: float = 0.0,
                duration_ms: float | None = None,
                overshoot_px: float = 0.0,
                jitter_px: float = 1.0,
                seed: int | None = None) -> List[Point]:
    """
    [兼容保留] 早期版本用的"数学拟人"轨迹：ease-out + 过冲 + 正弦抖动。

    它只查"匀速/等间隔"的粗糙度规则，所以在低成本规则下也能通过，但速度曲线过于光滑，
    会被 `defense.trajectory` 的粗糙度规则命中。新代码请用 `generate_track`。
    """
    rng = random.Random(seed)

    if duration_ms is None:
        duration_ms = 480 + distance * 3.2 + rng.uniform(-80, 120)
        duration_ms = max(420.0, min(duration_ms, 1800.0))

    n = int(max(18, min(60, duration_ms / 18 + rng.uniform(-4, 6))))

    raw = [1.0 + 0.55 * math.sin(math.pi * i / n) + rng.uniform(0.0, 0.35)
           for i in range(n)]
    scale = duration_ms / sum(raw)
    ts, acc = [], 0.0
    for w in raw:
        acc += w * scale
        ts.append(acc)

    end = distance + overshoot_px
    pts: List[Point] = []
    phase1 = 0.55
    for t in ts:
        p = t / duration_ms
        if p <= phase1:
            u = p / phase1
            x = end * _ease_out_quint(u) * (0.97 + 0.03 * u)
        else:
            u = (p - phase1) / (1 - phase1)
            if overshoot_px > 0:
                x = end - (end - distance) * _ease_in_out(u)
            else:
                wob = math.sin(u * math.pi * 2.5) * min(0.8, distance * 0.01) * (1 - u)
                x = end + wob - (end - distance) * u
        y = y0 + math.sin(p * math.pi * rng.uniform(1.2, 2.0)) * jitter_px \
            + rng.gauss(0, jitter_px * 0.45) * (1 - p)
        pts.append((round(x, 2), round(y, 2), round(t, 1)))

    pts.append((round(distance, 2), round(y0, 2), round(duration_ms + rng.uniform(40, 120), 1)))
    return pts


# --------------------------------------------------------------------------- #
# 产物格式
# --------------------------------------------------------------------------- #
def to_payload(points: Sequence[Point], fmt: str = "xyt") -> object:
    """
    把轨迹点转成前端实际提交的格式。

    fmt:
      "xyt"      -> [[x, y, t], ...]        大多数自研站点
      "xy"       -> [[x, y], ...]           不带时间戳
      "x"        -> [x, ...]                只提交水平位移
      "geetest"  -> [[x, y, t], ...] 整数    极验 v3/v4 的 track 字段
      "tencent"  -> [[x, y, t], ...] 整数    腾讯 tcaptcha 的 track 字段
      "object"   -> {"track": ..., "passtime": ..., "x": ...}
    """
    pts = [(float(x), float(y), float(t)) for x, y, t in points]
    if fmt == "xyt":
        return [[round(x, 2), round(y, 2), round(t, 1)] for x, y, t in pts]
    if fmt == "xy":
        return [[round(x, 2), round(y, 2)] for x, y, _ in pts]
    if fmt == "x":
        return [round(x, 2) for x, _, _ in pts]
    if fmt in ("geetest", "tencent"):
        return [[int(round(x)), int(round(y)), int(round(t))] for x, y, t in pts]
    if fmt == "object":
        passtime = pts[-1][2] - pts[0][2] if pts else 0.0
        return {
            "track": [[int(round(x)), int(round(y)), int(round(t))] for x, y, t in pts],
            "passtime": int(round(passtime)),
            "x": round(pts[-1][0], 2) if pts else 0.0,
            "y": round(pts[-1][1], 2) if pts else 0.0,
        }
    raise ValueError(f"未知的轨迹格式 {fmt!r}")


# --------------------------------------------------------------------------- #
# 质检
# --------------------------------------------------------------------------- #
def analyze(points: Sequence[Point]) -> Dict[str, float]:
    """轨迹体检：点数、时长、速度、粗糙度、过冲、风险分。"""
    try:
        from defense.trajectory import extract_features, features_table, score_risk
        f = extract_features(points)
        out = features_table(points)
        out["risk"] = float(score_risk(points).risk)
        out["duration_s"] = float(f.duration_ms) / 1000.0
        return out
    except Exception:
        pts = list(points)
        if len(pts) < 3:
            return {"n_points": len(pts), "risk": 1.0}
        t0, t1 = pts[0][2], pts[-1][2]
        return {
            "n_points": len(pts),
            "duration_ms": t1 - t0,
            "dx": pts[-1][0] - pts[0][0],
            "y_span": max(p[1] for p in pts) - min(p[1] for p in pts),
            "risk": 0.0,
        }


def audit(points: Sequence[Point], threshold: float = 0.5) -> Tuple[bool, float]:
    """返回 (是否通过, 风险分)。"""
    risk = _risk_of(points)
    return risk < threshold, risk


if __name__ == "__main__":
    for name in PROFILES:
        pts = generate_track(260, profile=name, seed=7, audit_risk=True)
        a = analyze(pts)
        print(f"{name:8s} points={a.get('n_points'):>3}  "
              f"duration={a.get('duration_ms', 0):>7.0f}ms  "
              f"risk={a.get('risk', 0):.3f}  "
              f"rough={a.get('v_roughness', 0):.4f}  last={pts[-1][:2]}")
