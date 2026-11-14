"""几何重合滑块（画布实心/空心配对）：判据、换算、与 JS 版对拍。

黄金样本 `tests/data/waf_canvas.png` 由 booking-lab/captcha_mock 的 seed=1000 生成，
Node 版 analyze.js 与 Python 版的 dx 均为 230.5。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from solver.overlap import analyze, analyze_payload, solve_canvas, to_slider_dx

SPECIMEN = Path(__file__).parent / "data" / "waf_canvas.png"


def test_golden_specimen_dx():
    res = analyze(str(SPECIMEN))
    assert res.ok
    assert abs(res.dx - 230.5) <= 1.0
    assert res.current_x < res.target_x          # 实心在左，目标空心在右
    assert res.pairs >= 2


def test_slider_mapping_matches_frontend():
    """slider_dx = dx * (trackW - handleW) / bmpW，range 下限 10。"""
    assert to_slider_dx(230.5, 400, 400, 34) == pytest.approx(230.5 * 366 / 400, rel=1e-9)
    assert to_slider_dx(10, 400, 8, 6) == pytest.approx(10 * 10 / 400, rel=1e-9)


def test_solve_canvas_returns_payload_shape():
    out = solve_canvas(str(SPECIMEN), track_w=400, handle_w=34)
    for k in ("ok", "dx", "slider_dx", "bmp_w", "track_w", "handle_w"):
        assert k in out
    assert out["ok"] and out["slider_dx"] == pytest.approx(230.5 * 366 / 400, abs=0.02)


def test_analyze_payload_accepts_data_url():
    import base64
    b64 = base64.b64encode(SPECIMEN.read_bytes()).decode()
    res = analyze_payload({"dataUrl": "data:image/png;base64," + b64, "bmpW": 400})
    assert res.ok and abs(res.dx - 230.5) <= 1.0


def test_blank_canvas_rejected():
    blank = np.zeros((330, 400, 3), np.uint8)
    res = analyze(blank)
    assert not res.ok and res.error


def test_mostly_grid_canvas_rejected():
    """只有深色网格、没有图形时不应硬凑出一对。"""
    canvas = np.full((330, 400, 3), 24, np.uint8)
    for x in range(0, 400, 20):
        canvas[:, x:x + 1] = 40
    for y in range(0, 330, 20):
        canvas[y:y + 1, :] = 40
    res = analyze(canvas)
    assert not res.ok
