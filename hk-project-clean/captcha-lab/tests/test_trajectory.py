import random

import pytest

from defense.trajectory import extract_features, linear_bot_track, score_risk
from solver.track import human_track


def _noisy_track(distance: float, seed: int):
    """测试用的"非解析"轨迹：步长、时间间隔、坐标都带独立噪声，模拟真实输入采样。"""
    rng = random.Random(seed)
    pts, x, y, t = [(0.0, 0.0, 0.0)], 0.0, 0.0, 0.0
    while x < distance:
        x += rng.uniform(0.5, 9.0)
        y += rng.gauss(0, 0.9)
        t += rng.uniform(6, 30)
        pts.append((x + rng.gauss(0, 0.35), y, t))
    pts.append((distance - rng.uniform(1, 3), y, t + rng.uniform(40, 90)))
    pts.append((distance + rng.gauss(0, 0.4), y, t + rng.uniform(120, 200)))
    return pts


@pytest.mark.parametrize("distance", [60, 150, 280])
def test_linear_bot_is_flagged(distance):
    a = score_risk(linear_bot_track(distance))
    assert a.is_bot and a.risk >= 0.9


@pytest.mark.parametrize("seed", range(10))
def test_repo_human_track_is_flagged(seed):
    a = score_risk(human_track(200, overshoot_px=seed % 6, seed=seed))
    assert a.is_bot, a.reasons


@pytest.mark.parametrize("seed", range(10))
def test_noisy_track_is_not_flagged(seed):
    a = score_risk(_noisy_track(200, seed))
    assert not a.is_bot, (a.risk, a.reasons)


def test_too_few_points_is_suspicious():
    a = score_risk([(0, 0, 0), (200, 0, 50)])
    assert a.is_bot


def test_features_basic_geometry():
    f = extract_features(linear_bot_track(100, steps=10, dt_ms=10))
    assert f.n_points == 11
    assert f.net_dx == pytest.approx(100)
    assert f.straightness == pytest.approx(1.0)
    assert f.dt_cv == pytest.approx(0.0)
    assert f.duration_ms == pytest.approx(100)


def test_overshoot_detected():
    pts = [(0, 0, 0), (50, 0.5, 20), (108, 1, 40), (104, 0.4, 70), (100, 0.2, 100)]
    assert extract_features(pts).overshoot_px == pytest.approx(8)


def test_unsorted_timestamps_are_handled():
    pts = linear_bot_track(100)
    assert extract_features(list(reversed(pts))).net_dx == pytest.approx(100)
