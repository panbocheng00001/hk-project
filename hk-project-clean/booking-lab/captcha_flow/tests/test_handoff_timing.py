# -*- coding: utf-8 -*-
"""人工交接的耗时 / 再提醒节奏测试。

全部用假时钟 + FakePage 子类：人什么时候完成由假时钟决定，而不是由探针调用次数决定，
所以"等了多久""提醒了几次"可以直接按时间断言。不 sleep、不开浏览器、不写真实 journal。
"""
from __future__ import annotations

import math

import pytest

import captcha
from captcha_flow import FakePage, Guard, Policy, Result
from captcha_flow.adapters import _is_solved_probe

NOT_SOLVED = {"tokenLen": 0, "visible": True}
IS_SOLVED = {"tokenLen": 32, "visible": False}
CAP_S = 120.0


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class TimedPage(FakePage):
    """验证码常驻；假时钟走到 solve_at 之后 solved 探针才返回已通过。

    calls 记录有序的 (动作, 时刻)，用于断言 bring_to_front / notify 的先后。
    """

    def __init__(self, clock: Clock, solve_at: float | None, kind: str = "slider",
                 clicks: int = 0) -> None:
        super().__init__(detect=[{"kind": kind}], clicks=clicks)
        self.clock = clock
        self.solve_at = solve_at
        self.calls: list[tuple[str, float]] = []

    def evaluate(self, js: str):
        if _is_solved_probe(js):
            self.n_solved += 1
            done = self.solve_at is not None and self.clock() >= self.solve_at
            self.calls.append(("solved" if done else "probe", self.clock()))
            return IS_SOLVED if done else NOT_SOLVED
        return super().evaluate(js)

    def bring_to_front(self) -> None:
        super().bring_to_front()
        self.calls.append(("front", self.clock()))


def run(policy: Policy, solve_at: float | None, kind: str = "slider", clicks: int = 0):
    clk = Clock()
    page = TimedPage(clk, solve_at, kind=kind, clicks=clicks)
    written: list[tuple[str, dict]] = []
    rec = captcha.HandoffRecorder("w1", write=lambda ev, **f: written.append((ev, f)),
                                  log=lambda _m: None)
    g = Guard(page, policy, log=lambda _m: None, on_event=rec.on_event,
              notify=lambda kind, waited: page.calls.append(("notify", clk())),
              clock=clk, sleep=clk.sleep)
    out = g.run("w1")
    done = rec.finish(out, "solved" if out.passed else "timeout")
    return out, page, rec, done, written


def schedule(p: Policy) -> list[float]:
    """独立于实现的再提醒计划：相对交接时刻的偏移量。"""
    out, t = [], 0.0
    for k in range(1, p.renotify_max_n + 1):
        t += min(max(CAP_S, p.renotify_every_s),
                 p.renotify_every_s * p.renotify_backoff ** (k - 1))
        out.append(t)
    return out


def renotify_events(written):
    return [f for ev, f in written if ev == "renotify"]


FAST = Policy(human_wait_s=600, renotify_every_s=10, renotify_backoff=2.0,
              renotify_max_n=3, poll_s=1.0)          # 计划：10 / 30 / 70，之后不再叫


# ------------------------------------------------------------------ 断言 1：waited_s
@pytest.mark.parametrize("solve_at", [0.5, 7.0, 44.0, 150.0])
def test_waited_equals_completion_minus_first_handoff(solve_at):
    p = Policy(poll_s=1.5)
    out, page, rec, done, _ = run(p, solve_at)
    assert out.result is Result.PASSED_HUMAN
    assert rec.t_appear == 0.0
    assert done["result"] == "solved" and done["timeout"] is False
    assert solve_at <= done["waited_s"] <= solve_at + p.poll_s


def test_waited_counts_from_handoff_not_from_run_start():
    """勾选框点了没过再交人：t_appear 是交接时刻（点击 + 3s 复核之后），不是 run 开始。"""
    p = Policy(poll_s=1.0)
    out, page, rec, done, _ = run(p, solve_at=30.0, kind="checkbox", clicks=1)
    assert out.result is Result.PASSED_HUMAN
    assert page.n_click == 1
    assert rec.t_appear == pytest.approx(3.0)
    assert done["waited_s"] == pytest.approx(30.0 - 3.0, abs=p.poll_s)
    assert done["kind"] == "checkbox"


# ------------------------------------------------------------------ 断言 2：再提醒次数
@pytest.mark.parametrize("solve_at", [5.5, 15.5, 45.5, 95.5, 400.0])
def test_renotify_count_follows_backoff_and_cap(solve_at):
    out, page, rec, done, written = run(FAST, solve_at)
    expected = min(FAST.renotify_max_n, sum(1 for s in schedule(FAST) if s < solve_at))
    assert done["renotify_n"] == expected
    assert len(renotify_events(written)) == expected
    assert [f["n"] for f in renotify_events(written)] == list(range(1, expected + 1))


def test_renotify_times_match_schedule_and_stop_at_max():
    p = Policy(human_wait_s=1000, poll_s=1.0)         # 默认 45s / 1.6 倍 / 5 次
    out, page, rec, done, written = run(p, solve_at=None)
    times = [f["waited_s"] for f in renotify_events(written)]
    plan = schedule(p)
    assert plan == pytest.approx([45.0, 117.0, 232.2, 352.2, 472.2])
    assert len(times) == p.renotify_max_n
    for got, want in zip(times, plan):
        assert want <= got < want + p.poll_s
    gaps = [b - a for a, b in zip([0.0] + plan, plan)]
    assert max(gaps) <= CAP_S + 1e-9
    # 第 5 次之后到 1000s 超时都不再叫
    notifies = [t for what, t in page.calls if what == "notify"]
    assert len(notifies) == 1 + p.renotify_max_n
    assert notifies[-1] < plan[-1] + p.poll_s


