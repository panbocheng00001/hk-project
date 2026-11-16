# -*- coding: utf-8 -*-
"""captcha_lab_bridge 的回归测试
=====================================================================
三类用例：

    1) 黄金样本：tests/data/waf_canvas.png -> dx≈230.5，slider_dx≈210.91
    2) 契约：make_track 末点 x == slider_dx、时间单调、风险分 < 0.5
    3) 集成：captcha_mock 在线时跑 10 个种子断言 10/10；离线则 skip

集成用例只用标准库 urllib（不引入新依赖）对接 127.0.0.1:8777 的复刻服务。
运行：
    cd hKdriver && python -m pytest test_captcha_lab_bridge.py -q
在线跑（不得 skip）：先 `cd captcha_mock && node server.js`
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import captcha_lab_bridge as bridge

SPECIMEN = Path(__file__).parent / "tests" / "data" / "waf_canvas.png"
MOCK_BASE = "http://127.0.0.1:8777"

# 实测几何（与 captcha_mock server.js 的 GEOM 一致）
TRACK_W = 400.0
HANDLE_W = 34.0
BMP_W = 400.0


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _http_json(path: str, payload: Optional[Dict[str, Any]] = None,
               timeout: float = 5.0) -> Dict[str, Any]:
    """对复刻服务发一个 GET/POST（POST 带 json body），返回解析后的 dict。"""
    url = MOCK_BASE + path
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mock_online() -> bool:
    try:
        r = _http_json("/healthz", timeout=1.5)
        return bool(r.get("ok"))
    except Exception:
        return False


MOCK_ONLINE = _mock_online()


# --------------------------------------------------------------------------- #
# 前置：内核必须可用（桥不可用则整文件红，暴露接线问题而非悄悄 skip）
# --------------------------------------------------------------------------- #
def test_bridge_available():
    assert bridge.is_available(), (
        f"captcha-lab 内核不可用；源目录={bridge.captcha_lab_path()}")


# --------------------------------------------------------------------------- #
# 1) 黄金样本
# --------------------------------------------------------------------------- #
def test_golden_specimen():
    assert SPECIMEN.exists(), f"缺少黄金样本 {SPECIMEN}"
    res = bridge.solve_canvas(SPECIMEN.read_bytes(), track_w=TRACK_W, handle_w=HANDLE_W)
    assert res["ok"], res.get("error")
    assert res["dx"] == pytest.approx(230.5, abs=1.0)
    assert res["slider_dx"] == pytest.approx(210.91, abs=0.1)
    assert res["current_x"] < res["target_x"]     # 实心在左、目标空心在右
    assert res["pairs"] >= 2


def test_golden_specimen_accepts_dataurl():
    import base64
    b64 = base64.b64encode(SPECIMEN.read_bytes()).decode()
    res = bridge.solve_canvas("data:image/png;base64," + b64,
                              track_w=TRACK_W, handle_w=HANDLE_W)
    assert res["ok"]
    assert res["dx"] == pytest.approx(230.5, abs=1.0)


# --------------------------------------------------------------------------- #
# 2) 轨迹契约
# --------------------------------------------------------------------------- #
def test_make_track_contract():
    slider_dx = 210.91
    pts = bridge.make_track(slider_dx, profile="stealth", seed=7)
    assert len(pts) >= 10

    # 末点 x 精确等于 slider_dx
    assert pts[-1][0] == pytest.approx(slider_dx, abs=1e-6)

    # 时间单调不减
    ts = [t for _, _, t in pts]
    assert all(b >= a for a, b in zip(ts, ts[1:])), "时间戳必须单调"

    # 风险分 < 0.5（make_track 内部已带风控自检）
    risk = bridge.analyze_track(pts).get("risk", 1.0)
    assert risk < 0.5, f"风险分过高：{risk}"


def test_make_track_seed_reproducible():
    a = bridge.make_track(188.0, seed=123)
    b = bridge.make_track(188.0, seed=123)
    assert a == b, "同种子应产出同轨迹"


# --------------------------------------------------------------------------- #
# 3) 集成：captcha_mock 在线时跑 10 个种子，断言 10/10
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not MOCK_ONLINE,
                    reason=f"captcha_mock 未在线（{MOCK_BASE}）：离线跳过集成用例")
def test_mock_ten_seeds_all_pass():
    n, start = 10, 2000
    passed = 0
    for i in range(n):
        seed = start + i
        ch = _http_json(f"/challenge?seed={seed}&dpr=1")
        res = bridge.solve_canvas(ch["dataUrl"], track_w=TRACK_W, handle_w=HANDLE_W,
                                  bmp_w=ch["bmpW"])
        assert res["ok"], f"seed={seed} 识别失败：{res.get('error')}"
        grade = _http_json("/grade", {
            "seed": seed, "sliderDx": res["slider_dx"],
            "bmpW": ch["bmpW"], "trackW": TRACK_W, "handleW": HANDLE_W,
        })
        if grade.get("pass"):
            passed += 1
        else:
            print(f"seed={seed} 未过 diff={grade.get('diff')} "
                  f"slider_dx={res['slider_dx']}")
    assert passed == n, f"只过了 {passed}/{n}"


@pytest.mark.skipif(not MOCK_ONLINE,
                    reason=f"captcha_mock 未在线（{MOCK_BASE}）：离线跳过对拍用例")
def test_mock_parity_with_analyze_js():
    """与 Node 版 analyze.js 的 dx 对齐（判据一致性），误差应 < 1px。"""
    seed = 1000
    ch = _http_json(f"/challenge?seed={seed}&dpr=1")
    res = bridge.solve_canvas(ch["dataUrl"], track_w=TRACK_W, handle_w=HANDLE_W,
                              bmp_w=ch["bmpW"])
    js = _http_json("/analyze", {
        "dataUrl": ch["dataUrl"], "bmpW": ch["bmpW"], "bmpH": ch["bmpH"],
        "cssW": ch["geom"]["w"], "cssH": ch["geom"]["h"], "dpr": 1,
        "trackW": TRACK_W, "handleW": HANDLE_W, "seed": seed,
    })
    assert js.get("ok")
    assert abs(res["dx"] - js["dx"]) < 1.0, f"与 JS 判据偏差：{res['dx']} vs {js['dx']}"
