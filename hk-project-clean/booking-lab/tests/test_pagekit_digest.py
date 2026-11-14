# -*- coding: utf-8 -*-
"""digest.py 的离线用例：摘要够用、够小、外发物里没有个人资料。

定位
----
用一份手写快照代替真浏览器，验证摘要的结构、截断口径与外发安全性。

输入输出
--------
输入是快照 dict 与临时词表；输出是摘要 dict、Markdown 文本与两个落盘文件。

不变量
------
· 外发物（Markdown）里不出现样例身分证、电话、查询串取值
· 摘要规模字段与实际列表长度一致
· 缺失字段进 ``notes``，不静默当成空页面

失败路径
--------
正文超限 → 截断并记录丢弃行数；快照缺键 → 记 note。
"""

from __future__ import annotations

import json
from pathlib import Path

from pagekit.digest import DigestOptions, build_digest, render_markdown, write_digest
from pagekit.redact import Redactor, TermTable


def _pii(kind: str) -> str:
    if kind == "hkid":
        return "A" + "123456" + "(3)"
    if kind == "hkphone":
        return "2804" + " " + "2600"
    raise KeyError(kind)


TERMS = TermTable(hosts=[("abs3.example.test", "<SITE_HOST>")],
                  literals=[("示例发证机关", "<ISSUER>")])


def _snapshot() -> dict:
    return {
        "phase": "home",
        "url": "https://abs3.example.test/?lang=sc&token=abc123",
        "title": "ABSDI",
        "status": 200,
        "captured_at": "2026-11-15T10:00:00+08:00",
        "headings": [{"level": 1, "text": "预约免试签发正式驾驶执照"},
                     {"level": 2, "text": "使用条款"}],
        "buttons": [{"label": "预约", "selector": "button", "disabled": False}],
        "links": [{"label": "常见问题", "href": "https://abs3.example.test/faq?sig=deadbeef"}],
        "fields": [{"name": "idNo", "type": "text", "label": f"身分证号码 {_pii('hkid')}",
                    "required": True, "maxlength": "10", "options": [],
                    "selector": "input[name=idNo]"}],
        "forms": [{"selector": "form", "action": "/submit", "method": "POST", "field_count": 1}],
        "requests": [{"method": "GET", "url": "https://abs3.example.test/spa-api/queue?c=1&e=2",
                      "status": 200, "kind": "fetch"}],
        "storage": {"local": ["i18nextLng"], "session": ["actionType"],
                    "cookie_names": ["HWWAFSESID"]},
        "console": [],
        "text": f"网上预约\n热线 {_pii('hkphone')}\n示例发证机关 只办理一份申请",
        "screenshot": "runs/pagekit/20261115-100000/home.png",
    }


def test_digest_keeps_structure_and_drops_personal_data() -> None:
    digest = build_digest(_snapshot(), Redactor(TERMS), DigestOptions(phase="home"))
    blob = json.dumps(digest, ensure_ascii=False)

    assert _pii("hkid") not in blob
    assert _pii("hkphone") not in blob
    assert "abc123" not in blob and "deadbeef" not in blob
    assert "abs3.example.test" not in blob

    assert digest["stats"]["headings"] == 2
    assert digest["stats"]["fields"] == 1
    assert digest["stats"]["buttons"] == 1
    assert digest["stats"]["links"] == 1
    assert digest["stats"]["redactions"], "应记录脱敏计数"
    assert digest["meta"]["host"].endswith("/") or "<SITE_HOST>" in digest["meta"]["host"]


def test_markdown_is_the_shareable_artifact() -> None:
    digest = build_digest(_snapshot(), Redactor(TERMS), DigestOptions(phase="home"))
    md = render_markdown(digest)

    for section in ("# 页面摘要：home", "## 1. 大纲", "## 2. 表单字段",
                    "## 3. 按钮", "## 4. 链接（只留路径）", "## 5. 请求",
                    "## 6. 正文"):
        assert section in md
    assert _pii("hkid") not in md
    assert _pii("hkphone") not in md
    assert "示例发证机关" not in md, "口径词不能出现在脱敏统计行里"
    assert "<HKID>" in md and "<PHONE>" in md
    assert "<ISSUER>" in md


def test_long_text_is_truncated_with_a_note() -> None:
    snapshot = _snapshot()
    snapshot["text"] = "\n".join(f"第 {i} 行内容" for i in range(300))
    digest = build_digest(snapshot, Redactor(TERMS),
                          DigestOptions(phase="home", max_lines=50))
    assert any("截断" in note for note in digest["notes"])
    assert digest["text"].count("\n") < 60


def test_missing_sections_are_reported_not_silent() -> None:
    digest = build_digest({}, Redactor(TERMS), DigestOptions(phase="empty"))
    assert digest["stats"]["fields"] == 0
    assert any("快照缺字段" in note for note in digest["notes"])


def test_write_digest_emits_json_and_markdown(tmp_path: Path) -> None:
    digest = build_digest(_snapshot(), Redactor(TERMS), DigestOptions(phase="home"))
    md_path = write_digest(digest, tmp_path / "home.digest.json")
    assert md_path.exists() and md_path.suffix == ".md"
    assert (tmp_path / "home.digest.json").exists()
    assert "<SITE_HOST>" in md_path.read_text(encoding="utf-8")
