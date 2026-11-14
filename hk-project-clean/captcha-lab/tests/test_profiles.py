"""站点适配层：口径、字段映射、轨迹格式。"""
from __future__ import annotations

import pytest

from solver.profiles import SITE_PROFILES, SiteProfile, build_payload, get_site
from solver.track import generate_track, to_payload


def test_get_site_by_name_and_object():
    assert get_site("generic").name == "generic"
    obj = SITE_PROFILES["geetest"]
    assert get_site(obj) is obj


def test_unknown_site_raises():
    with pytest.raises(KeyError):
        get_site("nope")


def test_scale_resolution():
    p = SiteProfile(name="t")
    assert p.resolve_scale(340, 680) == pytest.approx(2.0)
    assert p.resolve_scale(340, None) == 1.0
    assert SiteProfile(name="t", scale_by_display=False).resolve_scale(340, 680) == 1.0


def test_generic_payload_fields():
    pts = generate_track(200, seed=1)
    payload = build_payload(get_site("generic"), 200, pts)
    assert payload["x"] == 200
    assert len(payload["track"]) == len(pts)
    assert all(len(p) == 3 for p in payload["track"])


def test_geetest_payload_is_integer_track_with_passtime():
    pts = generate_track(240, seed=2)
    payload = build_payload(get_site("geetest"), 240, pts)
    assert payload["passtime"] == int(round(pts[-1][2] - pts[0][2]))
    assert all(isinstance(v, int) for p in payload["track"] for v in p)


def test_extra_fields_merge():
    pts = generate_track(120, seed=3)
    payload = build_payload(get_site("tencent"), 120, pts, extra={"token": "abc"})
    assert payload["token"] == "abc" and "x" in payload


@pytest.mark.parametrize("fmt", ["xyt", "xy", "x", "geetest", "tencent", "object"])
def test_to_payload_formats(fmt):
    pts = generate_track(150, seed=4)
    out = to_payload(pts, fmt)
    assert out is not None
    if fmt in ("xyt", "xy", "x", "geetest", "tencent"):
        assert len(out) == len(pts)
    if fmt == "object":
        assert {"track", "passtime", "x", "y"}.issubset(out)


def test_to_payload_rejects_unknown_format():
    with pytest.raises(ValueError):
        to_payload(generate_track(100, seed=5), "csv")
