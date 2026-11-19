"""
轨迹风控：从一条拖动轨迹里提取行为特征并打风险分
==================================================

验证码被判失效，很多时候不是"缺口找错了"，而是"拖动不像人"。防守方的行为层就做一件事：
拿到前端采集的 (x, y, t) 序列，判断它更像真人还是脚本。

这个模块是纯特征 + 规则打分，不依赖任何机器学习库就能跑；如果装了 scikit-learn，
可以用 `train_logistic` 在你自己的真人/脚本样本上训练一个更准的线性模型。

诚实的边界说明：
  - 这里能可靠识别的是"低成本脚本"：匀速直线、时间戳等间隔、坐标整数化、无过冲、
    无停顿。这类占真实攻击的绝大多数。
  - 它也能识别本仓库 `solver.track.human_track` 这类"数学拟人"轨迹的统计尾巴
    （速度曲线过于光滑、坐标固定小数位）。
  - 它**不能**仅凭一条轨迹就证明对面是真人——真正的风控还要叠加指纹、频率、
    IP 等维度。本模块只提供其中"轨迹"这一路信号。

坐标约定：points 是 [(x, y, t_ms), ...]，t 单调不减，单位毫秒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float, float]


@dataclass
class TrajectoryFeatures:
    n_points: int
    duration_ms: float
    path_len: float
    net_dx: float
    straightness: float          # |位移| / 路径长度，1=完全直线
    v_peak: float                # 峰值水平速度 (px/ms)
    v_mean: float
    v_std: float
    a_peak: float                # 峰值加速度
    jerk_rms: float              # 加加速度均方根（人手抖，脚本平滑或为 0）
    v_roughness: float           # 速度曲线偏离其平滑版本的程度 / 峰值速度
    pause_ratio: float           # 速度近 0 的采样占比
    overshoot_px: float          # 超过最终落点的最大过冲量
    reversals: int               # x 方向反向次数
    y_span: float                # y 的极差
    dt_cv: float                 # 时间间隔变异系数（等间隔→0，人手→偏大）
    dt_min_ms: float
    coord_quant: float           # 坐标"规整"比例（整数或固定小数位）
    decimals: int                # 坐标小数位数（脚本常固定 0/2 位）

    def vector(self) -> List[float]:
        return [self.straightness, self.v_std, self.jerk_rms, self.v_roughness,
                self.pause_ratio, self.overshoot_px, float(self.reversals),
                self.y_span, self.dt_cv, self.coord_quant]


def _diff(a: Sequence[float]) -> List[float]:
    return [a[i + 1] - a[i] for i in range(len(a) - 1)]


def _decimals_used(vals: Sequence[float]) -> int:
    """估计坐标普遍用了几位小数（脚本常是固定 0 或 2 位）。"""
    best = 0
    for d in range(4):
        scale = 10 ** d
        if all(abs(v * scale - round(v * scale)) < 1e-6 for v in vals):
            best = d
            break
    else:
        best = 4
    return best


def extract_features(points: Sequence[Point]) -> TrajectoryFeatures:
    """从一条轨迹抽取行为特征。点数过少时返回一个"高度可疑"的退化特征。"""
    pts = [(float(x), float(y), float(t)) for x, y, t in points]
    pts.sort(key=lambda p: p[2])
    n = len(pts)
    if n < 3:
        return TrajectoryFeatures(n, 0, 0, 0, 1.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                  0, 0, 1.0, 0)

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ts = [p[2] for p in pts]

    duration = ts[-1] - ts[0]
    dxs, dys, dts = _diff(xs), _diff(ys), _diff(ts)
    path_len = sum(math.hypot(dx, dy) for dx, dy in zip(dxs, dys))
    net_dx = xs[-1] - xs[0]
    straightness = abs(net_dx) / path_len if path_len > 1e-6 else 1.0

    # 速度 / 加速度 / 加加速度（都基于水平分量，拖动主方向）
    vel = [dx / dt if dt > 1e-6 else 0.0 for dx, dt in zip(dxs, dts)]
    v_abs = [abs(v) for v in vel]
    v_peak = max(v_abs) if v_abs else 0.0
    v_mean = sum(v_abs) / len(v_abs) if v_abs else 0.0
    v_std = _std(v_abs)
    acc = [(vel[i + 1] - vel[i]) / dts[i + 1] if dts[i + 1] > 1e-6 else 0.0
           for i in range(len(vel) - 1)]
    a_peak = max((abs(a) for a in acc), default=0.0)
    jerk = _diff(acc)
    jerk_rms = math.sqrt(sum(j * j for j in jerk) / len(jerk)) if jerk else 0.0

    # 真实输入管线（鼠标采样 + 手部微颤）让速度曲线天然"毛糙"；
    # 解析函数生成的轨迹无论时间轴怎么随机，速度曲线都近乎光滑。
    if len(vel) >= 5 and v_peak > 0:
        smooth = [(vel[i - 1] + vel[i] + vel[i + 1]) / 3.0
                  for i in range(1, len(vel) - 1)]
        resid = [vel[i] - smooth[i - 1] for i in range(1, len(vel) - 1)]
        v_roughness = math.sqrt(sum(r * r for r in resid) / len(resid)) / v_peak
    else:
        v_roughness = 0.0

    v_cut = max(0.02, 0.05 * v_peak)
    pause_ratio = sum(1 for v in v_abs if v < v_cut) / len(v_abs)

    peak_x = max(xs) if net_dx >= 0 else min(xs)
    overshoot = max(0.0, (peak_x - xs[-1]) if net_dx >= 0 else (xs[-1] - peak_x))

    reversals = sum(1 for i in range(len(dxs) - 1)
                    if dxs[i] * dxs[i + 1] < 0 and abs(dxs[i + 1]) > 0.5)
    y_span = max(ys) - min(ys)

    dt_cv = (_std(dts) / (sum(dts) / len(dts))) if dts and sum(dts) > 0 else 0.0
    dt_min = min(dts) if dts else 0.0

    coords = xs + ys
    quant = sum(1 for c in coords if abs(c - round(c)) < 1e-6) / len(coords)
    decimals = _decimals_used(coords)

    return TrajectoryFeatures(
        n_points=n, duration_ms=duration, path_len=path_len, net_dx=net_dx,
        straightness=straightness, v_peak=v_peak, v_mean=v_mean, v_std=v_std,
        a_peak=a_peak, jerk_rms=jerk_rms, v_roughness=v_roughness,
        pause_ratio=pause_ratio,
        overshoot_px=overshoot, reversals=reversals, y_span=y_span,
        dt_cv=dt_cv, dt_min_ms=dt_min, coord_quant=quant, decimals=decimals,
    )


def _std(a: Sequence[float]) -> float:
    if len(a) < 2:
        return 0.0
    m = sum(a) / len(a)
    return math.sqrt(sum((v - m) ** 2 for v in a) / len(a))


# 速度粗糙度下限：默认值是保守经验值，务必用真人录制样本校准（见 bench_detector.py）
ROUGHNESS_MIN = 0.02


@dataclass
class RiskAssessment:
    risk: float                  # 0~1，越高越像脚本
    is_bot: bool                 # risk >= 阈值
    reasons: List[str]
    features: TrajectoryFeatures


def score_risk(points: Sequence[Point], threshold: float = 0.5) -> RiskAssessment:
    """规则打分：累加各项"机器味"证据，归一到 0~1。

    每条规则都对应一种真实可观测的异常，命中就加权。权重是经验值，
    在你自己的样本上可用 train_logistic 校准得更准。
    """
    f = extract_features(points)
    flags: List[Tuple[str, float]] = []

    if f.n_points < 8:
        flags.append(("采样点过少（真人拖动通常 >15 点）", 0.35))
    if f.duration_ms < 180:
        flags.append(("拖动太快（<180ms，几乎不可能是真人）", 0.4))
    if f.straightness > 0.999:
        flags.append(("完全直线，无任何垂直抖动", 0.3))
    if f.y_span < 0.5:
        flags.append(("y 轴零抖动（真人手一定有漂移）", 0.25))
    if f.dt_cv < 0.05:
        flags.append(("时间间隔等距（典型 for 循环固定步长）", 0.35))
    if f.v_std < 1e-3 and f.v_mean > 0:
        flags.append(("速度恒定，无加减速曲线", 0.3))
    if f.jerk_rms < 1e-6:
        flags.append(("加加速度恒为 0，轨迹过度平滑", 0.2))
    elif f.n_points >= 8 and f.v_roughness < ROUGHNESS_MIN:
        flags.append(("速度曲线过于光滑（解析函数生成，缺少采样噪声与手颤）", 0.4))
    if f.overshoot_px <= 0 and f.reversals == 0:
        flags.append(("无过冲、无回拉（真人末段常微调）", 0.15))
    if f.decimals == 0 and f.coord_quant > 0.95:
        flags.append(("坐标全为整数（未经真实输入管线）", 0.2))
    elif f.decimals in (1, 2, 3) and f.coord_quant < 0.5:
        flags.append((f"坐标固定 {f.decimals} 位小数（数学生成后取整的特征）", 0.15))
    if f.pause_ratio < 1e-6 and f.duration_ms > 0:
        flags.append(("全程无停顿（真人常有极短迟疑）", 0.1))
    if f.dt_min_ms > 0 and abs(f.dt_min_ms - round(f.dt_min_ms)) < 1e-6 \
            and f.dt_cv < 0.2:
        flags.append(("时间戳整数毫秒且近似等距", 0.1))

    risk = min(1.0, sum(w for _, w in flags))
    return RiskAssessment(risk=risk, is_bot=risk >= threshold,
                          reasons=[r for r, _ in flags], features=f)


# --------------------------------------------------------------------------- #
# 负样本：用于自测"脚本一定被拦"。真人正样本必须来自真实录制（service 的 /record）
# --------------------------------------------------------------------------- #
def linear_bot_track(distance: float, steps: int = 30,
                     dt_ms: float = 16.0) -> List[Point]:
    """最朴素的脚本轨迹：匀速、等间隔、直线、整数坐标。作为"负样本"下限。"""
    return [(round(distance * i / steps), 0.0, i * dt_ms)
            for i in range(steps + 1)]


def features_table(points: Sequence[Point]) -> Dict[str, float]:
    return asdict(extract_features(points))


# --------------------------------------------------------------------------- #
# 可选：在真人/脚本样本上训练线性模型（需要 scikit-learn）
# --------------------------------------------------------------------------- #
def train_logistic(samples: List[Tuple[Sequence[Point], int]]):
    """samples: [(points, label), ...]，label 1=脚本 0=真人。返回 (model, scaler)。"""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError as e:
        raise RuntimeError("train_logistic 需要 numpy 和 scikit-learn") from e
    X = np.array([extract_features(p).vector() for p, _ in samples])
    y = np.array([lbl for _, lbl in samples])
    scaler = StandardScaler().fit(X)
    model = LogisticRegression(max_iter=1000).fit(scaler.transform(X), y)
    return model, scaler


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from solver.track import human_track

    cases = {"linear_bot": linear_bot_track(240)}
    for s in range(3):
        cases[f"human_track(seed={s})"] = human_track(240, overshoot_px=4, seed=s)
    for name, pts in cases.items():
        a = score_risk(pts)
        print(f"{name:<22s} risk={a.risk:.2f} is_bot={a.is_bot}")
        for r in a.reasons:
            print(f"    - {r}")
