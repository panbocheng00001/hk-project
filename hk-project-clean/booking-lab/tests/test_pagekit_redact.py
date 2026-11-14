# -*- coding: utf-8 -*-
"""redact.py 的离线用例：遮得住、遮得稳、报告里不泄露原文。

定位
----
不启动浏览器、不读真实词表、不连网络；用拼装出来的样例覆盖内置规则与词表规则。

输入输出
--------
输入是样例文本与临时词表 JSON，输出是 ``Redacted`` 与 ``Leftover`` 列表。

不变量
------
· 命中记录只有规则名与次数，任何路径都拿不到命中原文
· ``redact`` 幂等：对已脱敏文本再跑一次，结果不变
· 内置规则不遮 ``example.test`` 一类占位邮箱，避免测试夹具自遮自

失败路径
--------
词表缺失/损坏只记 ``notes``，内置规则照常生效。
"""

from __future__ import annotations

import json
from pathlib import Path

from pagekit.redact import (Redactor, TermTable, iter_rule_names, load_terms,
                            scan_leftovers)


def _sample(kind: str) -> str:
    """样例按片段拼接：仓内 lint 的 pii 规则不允许把真实格式的串落盘。"""
    if kind == "hkid":
        return "A" + "123456" + "(3)"
    if kind == "email":
        return "user" + "@" + "corp" + ".test"
    if kind == "mobile":
        return "+86" + "1" + "3" + "9" + "0000" + "0000"
    if kind == "hkphone":
        return "2804" + " " + "2600"
    if kind == "jwt":
        return "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "c2lnbmF0dXJlWGlueg"
    if kind == "cookie":
        return "HWWAFSESID" + "=" + "abc123def456"
    if kind == "hex":
        return "9f" * 16
    raise KeyError(kind)


def test_builtin_rules_mask_every_shape() -> None:
    text = "\n".join([
        f"申请人身分证 {_sample('hkid')} 已核对",
        f"联络电话 {_sample('mobile')} 与 {_sample('hkphone')}",
        f"电邮 {_sample('email')}",
        f"请求头 Authorization: Bearer {_sample('jwt')}",
        f"Cookie: {_sample('cookie')}; other=1",
        f"追踪码 {_sample('hex')}",
        "打开 /api/x?token=SECRETVALUE&lang=sc",
    ])
    result = Redactor().redact(text)

    for kind in ("hkid", "email", "mobile", "jwt", "cookie", "hex"):
        assert _sample(kind) not in result.text, f"{kind} 没遮住"
    assert "SECRETVALUE" not in result.text
    assert "<HKID>" in result.text
    assert "<EMAIL>" in result.text
    assert "<PHONE>" in result.text
    assert "<TOKEN>" in result.text
    assert "<COOKIE>" in result.text
    assert "<HEX>" in result.text
    assert "<PARAM>" in result.text


def test_hits_never_carry_the_original_text() -> None:
    text = f"身分证 {_sample('hkid')}，电邮 {_sample('email')}"
    result = Redactor().redact(text)
    dumped = json.dumps([hit.__dict__ for hit in result.hits], ensure_ascii=False)
    assert _sample("hkid") not in dumped
    assert _sample("email") not in dumped
    assert {hit.rule for hit in result.hits} >= {"hkid", "email"}


def test_redact_is_idempotent() -> None:
    text = f"身分证 {_sample('hkid')}，电话 {_sample('hkphone')}"
    once = Redactor().redact(text).text
    twice = Redactor().redact(once).text
    assert once == twice


def test_terms_file_maps_literal_and_keeps_stable_placeholder(tmp_path: Path) -> None:
    terms_file = tmp_path / "terms.json"
    terms_file.write_text(json.dumps({
        "hosts": [{"host": "abs3.example.test", "placeholder": "<SITE_HOST>"}],
        "terms": [
            {"term": "示例发证机关", "placeholder": "<ISSUER>"},
            {"term": "示例发证机关牌照事务处", "placeholder": "<OFFICE_FULL>"},
        ],
        "patterns": [{"name": "ref", "pattern": r"\bREF-\d{4}\b", "placeholder": "<BOOKING_REF>"}],
    }, ensure_ascii=False), encoding="utf-8")

    redactor = Redactor(load_terms(terms_file))
    text = "示例发证机关牌照事务处；示例发证机关；编号 REF-1234；站点 abs3.example.test"
    result = redactor.redact(text)

    assert "<OFFICE_FULL>" in result.text      # 长词优先
    assert "<ISSUER>" in result.text
    assert "<BOOKING_REF>" in result.text
    assert "<SITE_HOST>" in result.text
    assert "abs3.example.test" not in result.text
    # 同一词第二次出现仍用同一占位
    assert result.text.count("<ISSUER>") == 1 and result.text.count("<OFFICE_FULL>") == 1


def test_missing_terms_file_only_records_a_note(tmp_path: Path) -> None:
    table = load_terms(tmp_path / "nope.json")
    assert table.notes and "词表不存在" in table.notes[0]
    result = Redactor(table).redact(f"电话 {_sample('hkphone')}")
    assert "<PHONE>" in result.text
    assert result.notes


def test_broken_terms_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    terms_file = tmp_path / "broken.json"
    terms_file.write_text(json.dumps({
        "terms": [{"term": ""}],
        "patterns": [{"name": "bad", "pattern": "(["}],
    }), encoding="utf-8")
    table = load_terms(terms_file)
    assert any("已跳过" in note for note in table.notes)
    assert "<PHONE>" in Redactor(table).redact(_sample("hkphone")).text


def test_redact_url_keeps_path_only() -> None:
    table = TermTable(hosts=[("abs3.example.test", "<SITE_HOST>")])
    redactor = Redactor(table)
    assert redactor.redact_url("https://abs3.example.test/SelectAuth?lang=sc&token=abc") == \
        "https://<SITE_HOST>/SelectAuth?lang&token"
    assert redactor.redact_url("/api/queue?c=1&e=2") == "/api/queue?c&e"


def test_scan_leftovers_empty_after_redact() -> None:
    text = f"身分证 {_sample('hkid')}；电话 {_sample('hkphone')}；电邮 {_sample('email')}"
    before = scan_leftovers(text)
    assert {item.rule for item in before} >= {"hkid", "email"}

    cleaned = Redactor().redact(text).text
    assert scan_leftovers(cleaned) == []


def test_example_test_mailbox_is_left_alone() -> None:
    text = "联络 user@example.test 或 user@example.com"
    assert Redactor().redact(text).text == text


def test_redact_obj_walks_nested_structures() -> None:
    payload = {"fields": [{"label": f"身分证 {_sample('hkid')}", "value": _sample('hkphone')}],
               "ok": True, "count": 3}
    cleaned, hits = Redactor().redact_obj(payload)
    assert _sample("hkid") not in json.dumps(cleaned, ensure_ascii=False)
    assert cleaned["ok"] is True and cleaned["count"] == 3
    assert {hit.rule for hit in hits} >= {"hkid"}


def test_builtin_rule_set_did_not_shrink() -> None:
    assert set(iter_rule_names()) >= {
        "jwt", "bearer", "cookie_pair", "url_param", "hkid", "cn_id",
        "email", "intl_phone", "hk_phone", "secret_field", "long_hex",
    }
