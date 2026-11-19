# -*- coding: utf-8 -*-
"""waf_profile 的离线用例。

定位
----
锁定三件事：主版本号在 UA / sec-ch-ua / TLS 画像名三处同源；头部顺序只由入参决定；
身份不自洽时 audit 能报出来。

输入输出
--------
纯函数调用，不联网、不启浏览器、不读时钟；需要环境变量时用 monkeypatch 注入。

不变量
------
同一 profile 的 ua/tls_name/sec_ch_ua 主版本号相同；header_plan 连续两次调用逐项相等。

失败路径
--------
未知 kind 抛 ValueError；不受支持的画像名由 session_kwargs 返回空字典而不是抛错。
"""
from __future__ import annotations

import pytest

import waf_profile as wp


def test_default_profile_is_self_consistent():
    p = wp.profile()
    assert p.major == wp.DEFAULT_MAJOR
    assert f"Chrome/{p.major}.0.0.0" in p.ua
    assert p.tls_name == f"chrome{p.major}"
    assert f'v="{p.major}"' in p.sec_ch_ua
    assert p.accept_language.startswith("zh-CN")


def test_major_from_env(monkeypatch):
    monkeypatch.setenv("BOOKING_CHROME_MAJOR", "131")
    p = wp.profile()
    assert p.major == 131
    assert p.tls_name == "chrome131"
    assert 'v="131"' in p.sec_ch_ua


def test_major_from_user_agent():
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
    p = wp.from_user_agent(ua)
    assert p.major == 156
    assert p.ua == ua


def test_bad_major_falls_back():
    assert wp.profile("not-a-number").major == wp.DEFAULT_MAJOR
    assert wp.profile("12").major == wp.DEFAULT_MAJOR


def test_document_plan_order():
    p = wp.profile(131)
    names = [k for k, _ in wp.header_plan("document", p, referer="https://abs3.example.test/")]
    assert names[:3] == ["sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"]
    assert names.index("User-Agent") < names.index("Accept")
    assert names.index("Accept") < names.index("Sec-Fetch-Site")
    assert names.index("Sec-Fetch-Dest") < names.index("Referer")
    assert names[-1] == "Accept-Language"
    assert "Origin" not in names


def test_form_origin_only_cross_site():
    p = wp.profile(131)
    same = dict(wp.header_plan("form", p, origin="https://abs1.example.test"))
    cross = dict(wp.header_plan("form", p, origin="https://abs1.example.test", cross_site=True))
    assert "Origin" not in same
    assert cross["Origin"] == "https://abs1.example.test"
    assert cross["Sec-Fetch-Site"] == "cross-site"
    assert "Content-Type" not in cross


def test_json_plan_has_no_upgrade_and_empty_dest():
    p = wp.profile(131)
    plan = dict(wp.header_plan("json", p, content_type="application/json",
                              referer="https://abs3.example.test/"))
    assert "Upgrade-Insecure-Requests" not in plan
    assert plan["Sec-Fetch-Dest"] == "empty"
    assert plan["Sec-Fetch-Mode"] == "cors"
    assert plan["Content-Type"] == "application/json"


def test_plan_is_deterministic():
    p = wp.profile(136)
    assert wp.header_plan("document", p) == wp.header_plan("document", p)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        wp.header_plan("websocket", wp.profile())


def test_apply_headers_clears_and_keeps_order():
    class Bag:
        def __init__(self):
            self.data = {}

        def clear(self):
            self.data.clear()

        def __setitem__(self, k, v):
            self.data[k] = v

        def keys(self):
            return list(self.data)

    bag = Bag()
    bag["Stale"] = "1"
    wp.apply_headers(bag, [("b", "2"), ("a", "1"), ("c", "")])
    assert bag.keys() == ["b", "a"]


def test_session_kwargs_respects_support():
    p = wp.profile(136)
    assert wp.session_kwargs(p) == {"impersonate": "chrome136"}
    assert wp.session_kwargs(p, enabled=False) == {}
    assert wp.session_kwargs(p, name="chrome999") == {}


def test_resolve_tls_off_and_default(monkeypatch):
    monkeypatch.delenv("BOOKING_TLS_PROFILE", raising=False)
    assert wp.resolve_tls("none") == ""
    assert wp.resolve_tls("") == wp.profile().tls_name
    monkeypatch.setenv("BOOKING_TLS_PROFILE", "chrome131")
    assert wp.resolve_tls("") == "chrome131"


def test_audit_reports_each_mismatch():
    p = wp.profile(136)
    issues = wp.audit(p, tls_name="chrome131", ua=wp.profile(156).ua,
                      locale="zh-HK", timezone="Asia/Hong_Kong")
    assert len(issues) == 4
    assert wp.audit(p, tls_name="chrome136", ua=p.ua,
                    locale=p.locale, timezone=p.timezone) == []


def test_audit_notes_missing_tls():
    p = wp.profile(136)
    assert any("未指定 TLS" in s for s in wp.audit(p, tls_name=""))
