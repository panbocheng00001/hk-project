"""求解引擎：识别精度、口径换算、候选重试、输入形态。"""
from __future__ import annotations

import base64

import numpy as np
import pytest

from solver import SliderSolver, encode_image_b64
from solver.engine import SolveError, load_image
from solver.synth import make_sample


@pytest.fixture(scope="module")
def sample():
    img, piece, (x, y, w, h) = make_sample(seed=11)
    return img, piece, (x, y, w, h)


def test_solve_hits_ground_truth(sample):
    img, piece, (x, y, w, h) = sample
    r = SliderSolver(audit_track=False).solve(img, piece=piece)
    assert abs(r.x - (x + w / 2)) <= 6
    assert r.ok and r.answers
    assert 0 < r.confidence <= 1.0


def test_candidates_sorted_by_confidence(sample):
    img, piece, _ = sample
    r = SliderSolver(topk=4, audit_track=False).solve(img, piece=piece)
    confs = [a.confidence for a in r.answers]
    assert confs == sorted(confs, reverse=True)
    assert len(r.answers) <= 4


def test_answer_modes_differ_by_half_piece(sample):
    """left 口径比 center 口径少半个拼图块宽度。"""
    img, piece, (_, _, w, _) = sample
    center = SliderSolver(site="generic", audit_track=False).solve(img, piece=piece)
    left = SliderSolver(site="geetest", audit_track=False).solve(img, piece=piece)
    assert center.answer.distance > left.answer.distance
    assert abs((center.answer.distance - left.answer.distance) - w / 2) <= 3


def test_display_width_scales_distance(sample):
    img, piece, _ = sample
    r = SliderSolver(audit_track=False).solve(img, piece=piece, display_width=170)
    assert r.scale == pytest.approx(0.5, abs=1e-6)
    assert abs(r.distance - r.x * 0.5) <= 1.5


def test_solve_accepts_base64_and_bytes(sample):
    img, piece, _ = sample
    b64 = encode_image_b64(img)
    r1 = SliderSolver(audit_track=False).solve(b64, piece=piece)
    r2 = SliderSolver(audit_track=False).solve("data:image/png;base64," + b64)
    import cv2
    ok, buf = cv2.imencode(".png", img)
    r3 = SliderSolver(audit_track=False).solve(buf.tobytes(), piece=piece)
    for r in (r1, r2, r3):
        assert abs(r.x - r1.x) < 1e-6


def test_solve_accepts_url(monkeypatch, sample):
    """站点把题图放 CDN 上时，直接传 URL。"""
    import cv2
    img, _, _ = sample
    ok, buf = cv2.imencode(".png", img)
    monkeypatch.setattr("solver.engine._fetch_url",
                        lambda url, timeout=10.0: buf.tobytes())
    r = SliderSolver(audit_track=False).solve("https://cdn.example.com/bg.png")
    assert r.x > 0 and r.ok


def test_payload_matches_site_profile(sample):
    img, piece, _ = sample
    r = SliderSolver(site="geetest", audit_track=False).solve(img, piece=piece)
    assert set(["x", "track", "passtime"]).issubset(r.payload)
    assert isinstance(r.payload["track"][0][0], int)
    assert r.payload["passtime"] > 0


def test_result_serialization(sample):
    img, piece, _ = sample
    r = SliderSolver(audit_track=False).solve(img, piece=piece)
    d = r.to_dict()
    assert d["ok"] and "answers" in d and "payload" in d
    assert len(d["track"]) == len(r.track)


def test_solve_until_retries_candidate(monkeypatch, sample):
    """第一次提交失败时应该换下一个候选，而不是直接放弃。"""
    img, piece, _ = sample
    attempts = []

    def fetch(attempt):
        return {"bg": img, "piece": piece}

    def submit(answer, payload):
        attempts.append(answer.distance)
        return len(attempts) >= 2          # 第二次才通过

    s = SliderSolver(topk=3, audit_track=False)
    r = s.solve_until(fetch, submit, max_attempts=2, candidates_per_challenge=2)
    assert r.ok
    assert len(attempts) == 2
    assert attempts[0] != attempts[1]


def test_solve_until_all_fail(sample):
    img, piece, _ = sample
    s = SliderSolver(topk=2, audit_track=False)
    r = s.solve_until(lambda i: {"bg": img, "piece": piece},
                      lambda a, p: False, max_attempts=1, candidates_per_challenge=1)
    assert not r.ok


def test_empty_image_raises():
    tiny = np.full((10, 10, 3), 255, np.uint8)
    with pytest.raises(SolveError):
        SliderSolver(audit_track=False).solve(tiny)


def test_load_image_rejects_garbage():
    with pytest.raises((FileNotFoundError, ValueError)):
        load_image("not-a-real-file-and-not-base64!!")
