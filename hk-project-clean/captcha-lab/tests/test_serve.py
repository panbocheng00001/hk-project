"""HTTP 服务：接口契约、鉴权、错误码。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from solver import encode_image_b64
from solver.serve import create_app
from solver.synth import make_sample


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(scope="module")
def images():
    img, piece, rect = make_sample(seed=21)
    return encode_image_b64(img), encode_image_b64(piece), rect


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"]


def test_profiles(client):
    data = client.get("/v1/profiles").json()
    assert "generic" in data["sites"] and "stealth" in data["tracks"]


def test_solve(client, images):
    bg, piece, (x, y, w, h) = images
    r = client.post("/v1/solve", json={"bg": bg, "piece": piece, "site": "generic"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and abs(body["x"] - (x + w / 2)) <= 6
    assert body["payload"]["x"] == body["distance"]
    assert body["track"]


def test_solve_with_debug_image(client, images):
    bg, piece, _ = images
    r = client.post("/v1/solve", json={"bg": bg, "piece": piece, "debug_image": True})
    assert r.status_code == 200 and r.json()["debug_image"]


def test_solve_bad_image(client):
    r = client.post("/v1/solve", json={"bg": "aGVsbG8="})   # "hello"
    assert r.status_code in (400, 422)


def test_track_endpoint(client):
    r = client.post("/v1/track", json={"distance": 212, "profile": "stealth"})
    assert r.status_code == 200
    body = r.json()
    assert body["payload"]["x"] == 212
    assert body["features"]["risk"] < 0.5


def test_track_unknown_site(client):
    r = client.post("/v1/track", json={"distance": 100, "site": "nope"})
    assert r.status_code == 422


def test_metrics(client):
    client.get("/healthz")
    out = client.get("/metrics").text
    assert "captcha_solve_total" in out


def test_api_key_enforced(monkeypatch):
    monkeypatch.setenv("CAPTCHA_API_KEY", "k1")
    c = TestClient(create_app())
    r = c.post("/v1/track", json={"distance": 100})
    assert r.status_code == 401
    r = c.post("/v1/track", json={"distance": 100}, headers={"X-API-Key": "k1"})
    assert r.status_code == 200
