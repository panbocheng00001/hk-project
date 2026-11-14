# -*- coding: utf-8 -*-
"""captcha_regress.py 的离线用例。

定位
----
验证固定题号调用、统计口径、JSON 结构和命令行退出码。

输入输出
--------
用假页面、假 Playwright 和假 solve_page 执行回归脚本，不启动浏览器、不连网络。

不变量
------
同一 ``--start/--n`` 请求同一题号段；统计只读取 solve_page 与 window.__grade。

失败路径
--------
服务、浏览器和单题失败分别产生约定退出码或带题号的失败记录，不静默跳过。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

_HERE = Path(__file__).resolve()
_MOD_PATH = _HERE.parents[1] / "tools" / "captcha_regress.py"
ROW_KEYS = {
    "n", "seed", "ok", "pass", "submitted_dx", "truth_dx", "diff", "elapsed_ms",
}


def _load() -> ModuleType:
    """从 tools 目录加载待测 CLI 模块。"""
    spec = importlib.util.spec_from_file_location("captcha_regress", _MOD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def _row(seed: int, ok: bool = True, passed: bool = True,
         diff: float | None = 0.2, ms: float = 100.0) -> dict[str, Any]:
    """构造一条不依赖浏览器的逐题结果。"""
    return {
        "n": seed - 999,
        "seed": seed,
        "ok": ok,
        "pass": passed,
        "submitted_dx": 120.0,
        "truth_dx": 120.0,
        "diff": diff,
        "elapsed_ms": ms,
    }


def _install_fake_browser(
        monkeypatch: pytest.MonkeyPatch, shown_seed: Callable[[int], int] = lambda seed: seed,
) -> tuple[list[int], list[int]]:
    """安装假 Playwright 与 solve_page，返回请求题号和求解题号记录。"""
    requested: list[int] = []
    solved: list[int] = []

    class FakePage:
        def __init__(self) -> None:
            self.current = 0

        def goto(self, _url: str, wait_until: str) -> None:
            assert wait_until == "networkidle"

        def wait_for_selector(self, selector: str) -> None:
            assert selector == "#handle"

        def wait_for_timeout(self, _ms: int) -> None:
            return None

        def inner_text(self, selector: str) -> str:
            assert selector == "#seed"
            return str(shown_seed(self.current))

        def evaluate(self, script: str, seed: int | None = None) -> dict[str, Any]:
            if "newChallenge" in script:
                assert seed is not None
                self.current = int(seed)
                requested.append(self.current)
                return {"seed": self.current}
            assert "__grade" in script
            return {
                "ok": True,
                "pass": True,
                "submitted_dx": 230.25,
                "truth_dx": 230.5,
                "diff": 0.25,
                "sliderDx": 210.68,
            }

    page = FakePage()

    class FakeBrowser:
        def new_page(self) -> FakePage:
            return page

        def close(self) -> None:
            return None

    class FakePlaywright:
        chromium = types.SimpleNamespace(
            launch=lambda **_kwargs: FakeBrowser(),
        )

    class FakeContext:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_solve_page(fake_page: FakePage, **_kwargs: Any) -> dict[str, Any]:
        solved.append(fake_page.current)
        return {"ok": True, "tries": 1, "note": "page-grade"}

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeContext()  # type: ignore[attr-defined]
    captcha_auto = types.ModuleType("captcha_auto")
    captcha_auto.solve_page = fake_solve_page  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setitem(sys.modules, "captcha_auto", captcha_auto)
    return requested, solved


def test_seeds_fixed_range() -> None:
    assert cr.seeds(1000, 3) == [1000, 1001, 1002]


def test_seeds_repeat_identical() -> None:
    assert cr.seeds(1000, 30) == cr.seeds(1000, 30)


def test_run_requests_fixed_seeds_and_reads_page_grade(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """run 逐题调用 newChallenge，并保留页面回显题号与判分字段。"""
    requested, solved = _install_fake_browser(monkeypatch)

    rows = cr.run(cr.BASE_DEFAULT, [1000, 1001, 1002], log=lambda _line: None)

    assert requested == [1000, 1001, 1002]
    assert solved == [1000, 1001, 1002]
    assert [row["n"] for row in rows] == [1, 2, 3]
    assert [row["seed"] for row in rows] == [1000, 1001, 1002]
    assert all(ROW_KEYS <= set(row) for row in rows)
    assert rows[0]["submitted_dx"] == pytest.approx(230.25)
    assert rows[0]["truth_dx"] == pytest.approx(230.5)
    assert rows[0]["diff"] == pytest.approx(0.25)


def test_run_rejects_mismatched_page_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """页面回显题号与请求不一致时，该题失败且不调用 solve_page。"""
    requested, solved = _install_fake_browser(monkeypatch, lambda seed: seed + 1)

    rows = cr.run(cr.BASE_DEFAULT, [1000], log=lambda _line: None)

    assert requested == [1000]
    assert solved == []
    assert rows[0]["ok"] is False
    assert rows[0]["pass"] is False
    assert "题号不一致" in rows[0]["note"]


def test_summarize_counts_and_diff() -> None:
    rows = [_row(1000, diff=0.1), _row(1001, diff=0.3), _row(1002, diff=0.5)]
    summary = cr.summarize(rows)
    assert summary["total"] == 3
    assert summary["passed"] == 3
    assert summary["pass_rate"] == pytest.approx(1.0)
    assert summary["diff_mean"] == pytest.approx(0.3)
    assert summary["diff_p95"] == pytest.approx(0.48)
    assert summary["diff_max"] == pytest.approx(0.5)
    assert summary["elapsed_total_s"] == pytest.approx(0.3)
    assert summary["failed_seeds"] == []


def test_summarize_treats_ok_without_pass_as_failure() -> None:
    rows = [_row(1000), _row(1001, ok=True, passed=False, diff=None)]
    summary = cr.summarize(rows)
    assert summary["passed"] == 1
    assert summary["failed_seeds"] == [1001]


def test_summarize_single_and_empty_inputs() -> None:
    one = cr.summarize([_row(1000, diff=0.4)])
    empty = cr.summarize([])
    assert one["diff_p95"] == pytest.approx(0.4)
    assert empty["total"] == 0
    assert empty["pass_rate"] == 0.0
    assert empty["diff_mean"] is None


def test_decide_exit_paths() -> None:
    passed = cr.summarize([_row(1000), _row(1001)])
    mixed = cr.summarize([_row(1000), _row(1001, passed=False, diff=None)])
    assert cr.decide_exit(passed, 1.0) == 0
    assert cr.decide_exit(mixed, 1.0) == 1
    assert cr.decide_exit(mixed, 0.5) == 0
    assert cr.decide_exit(cr.summarize([]), 0.0) == 1


def test_service_alive_disables_environment_proxy(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """回环健康检查使用空代理表，不继承系统代理。"""
    seen: dict[str, Any] = {}

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Opener:
        def open(self, url: str, timeout: float) -> Response:
            seen["url"] = url
            seen["timeout"] = timeout
            return Response()

    def build_opener(*handlers: object) -> Opener:
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(cr.urllib.request, "build_opener", build_opener)
    monkeypatch.setattr(
        cr.urllib.request,
        "urlopen",
        lambda *_a, **_kw: pytest.fail("不应调用继承系统代理的 urlopen"),
    )

    assert cr.service_alive(cr.BASE_DEFAULT) is True
    handler = seen["handlers"][0]
    assert isinstance(handler, urllib.request.ProxyHandler)
    assert handler.proxies == {}


def test_service_alive_false_when_unreachable(
        monkeypatch: pytest.MonkeyPatch) -> None:
    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr(cr.urllib.request, "build_opener", lambda *_a: Opener())
    assert cr.service_alive("http://127.0.0.1:1") is False


def test_main_exit_3_when_service_down(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cr, "service_alive", lambda *_a, **_k: False)
    code = cr.main(["--n", "2"])
    output = capsys.readouterr().out
    assert code == 3
    assert "node server.js" in output


def test_main_exit_4_when_playwright_missing(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cr, "service_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "playwright_available", lambda: False)
    code = cr.main(["--n", "2"])
    assert code == 4
    assert "playwright" in capsys.readouterr().out


def test_main_exit_1_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cr, "service_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "playwright_available", lambda: True)
    monkeypatch.setattr(
        cr,
        "run",
        lambda *_a, **_k: [_row(1000), _row(1001, passed=False, diff=None)],
    )
    assert cr.main(["--n", "2", "--fail-under", "1.0"]) == 1


def test_main_writes_json_and_returns_0(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cr, "service_alive", lambda *_a, **_k: True)
    monkeypatch.setattr(cr, "playwright_available", lambda: True)

    def fake_run(_base: str, seed_list: list[int],
                 headless: bool = True, log: Any = print) -> list[dict[str, Any]]:
        assert headless is True
        assert list(seed_list) == [1000, 1001, 1002]
        return [_row(seed) for seed in seed_list]

    monkeypatch.setattr(cr, "run", fake_run)
    dst = tmp_path / "regress.json"
    code = cr.main(["--start", "1000", "--n", "3", "--json", str(dst)])
    payload = json.loads(dst.read_text(encoding="utf-8"))

    assert code == 0
    assert payload["summary"]["passed"] == 3
    assert [row["seed"] for row in payload["rows"]] == [1000, 1001, 1002]
    assert all(ROW_KEYS <= set(row) for row in payload["rows"])
