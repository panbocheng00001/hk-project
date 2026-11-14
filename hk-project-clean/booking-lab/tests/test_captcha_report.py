# -*- coding: utf-8 -*-
"""captcha_report 的 pytest 用例。

定位
----
把交接报表口径纳入 pytest，并覆盖命令行 JSON 导出。

输入输出
--------
读取 ``tests/fixtures/journal`` 的固定样本，断言统计、分组、文本输出与 JSON 结构。

不变量
------
测试固定 ``today=2026-11-11``，不读取真实 runs、不启动浏览器、不依赖真实时间。

失败路径
--------
坏行、坏数值、缺字段与空文件不得抛异常；没有 handoff_done 时按无数据返回。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import captcha_report as cr

FIX = Path(__file__).resolve().parent / "fixtures" / "journal"
TODAY = date(2026, 11, 11)
JSON_KEYS = {
    "days", "n", "solved", "timeout", "rate", "p50", "p90", "max",
    "avg_renotify", "slider", "by_kind", "by_day",
}
GROUP_KEYS = {
    "n", "solved", "timeout", "rate", "p50", "p90", "max", "avg_renotify",
}


@pytest.fixture()
def summary() -> dict[str, Any]:
    """返回固定两天样本的统计结果。"""
    return cr.summarize(cr.load(7, FIX, today=TODAY))


def _freeze_cli_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """让命令行测试使用固定日期窗口。"""
    real_load = cr.load

    def fixed_load(days: int, jdir: Path = cr.JDIR) -> list[dict[str, Any]]:
        return real_load(days, jdir, today=TODAY)

    monkeypatch.setattr(cr, "load", fixed_load)


def test_fixture_files_present() -> None:
    """夹具目录只包含约定的两个日期文件。"""
    assert sorted(p.name for p in FIX.glob("*.jsonl")) == [
        "2026-11-10.jsonl",
        "2026-11-11.jsonl",
    ]


def test_totals_and_percentiles(summary: dict[str, Any]) -> None:
    """总体次数、完成率、超时和完成耗时沿用既有口径。"""
    assert summary["n"] == 7
    assert summary["solved"] == 4
    assert summary["timeout"] == 3
    assert summary["rate"] == pytest.approx(4 / 7)
    assert summary["p50"] == pytest.approx(25.0)
    assert summary["p90"] == pytest.approx(37.0)
    assert summary["max"] == pytest.approx(40.0)
    assert summary["avg_renotify"] == pytest.approx(1.0)
    assert summary["slider"] == 5
    assert summary["renotify_events"] == 2


def test_percentile_uses_linear_interpolation() -> None:
    """P50/P90 使用排序后位置的线性插值。"""
    assert cr.percentile([], 0.5) is None
    assert cr.percentile([5.0], 0.9) == 5.0
    assert cr.percentile([4, 1, 3, 2], 0.9) == pytest.approx(3.7)
    assert cr.percentile([10, 20, 30, 40], 0.5) == pytest.approx(25.0)


def test_by_kind_is_grouped_by_count(summary: dict[str, Any]) -> None:
    """类型分组按交接次数降序，组内统计不混入其他类型。"""
    grouped = summary["by_kind"]
    assert list(grouped) == ["slider", "image_code", "unknown"]
    assert set(grouped["slider"]) == GROUP_KEYS
    assert grouped["slider"] == {
        "n": 5,
        "solved": 3,
        "timeout": 2,
        "rate": pytest.approx(3 / 5),
        "p50": pytest.approx(20.0),
        "p90": pytest.approx(28.0),
        "max": pytest.approx(30.0),
        "avg_renotify": pytest.approx(0.8),
    }
    assert grouped["image_code"]["p50"] == pytest.approx(40.0)
    assert grouped["unknown"]["rate"] == pytest.approx(0.0)


def test_by_day_is_chronological(summary: dict[str, Any]) -> None:
    """按天趋势按日期升序输出。"""
    grouped = summary["by_day"]
    assert list(grouped) == ["2026-11-10", "2026-11-11"]
    assert grouped["2026-11-10"]["n"] == 4
    assert grouped["2026-11-10"]["p50"] == pytest.approx(20.0)
    assert grouped["2026-11-11"]["n"] == 3
    assert grouped["2026-11-11"]["rate"] == pytest.approx(1 / 3)


def test_bad_values_missing_fields_and_empty_file_are_tolerated(tmp_path: Path) -> None:
    """坏数值不进耗时统计，缺字段和空文件不产生交接记录。"""
    (tmp_path / "2026-11-10.jsonl").write_text("", encoding="utf-8")
    rows = [
        "{bad-json",
        json.dumps({}),
        json.dumps({"src": "captcha", "ev": "renotify"}),
        json.dumps({
            "iso": "2026-11-11T10:00:00+08:00",
            "src": "captcha",
            "ev": "handoff_done",
            "result": "solved",
            "waited_s": "bad",
        }),
    ]
    (tmp_path / "2026-11-11.jsonl").write_text("\n".join(rows), encoding="utf-8")

    result = cr.summarize(cr.load(7, tmp_path, today=TODAY))

    assert result["n"] == 1
    assert result["solved"] == 1
    assert result["p50"] is None
    assert result["p90"] is None
    assert result["max"] is None
    assert result["by_kind"]["?"]["n"] == 1


def test_empty_directory_prints_no_data_and_returns_zero(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """空目录是正常的无数据状态。"""
    code = cr.main(["--dir", str(tmp_path), "--days", "7"])

    assert code == 0
    assert "无数据" in capsys.readouterr().out


def test_json_file_matches_public_structure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        summary: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    """--json 落盘字段与 render 使用同一份 summarize 结果。"""
    _freeze_cli_today(monkeypatch)
    output = tmp_path / "nested" / "report.json"

    code = cr.main([
        "--dir", str(FIX),
        "--days", "7",
        "--json", str(output),
    ])
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert set(payload) == JSON_KEYS
    assert payload == cr.to_json(summary, 7)
    assert payload["n"] == 7
    assert payload["p50"] == pytest.approx(25.0)
    assert payload["by_kind"]["slider"]["n"] == 5
    stdout = capsys.readouterr().out
    assert "统计已写入" in stdout
    assert "按类型" in stdout
    assert "按天趋势" in stdout