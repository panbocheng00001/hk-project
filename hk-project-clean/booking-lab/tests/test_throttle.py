# -*- coding: utf-8 -*-
"""throttle 的离线用例。

定位
----
用假时钟验证四道闸门各自的行为：硬下限、分布间隔、每分钟预算、连续被挡的退避与熔断。

输入输出
--------
构造 Throttle 时注入 clock/sleeper/rng；断言只读假时钟的累计时间与内部计数。

不变量
------
任何一次 wait() 的间隔不小于 min_interval；滑动窗口满额时一定会等到最早一条过期；
连续被挡达到 trip_after 时抛 ThrottleTripped。

失败路径
--------
熔断后继续调用 wait() 仍抛 ThrottleTripped；on_ok 清零连续计数。
"""
from __future__ import annotations

import random

import pytest

from throttle import Throttle, ThrottleTripped


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)


def _th(**kw) -> tuple[Throttle, FakeClock]:
    c = FakeClock()
    base = dict(min_interval=2.0, log=lambda *a, **k: None,
                clock=c.now, sleeper=c.sleep, rng=random.Random(7))
    base.update(kw)
    return Throttle(**base), c


def test_wait_never_below_floor_and_varies():
    th, c = _th(min_interval=2.0, interval_sigma=0.4, pause_prob=0.0)
    th.wait()
    gaps = []
    for _ in range(120):
        before = c.t
        th.wait()
        gaps.append(c.t - before)
    assert min(gaps) >= 2.0 - 1e-9
    assert max(gaps) > min(gaps)           # 有抖动，不是固定 sleep
    assert th.waits == 121


def test_pause_adds_long_gap():
    th, c = _th(min_interval=1.0, interval_sigma=0.0, pause_prob=1.0,
                pause_range=(4.0, 4.0))
    th.wait()
    before = c.t
    th.wait()
    assert c.t - before >= 4.0


def test_minute_budget_waits_for_window():
    th, c = _th(min_interval=0.05, interval_sigma=0.0, pause_prob=0.0,
                max_per_minute=2)
    start = c.t
    th.wait()
    th.wait()
    th.wait()
    assert c.t - start >= 55.0                 # 第三条必须等窗口腾位置
    assert len(th._window) <= 2
    assert th.status()["max_per_minute"] == 2


def test_block_backoff_then_trip():
    th, _c = _th(trip_after=2, backoff_base=10.0, backoff_cap=60.0)
    th.on_block(412)
    assert th.hits == 1 and th.sleep_for == 10.0 and not th.tripped
    with pytest.raises(ThrottleTripped):
        th.on_block(429)
    assert th.tripped
    with pytest.raises(ThrottleTripped):
        th.wait()


def test_ok_resets_streak():
    th, _c = _th(trip_after=3)
    th.on_block(403)
    th.on_ok()
    assert th.hits == 0
    th.on_block(405)
    assert th.hits == 1
