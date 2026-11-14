"""轨迹生成器：终点精度、时间单调、风控自检。"""
from __future__ import annotations

import pytest

from solver.track import PROFILES, analyze, audit, generate_track, human_track


@pytest.mark.parametrize("name", list(PROFILES))
def test_track_ends_exactly_at_distance(name):
    pts = generate_track(233.5, profile=name, seed=1, audit_risk=False)
    assert pts[-1][0] == pytest.approx(233.5)
    assert len(pts) > 8


@pytest.mark.parametrize("name", list(PROFILES))
def test_track_time_monotonic(name):
    pts = generate_track(180, profile=name, seed=2, audit_risk=False)
    ts = [p[2] for p in pts]
    assert all(b >= a for a, b in zip(ts, ts[1:]))


@pytest.mark.parametrize("name", list(PROFILES))
def test_track_passes_builtin_risk_model(name):
    """自检开启时，生成的轨迹风险分应低于阈值（0.5）。"""
    pts = generate_track(260, profile=name, seed=3, audit_risk=True)
    ok, risk = audit(pts)
    assert ok, f"{name} risk={risk}"
    assert analyze(pts)["n_points"] == len(pts)


def test_human_track_still_flagged_as_bot():
    """legacy 接口保留：数学拟人轨迹仍会被风控命中，作为负样本。"""
    from defense.trajectory import score_risk
    a = score_risk(human_track(240, overshoot_px=4, seed=0))
    assert a.is_bot


def test_seed_reproducible():
    a = generate_track(200, profile="mouse", seed=42)
    b = generate_track(200, profile="mouse", seed=42)
    assert a == b


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        generate_track(100, profile="robot")


def test_negative_distance_raises():
    with pytest.raises(ValueError):
        generate_track(-1, profile="mouse")
