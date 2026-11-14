import json

import pytest
from fastapi.testclient import TestClient

from defense.challenge import PIECE_PAD
from defense.curve import PATH_POINTS
from defense.service import ServiceSettings, create_app
from defense.trajectory import linear_bot_track
from tests.test_trajectory import _noisy_track


@pytest.fixture
def client(tmp_path):
    s = ServiceSettings(secret=b"test", rate_per_min=600, burst=50,
                        record_path=tmp_path / "rec.jsonl")
    return TestClient(create_app(s))


def _truth_x(client, token):
    """测试专用：从服务端存储里拿真值，换算成前端应提交的滑块左边缘 x。"""
    a = _pending(client, token).answer
    return a["gap_cx"] - a["side"] / 2.0 - PIECE_PAD


def _pending(client, token):
    return client.app.state.store._pending[token.split(".")[0]]


def _new(client, kind="slider"):
    r = client.post(f"/api/challenge?kind={kind}")
    assert r.status_code == 200
    return r.json()


def test_challenge_does_not_leak_answer(client):
    body = _new(client)
    assert set(body) == {"token", "kind", "bg", "piece", "piece_y", "width",
                         "height", "expires_in"}
    assert body["bg"].startswith("data:image/png;base64,")


def test_curve_challenge_sends_path_but_not_target(client):
    body = _new(client, "curve")
    assert set(body) == {"token", "kind", "bg", "piece", "path", "angle0",
                         "rotate_deg", "width", "height", "prompt", "expires_in"}
    assert len(body["path"]) == PATH_POINTS


def _truth_s(client, token):
    a = _pending(client, token).answer
    return a["path"].index(a["target"]) / (PATH_POINTS - 1)


def test_curve_correct_overlap_passes(client):
    ch = _new(client, "curve")
    s = _truth_s(client, ch["token"])
    r = client.post("/api/verify", json={"token": ch["token"], "s": s,
                                         "track": _noisy_track(300 * s, seed=4)}).json()
    assert r["ok"] is True


def test_curve_wrong_overlap_rejected(client):
    ch = _new(client, "curve")
    s = _truth_s(client, ch["token"])
    wrong = s - 0.25 if s > 0.5 else s + 0.25
    r = client.post("/api/verify", json={"token": ch["token"], "s": wrong,
                                         "track": _noisy_track(300 * wrong, seed=5)}).json()
    assert r["ok"] is False and r["reason"] == "position"


def test_curve_bot_track_rejected(client):
    ch = _new(client, "curve")
    s = _truth_s(client, ch["token"])
    r = client.post("/api/verify", json={"token": ch["token"], "s": s,
                                         "track": linear_bot_track(300 * s)}).json()
    assert r["ok"] is False and r["reason"] == "behavior"


def test_missing_answer_field_is_wrong_position(client):
    ch = _new(client, "curve")
    r = client.post("/api/verify", json={"token": ch["token"], "x": 100,
                                         "track": _noisy_track(100, seed=6)}).json()
    assert r["reason"] == "position"


def test_unknown_kind_rejected(client):
    assert client.post("/api/challenge?kind=rotate").status_code == 400


def test_correct_position_and_human_like_track_passes(client):
    ch = _new(client)
    x = _truth_x(client, ch["token"])
    r = client.post("/api/verify", json={"token": ch["token"], "x": x,
                                         "track": _noisy_track(x, seed=1)}).json()
    assert r["ok"] is True
    assert client.post("/api/redeem", json={"pass_token": r["pass_token"]}).json()["valid"]
    assert not client.post("/api/redeem", json={"pass_token": r["pass_token"]}).json()["valid"]


def test_correct_position_but_bot_track_is_rejected(client):
    ch = _new(client)
    x = _truth_x(client, ch["token"])
    r = client.post("/api/verify", json={"token": ch["token"], "x": x,
                                         "track": linear_bot_track(x)}).json()
    assert r["ok"] is False and r["reason"] == "behavior"
    assert r["signals"]


def test_wrong_position_is_rejected(client):
    ch = _new(client)
    x = _truth_x(client, ch["token"]) + 30
    r = client.post("/api/verify", json={"token": ch["token"], "x": x,
                                         "track": _noisy_track(x, seed=2)}).json()
    assert r["ok"] is False and r["reason"] == "position"


def test_challenge_is_single_use(client):
    ch = _new(client)
    x = _truth_x(client, ch["token"])
    payload = {"token": ch["token"], "x": x, "track": _noisy_track(x, seed=3)}
    client.post("/api/verify", json=payload)
    r = client.post("/api/verify", json=payload).json()
    assert r["ok"] is False and r["reason"] == "expired_or_used"


def test_forged_token_rejected(client):
    ch = _new(client)
    cid = ch["token"].split(".")[0]
    r = client.post("/api/verify", json={"token": f"{cid}.deadbeefdeadbeef",
                                         "x": 0, "track": []})
    assert r.status_code == 400


def test_rate_limit(tmp_path):
    app = create_app(ServiceSettings(secret=b"t", rate_per_min=1, burst=2))
    c = TestClient(app)
    codes = [c.post("/api/challenge").status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_record_endpoint_appends_jsonl(client, tmp_path):
    track = [[0, 0, 0], [10, 1, 20], [20, 1, 40]]
    assert client.post("/api/record", json={"track": track}).json() == {"saved": True}
    rec = json.loads((tmp_path / "rec.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["label"] == "human" and rec["track"] == track


def test_record_disabled_by_default():
    c = TestClient(create_app(ServiceSettings(secret=b"t")))
    assert c.post("/api/record", json={"track": [[0, 0, 0]]}).status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "拖动滑块" in r.text


def test_curve_page_served(client):
    r = client.get("/curve")
    assert r.status_code == 200 and "kind=curve" in r.text