def test_default_policy_renotifies_twice_within_180s():
    out, page, rec, done, written = run(Policy(), solve_at=None)
    assert done["renotify_n"] == 2
    assert [round(f["waited_s"]) for f in renotify_events(written)] == [45, 117]


@pytest.mark.parametrize("policy", [
    Policy(human_wait_s=120, renotify_every_s=0, poll_s=1.0),
    Policy(human_wait_s=120, renotify_max_n=0, poll_s=1.0),
])
def test_renotify_can_be_disabled(policy):
    out, page, rec, done, written = run(policy, solve_at=None)
    assert done["renotify_n"] == 0
    assert renotify_events(written) == []
    assert [w for w, _ in page.calls].count("notify") == 1


# ------------------------------------------------------------------ 断言 3：完成后静默
@pytest.mark.parametrize("solve_at", [10.0, 30.0, 50.0])
def test_nothing_fires_after_human_completes(solve_at):
    """含边界：完成时刻恰好等于计划提醒时刻，完成优先，不再提醒。"""
    out, page, rec, done, written = run(FAST, solve_at)
    assert out.result is Result.PASSED_HUMAN
    first_ok = next(i for i, (w, _) in enumerate(page.calls) if w == "solved")
    tail = [w for w, _ in page.calls[first_ok:]]
    assert "front" not in tail and "notify" not in tail
    assert all(f["waited_s"] < solve_at for f in renotify_events(written))
    assert done["renotify_n"] == sum(1 for s in schedule(FAST) if s < solve_at)


# ------------------------------------------------------------------ 断言 4：提醒前后抬窗口
def test_each_alert_is_wrapped_by_bring_to_front():
    out, page, rec, done, _ = run(FAST, solve_at=None)
    seq = [w for w, _ in page.calls if w in ("front", "notify")]
    idx = [i for i, w in enumerate(seq) if w == "notify"]
    assert len(idx) == 1 + FAST.renotify_max_n
    for i in idx:
        assert seq[i - 1] == "front" and seq[i + 1] == "front"
    assert page.fronted == 2 * len(idx)
    assert page.n_click == 0


# ------------------------------------------------------------------ 断言 5：超时
@pytest.mark.parametrize("policy", [
    Policy(),                                         # 180s，默认 poll 1.5
    Policy(human_wait_s=60, poll_s=1.5),
    Policy(human_wait_s=37, poll_s=2.0, renotify_every_s=5),
])
def test_timeout_returns_timeout_and_waits_human_wait_s(policy):
    out, page, rec, done, _ = run(policy, solve_at=None)
    assert out.result is Result.TIMEOUT
    assert done["result"] == "timeout" and done["timeout"] is True
    assert policy.human_wait_s <= done["waited_s"] < policy.human_wait_s + policy.poll_s
    assert done["renotify_n"] <= policy.renotify_max_n


def test_caller_deadline_still_caps_handoff():
    clk = Clock()
    page = TimedPage(clk, solve_at=None)
    g = Guard(page, Policy(human_wait_s=999, poll_s=1.0), log=lambda _m: None,
              clock=clk, sleep=clk.sleep)
    out = g.run("w1", deadline_s=30)
    assert out.result is Result.TIMEOUT
    assert out.waited_s == pytest.approx(30, abs=1.0)


# ------------------------------------------------------------------ 记录侧
def test_recorder_write_failure_never_raises():
    logs: list[str] = []

    def boom(ev, **f):
        raise OSError("disk full")

    clk = Clock()
    page = TimedPage(clk, solve_at=50.0)
    rec = captcha.HandoffRecorder("w1", write=boom, log=logs.append)
    g = Guard(page, FAST, log=lambda _m: None, on_event=rec.on_event,
              clock=clk, sleep=clk.sleep)
    out = g.run("w1")
    done = rec.finish(out, "solved")
    assert out.result is Result.PASSED_HUMAN
    assert done is not None and done["renotify_n"] == 2
    assert len(logs) == 3 and all("journal 写入失败" in m for m in logs)


def test_recorder_skips_when_no_handoff():
    clk = Clock()
    page = FakePage(detect=[{"kind": "none"}], solved=[{"tokenLen": -1, "visible": False}])
    written = []
    rec = captcha.HandoffRecorder("w1", write=lambda ev, **f: written.append(ev))
    out = Guard(page, Policy(), log=lambda _m: None, on_event=rec.on_event,
                clock=clk, sleep=clk.sleep).run("w1")
    assert out.result is Result.NO_CHALLENGE
    assert rec.finish(out, "none") is None
    assert written == []


def test_handoff_done_record_shape():
    out, page, rec, done, written = run(FAST, solve_at=45.5)
    ev, fields = written[-1]
    assert ev == "handoff_done"
    assert set(fields) == {"who", "kind", "waited_s", "result", "renotify_n", "timeout"}
    assert fields["who"] == "w1" and fields["kind"] == "slider"
    assert math.isclose(fields["waited_s"], 46.0)
