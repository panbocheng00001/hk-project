# -*- coding: utf-8 -*-
"""验证码状态机的单元测试。

不依赖浏览器、不依赖网络、不 sleep —— 用假时钟 + FakePage 全离线跑。
这是"这套逻辑能被别人审"的前提：审的人不需要跑起来才能看懂行为。
"""
from __future__ import annotations

import pytest

from captcha_flow import (
    CaptchaKind, ChallengeInfo, FakePage, Guard, Outcome, Policy, Result, decide,
)
from captcha_flow.detect import detect_from_payload
from captcha_flow.policy import Action

# 两组常用假数据
NOT_SOLVED = {"tokenLen": 0, "visible": True}
IS_SOLVED = {"tokenLen": 32, "visible": False}
NO_TOKEN_FIELD = {"tokenLen": -1, "visible": False}


class Clock:
    """假时钟：sleep 只推进时间，不真等。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def make_guard(detect, solved, policy=None, clicks=1, click_ok=True):
    clk = Clock()
    fp = FakePage(detect=detect, solved=solved, clicks=clicks, click_ok=click_ok)
    g = Guard(fp, policy or Policy(), log=lambda _m: None,
              clock=clk, sleep=clk.sleep)
    return g, fp, clk


# ------------------------------------------------------------------ detect
@pytest.mark.parametrize("payload,expected", [
    ({"kind": "none"}, CaptchaKind.NONE),
    ({"kind": "token_only", "tokenLen": 0, "hasSdk": True}, CaptchaKind.TOKEN_ONLY),
    ({"kind": "checkbox"}, CaptchaKind.CHECKBOX),
    ({"kind": "slider"}, CaptchaKind.SLIDER),
    ({"kind": "image_code"}, CaptchaKind.IMAGE_CODE),
    ({"kind": "weird_new_thing"}, CaptchaKind.UNKNOWN),
])
def test_detect_from_payload_maps_kind(payload, expected):
    assert detect_from_payload(payload).kind is expected


def test_detect_from_payload_handles_empty():
    assert detect_from_payload(None).kind is CaptchaKind.UNKNOWN


def test_challenge_info_summary_is_readable():
    info = ChallengeInfo(kind=CaptchaKind.SLIDER, detail=("canvas", "拖动"))
    assert "slider" in info.summary


def test_solved_probe_detection_helper():
    from captcha_flow.adapters import _is_solved_probe
    from captcha_flow.detect import DETECT_JS, SOLVED_JS
    assert _is_solved_probe(SOLVED_JS)
    assert not _is_solved_probe(DETECT_JS)


# ------------------------------------------------------------------ policy
def test_policy_passes_through_when_nothing_present():
    assert decide(ChallengeInfo(kind=CaptchaKind.NONE)).action is Action.PASS_THROUGH


def test_policy_waits_when_sdk_present_but_token_empty():
    info = ChallengeInfo(kind=CaptchaKind.NONE, token_len=0, has_sdk=True)
    assert decide(info).action is Action.WAIT_TOKEN


def test_policy_hands_slider_to_human():
    """滑块是内容判断题，策略层必须直接交人工，不允许任何自动求解路径。"""
    assert decide(ChallengeInfo(kind=CaptchaKind.SLIDER)).action is Action.HAND_TO_HUMAN


@pytest.mark.parametrize("kind", [CaptchaKind.IMAGE_CODE, CaptchaKind.IMAGE_GRID,
                                  CaptchaKind.UNKNOWN])
def test_policy_hands_judgement_kinds_to_human(kind):
    assert decide(ChallengeInfo(kind=kind)).action is Action.HAND_TO_HUMAN


def test_policy_auto_budget_caps_automation():
    p = Policy(max_auto=2)
    info = ChallengeInfo(kind=CaptchaKind.CHECKBOX)
    assert decide(info, p, auto_used=0).action is Action.CLICK_CHECKBOX
    assert decide(info, p, auto_used=2).action is Action.HAND_TO_HUMAN


def test_policy_is_pure():
    info = ChallengeInfo(kind=CaptchaKind.CHECKBOX)
    assert decide(info) == decide(info)


def test_no_captcha_kind_is_auto_capable():
    """needs_human / can_auto 两个谓词必须互斥且覆盖全部形态。"""
    for kind in CaptchaKind:
        if kind in (CaptchaKind.NONE,):
            continue
        assert not (kind.needs_human and kind.can_auto), kind


# ------------------------------------------------------------------ guard
def test_no_challenge_passes_through_without_clicking():
    g, fp, _ = make_guard([{"kind": "none"}], [NO_TOKEN_FIELD])
    out = g.run("w1")
    assert out.result is Result.NO_CHALLENGE
    assert out.passed
    assert fp.n_click == 0


def test_no_challenge_with_empty_token_waits():
    g, fp, clk = make_guard(
        [{"kind": "none", "tokenLen": 0, "hasSdk": True}],
        [NOT_SOLVED, NOT_SOLVED, IS_SOLVED],
    )
    out = g.run("w1")
    assert out.passed
    assert clk.t > 0, "token 未生成时应轮询等待，而不是立即放行"


def test_checkbox_click_then_pass():
    g, fp, _ = make_guard(
        [{"kind": "checkbox"}, {"kind": "none"}],
        [NOT_SOLVED, IS_SOLVED],
    )
    out = g.run("w1")
    assert out.result is Result.PASSED_AUTO
    assert fp.n_click == 1


def test_checkbox_click_fails_then_human_passes():
    g, fp, _ = make_guard(
        [{"kind": "checkbox"}, {"kind": "none"}],
        [NOT_SOLVED, NOT_SOLVED, IS_SOLVED],
        clicks=0,
    )
    out = g.run("w1")
    assert out.result is Result.PASSED_HUMAN
    assert fp.fronted >= 1


def test_slider_never_triggers_automatic_action():
    """最关键的一条：滑块出现时不能有任何自动点击发生。"""
    g, fp, _ = make_guard(
        [{"kind": "slider"}, {"kind": "none"}],
        [NOT_SOLVED, IS_SOLVED],
    )
    out = g.run("w1")
    assert out.result is Result.PASSED_HUMAN
    assert fp.n_click == 0, "滑块路径不得产生任何自动点击"


def test_detect_exception_is_treated_as_unknown_and_hands_off():
    g, fp, _ = make_guard(
        [RuntimeError("page navigating"), {"kind": "none"}],
        [NOT_SOLVED, IS_SOLVED],
        policy=Policy(human_wait_s=10, poll_s=1),
    )
    out = g.run("w1")
    assert out.result is Result.PASSED_HUMAN
    assert fp.fronted >= 1


def test_token_never_appears_degrades_to_human_then_timeout():
    g, fp, clk = make_guard(
        [{"kind": "token_only", "tokenLen": 0, "hasSdk": True}],
        [NOT_SOLVED],
        policy=Policy(token_wait_s=4, human_wait_s=6, renotify_every_s=3, poll_s=1),
    )
    out = g.run("w1")
    assert out.result is Result.TIMEOUT
    assert fp.fronted >= 1
    assert out.auto_attempts <= Policy().max_auto


def test_human_timeout_reports_elapsed_and_events():
    g, fp, clk = make_guard(
        [{"kind": "image_grid"}], [NOT_SOLVED],
        policy=Policy(human_wait_s=6, renotify_every_s=3, poll_s=1),
    )
    out = g.run("w1")
    assert out.result is Result.TIMEOUT
    assert out.waited_s >= 5
    assert any("handoff" in e for e in out.events)
    assert any("timeout" in e for e in out.events)


def test_notify_callback_fires_on_handoff():
    calls = []
    clk = Clock()
    fp = FakePage(detect=[{"kind": "image_code"}], solved=[NOT_SOLVED, IS_SOLVED])
    g = Guard(fp, Policy(human_wait_s=5, poll_s=1), log=lambda _m: None,
              notify=lambda kind, waited: calls.append((kind, waited)),
              clock=clk, sleep=clk.sleep)
    assert g.run("w1").passed
    assert calls and calls[0][0] == "image_code"


def test_events_are_emitted_for_traceability():
    seen = []
    clk = Clock()
    fp = FakePage(detect=[{"kind": "slider"}], solved=[NOT_SOLVED, IS_SOLVED])
    g = Guard(fp, Policy(human_wait_s=5, poll_s=1), log=lambda _m: None,
              on_event=seen.append, clock=clk, sleep=clk.sleep)
    out = g.run("w1")
    assert len(seen) >= 2
    assert out.events


def test_outcome_bool_reflects_pass():
    assert bool(Outcome(result=Result.PASSED_HUMAN))
    assert bool(Outcome(result=Result.PASSED_AUTO))
    assert bool(Outcome(result=Result.NO_CHALLENGE))
    assert not bool(Outcome(result=Result.TIMEOUT))
    assert not bool(Outcome(result=Result.ABORTED))


def test_hard_deadline_override_is_honoured():
    """调用方给的 deadline 优先：即使 policy 里配了更长的等待也不能越过。"""
    g, fp, clk = make_guard(
        [{"kind": "slider"}], [NOT_SOLVED],
        policy=Policy(human_wait_s=999, poll_s=1),
    )
    out = g.run("w1", deadline_s=5)
    assert out.result is Result.TIMEOUT
    assert out.waited_s <= 8
