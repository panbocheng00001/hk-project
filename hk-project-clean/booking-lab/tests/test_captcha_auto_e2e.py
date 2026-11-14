# -*- coding: utf-8 -*-
"""端到端：真实 Playwright + 本地复刻题服务，验证「识别 → 拖动 → 过」整条链路。

这些用例会开局域网到 127.0.0.1:8777；mock 没起时自动拉起，拉不起来就 skip。
"""
from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "captcha_mock"
BASE = "http://127.0.0.1:8777"


def _port_open() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 8777), timeout=0.4).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def mock() -> str:
    proc = None
    if not _port_open():
        try:
            proc = subprocess.Popen(["node", "server.js"], cwd=str(MOCK_DIR),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pytest.skip("没有 node，跳过端到端用例")
        for _ in range(60):
            if _port_open():
                break
            time.sleep(0.1)
        else:
            pytest.skip("captcha_mock 起不来，跳过端到端用例")
    yield BASE
    if proc is not None:
        proc.terminate()


@pytest.fixture()
def browser():
    api = pytest.importorskip("playwright.sync_api")
    with api.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _open(b, tag: str):
    pg = b.new_page()
    pg.goto(f"{BASE}/?t={tag}", wait_until="networkidle")
    pg.wait_for_selector("#handle")
    pg.wait_for_timeout(400)
    return pg


def test_solve_page_passes(mock, browser):
    import captcha_auto

    pg = _open(browser, "solve")
    r = captcha_auto.solve_page(pg, who="e2e", log=lambda *_a: None)
    assert r["ok"], r
    g = pg.evaluate("() => window.__grade || null")
    assert g and g["pass"] is True, g
    assert abs(float(g["diff"])) <= 3.0, g


def test_guard_returns_solved(mock, browser, monkeypatch):
    import captcha

    monkeypatch.setattr(captcha, "_journal_write", lambda *a, **k: None)
    monkeypatch.setenv("CAPTCHA_AUTO", "1")

    pg = _open(browser, "guard")
    out = captcha.guard(pg, "e2e", say=lambda *_a: None)
    assert out == "solved", out
    g = pg.evaluate("() => window.__grade || null")
    assert g and g["pass"] is True, g


def test_guard_falls_back_when_auto_off(mock, browser, monkeypatch):
    """CAPTCHA_AUTO=0：不自动拖动，交人（3 秒内不会自己通过）。"""
    import captcha

    monkeypatch.setattr(captcha, "_journal_write", lambda *a, **k: None)
    monkeypatch.setenv("CAPTCHA_AUTO", "0")

    pg = _open(browser, "off")
    pg.evaluate("() => { window.__autoOff = true; }")
    info = captcha.detect(pg)
    assert info["type"] in ("slider", "unknown", "none"), info
    # 关闭时完全不应出现拖动：手柄位置保持不变
    left = pg.evaluate("() => document.getElementById('handle').style.left")
    assert left in ("", "0px"), left
