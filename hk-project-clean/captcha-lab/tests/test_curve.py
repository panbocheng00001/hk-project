import math

import numpy as np
import pytest

from defense.curve import (ASYMMETRIC_SHAPES, PATH_KINDS, PATH_POINTS, CurveChallenge,
                           CurveConfig, generate, render)
from defense.robustness import CURVE_PRESETS, evaluate
from solver.curve_match import contour_align


@pytest.mark.parametrize("path_kind", PATH_KINDS)
@pytest.mark.parametrize("seed", range(5))
def test_path_inside_canvas_and_uniform(path_kind, seed):
    cfg = CurveConfig(path_kind=path_kind)
    ch = generate(cfg, seed=seed)
    assert ch.path.shape == (PATH_POINTS, 2)
    assert (ch.path[:, 0] >= 0).all() and (ch.path[:, 0] <= cfg.width).all()
    assert (ch.path[:, 1] >= 0).all() and (ch.path[:, 1] <= cfg.height).all()
    step = np.hypot(*np.diff(ch.path, axis=0).T)
    assert step.std() / step.mean() < 0.25


def test_deterministic():
    a, b = generate(seed=9), generate(seed=9)
    assert a.target_idx == b.target_idx and np.array_equal(a.bg, b.bg)


@pytest.mark.parametrize("seed", range(10))
def test_targets_and_decoys_are_separated(seed):
    cfg = CurveConfig(num_path_decoys=3)
    ch = generate(cfg, seed=seed)
    idx = [ch.target_idx] + ch.path_decoy_idx
    assert len(idx) == 4
    pts = ch.path[idx]
    for i in range(len(pts)):
        assert math.hypot(*(pts[i] - ch.path[0])) >= 1.6 * cfg.shape_size - 1e-6
        for j in range(i):
            assert math.hypot(*(pts[i] - pts[j])) >= 1.5 * cfg.shape_size - 1e-6


@pytest.mark.parametrize("seed", range(5))
def test_free_decoys_stay_off_path(seed):
    cfg = CurveConfig(num_free_decoys=4)
    ch = generate(cfg, seed=seed)
    for x, y in ch.free_decoys:
        assert np.min(np.hypot(ch.path[:, 0] - x, ch.path[:, 1] - y)) >= 1.1 * cfg.shape_size


def test_is_correct_uses_pixel_tolerance():
    ch = generate(CurveConfig(tolerance_px=8), seed=1)
    assert ch.is_correct(ch.target_s)
    assert not ch.is_correct(0.0)
    near = [i for i in range(PATH_POINTS)
            if 0 < math.hypot(*(ch.path[i] - ch.path[ch.target_idx])) <= 8]
    assert near and all(ch.is_correct(i / (PATH_POINTS - 1)) for i in near)


def test_same_kind_decoys_use_asymmetric_shapes():
    for seed in range(10):
        assert generate(CurveConfig(same_kind_decoys=True), seed=seed).kind in ASYMMETRIC_SHAPES


def test_render_at_target_differs_from_start():
    ch = generate(seed=2)
    assert not np.array_equal(render(ch, 0.0), render(ch, ch.target_s))


def test_index_of_clamps():
    assert CurveChallenge.index_of(-1) == 0
    assert CurveChallenge.index_of(2) == PATH_POINTS - 1


@pytest.mark.parametrize("bad", [
    dict(path_kind="spiral"), dict(contrast=0.0), dict(shape_size=200),
    dict(decoy_angle=(5.0, 40.0)), dict(num_path_decoys=-1),
])
def test_validate_rejects_bad_config(bad):
    with pytest.raises(ValueError):
        generate(CurveConfig(**bad))


@pytest.mark.parametrize("rotate", [0.0, 180.0])
def test_contour_align_finds_target_without_decoys(rotate):
    """没有路径假目标时，轮廓对齐应能稳定找到答案；也顺带校验了旋转方向约定前后端一致。"""
    cfg = CurveConfig(num_path_decoys=0, rotate_deg=rotate)
    hits = 0
    for seed in range(10):
        ch = generate(cfg, seed=seed)
        s = contour_align(ch.bg, ch.piece, ch.path, ch.angle0, cfg.rotate_deg, topk=1)
        hits += ch.is_correct(s[0])
    assert hits >= 9


def test_path_decoys_beat_free_decoys():
    free = evaluate(CURVE_PRESETS["curve_free_decoys6"], samples=20)
    hard = evaluate(CURVE_PRESETS["curve_hardened"], samples=20)
    assert free.attack_top1 > hard.attack_top1
