import numpy as np
import pytest

from defense.challenge import (NOTCH_SHAPES, PIECE_PAD, TEXTURES, ChallengeConfig,
                               canvas_side, encode_png, generate)


@pytest.mark.parametrize("shape", NOTCH_SHAPES)
@pytest.mark.parametrize("texture", TEXTURES)
def test_generate_shapes_and_textures(shape, texture):
    decoys = 2 if shape == "puzzle" else 0
    cfg = ChallengeConfig(notch_shape=shape, texture=texture, num_decoys=decoys)
    ch = generate(cfg, seed=3)
    assert ch.bg.shape == (cfg.height, cfg.width, 3)
    assert ch.bg.dtype == np.uint8
    assert ch.side == canvas_side(cfg) >= cfg.notch_side
    assert ch.piece.shape == (ch.side + 2 * PIECE_PAD,) * 2 + (4,)
    assert 0 <= ch.gap_x <= cfg.width - ch.side
    assert 0 <= ch.gap_y <= cfg.height - ch.side


def test_same_seed_is_deterministic():
    a = generate(ChallengeConfig(), seed=11)
    b = generate(ChallengeConfig(), seed=11)
    assert a.gap_x == b.gap_x and a.gap_y == b.gap_y
    assert np.array_equal(a.bg, b.bg)


def test_decoys_share_row_and_do_not_overlap():
    cfg = ChallengeConfig(num_decoys=2)
    for seed in range(20):
        ch = generate(cfg, seed=seed)
        assert len(ch.decoys) == 2
        xs = sorted([ch.gap_x] + [d[0] for d in ch.decoys])
        assert all(b - a >= ch.side for a, b in zip(xs, xs[1:]))
        assert all(d[1] == ch.gap_y for d in ch.decoys)


def test_is_correct_respects_tolerance():
    ch = generate(ChallengeConfig(tolerance_px=5), seed=0)
    assert ch.is_correct(ch.gap_cx + 5)
    assert ch.is_correct(ch.gap_cx - 4.9)
    assert not ch.is_correct(ch.gap_cx + 5.1)


def test_piece_alpha_matches_notch_shape():
    ch = generate(ChallengeConfig(notch_shape="square", num_decoys=0), seed=0)
    inner = ch.piece[PIECE_PAD:-PIECE_PAD, PIECE_PAD:-PIECE_PAD, 3]
    assert (inner > 0).all()
    assert (ch.piece[0, :, 3] == 0).all()


@pytest.mark.parametrize("bad", [
    dict(notch_shape="hexagon"), dict(texture="photo"),
    dict(edge_strength=1.5), dict(notch_side=4),
    dict(notch_shape="square", num_decoys=1),
])
def test_validate_rejects_bad_config(bad):
    with pytest.raises(ValueError):
        generate(ChallengeConfig(**bad))


def test_encode_png_roundtrip():
    png = encode_png(generate(seed=1).bg)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
