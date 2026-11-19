#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
throttle.py —— 全局节流与熔断（今天把自己打死的教训，写成代码）
===========================================================================
2026-11-11 的事实：
  · 15:19–15:41  家宽 curl → 200（一切正常）
  · 15:45 起     → 412（WAF 挑战）
  · 16:41 起     → 412 与 000（连接超时）交替
  · 17:00 放号时 → 连发 3 次全 000，彻底进不去

原因不是"目标挂了"，是我在 40 分钟里跑了 pool_runner dry-run、flow_verify 多轮、
pool_multi、goto_queue、goto_step1 …… 几十次 enqueue + 大量 412 重试，
把唯一能过的家宽出口打进了限速。

所以：**节流不是可选项**。任何打 abs3/abs4 的动作都必须走这个闸。

规则（四道）
------------
1. `min_interval`  —— 同一出口两次请求的硬下限（默认 3.0s；rush 阶段可降到 1.5s）
      实际间隔 = 下限 × (1 + 正态抖动)，再按概率叠加一次"看页面"的长停顿。
      固定 sleep 的间隔分布是一个尖峰，正态 + 偶发长停顿更接近浏览行为。
2. `max_per_minute` —— 每分钟请求数上限（0 = 不限）。按滑动 60 秒窗口计，
      到顶就睡到窗口里最早那条过期。文档口径是「不要卡着阈值，留 30% 余量」。
3. `backoff`       —— 命中 403/405/412/429 后的指数退避（60 → 120 → 240 → 480s）
4. `trip_after`    —— 连续被挡达到 N 次就**熔断**（默认 3），之后整个进程只读不写

用法
----
    from throttle import Throttle
    th = Throttle(min_interval=3.0, max_per_minute=18)

    th.wait()              # 每次请求前
    r = sess.get(url)
    if r.status_code in (403, 405, 412, 429):
        th.on_block(r.status_code)   # 记录 + 退避（必要时抛 TripError）
    else:
        th.on_ok()
"""

from __future__ import annotations

import random
import time
from collections import deque
from typing import Callable, Deque


class ThrottleTripped(RuntimeError):
    """熔断：继续打只会让封禁更深，必须停。"""


class Throttle:
    def __init__(self, min_interval: float = 3.0, trip_after: int = 3,
                 backoff_base: float = 60.0, backoff_cap: float = 480.0,
                 log=None, *,
                 interval_sigma: float = 0.25,
                 pause_prob: float = 0.08,
                 pause_range: tuple[float, float] = (4.0, 12.0),
                 max_per_minute: float = 0.0,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None):
        self.min_interval = min_interval
        self.trip_after = trip_after
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.log = log or (lambda m, lv="·": print(m, flush=True))
        self.interval_sigma = max(0.0, interval_sigma)
        self.pause_prob = min(max(pause_prob, 0.0), 1.0)
        self.pause_range = pause_range
        self.max_per_minute = max(0.0, max_per_minute)
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self._last = 0.0
        self._window: Deque[float] = deque()
        self.hits = 0          # 连续被挡计数
        self.tripped = False
        self.sleep_for = 0.0
        self.waits = 0         # 累计请求次数（含首次）

    # ---------------------------------------------------------------- 闸门
    def wait(self) -> None:
        if self.tripped:
            raise ThrottleTripped("已熔断（连续被挡达到阈值），不再发请求")
        self._spend_budget()
        gap = self._clock() - self._last
        need = self._draw_interval() - gap
        if need > 0:
            self._sleep(need)
        self._last = self._clock()
        self._window.append(self._last)
        self.waits += 1

    def _draw_interval(self) -> float:
        """一次请求间隔：硬下限 + 正态抖动 + 小概率长停顿。"""
        jitter = self._rng.gauss(0.0, self.interval_sigma)
        value = self.min_interval * (1.0 + jitter)
        if self._rng.random() < self.pause_prob:
            lo, hi = self.pause_range
            value += self._rng.uniform(lo, hi)
        return max(self.min_interval, value)

    def _spend_budget(self) -> None:
        """滑动 60 秒窗口限流；窗口满了就睡到最早一条过期。"""
        if self.max_per_minute <= 0:
            return
        while True:
            now = self._clock()
            while self._window and self._window[0] <= now - 60.0:
                self._window.popleft()
            if len(self._window) < self.max_per_minute:
                return
            wait = self._window[0] + 60.0 - now + 0.05
            self.log(f"   本分钟已发 {len(self._window)} 次（上限 {self.max_per_minute:g}），"
                     f"等 {wait:.1f}s", "~")
            self._sleep(max(0.05, wait))

    # ---------------------------------------------------------------- 失败记账
    def on_block(self, status: int = 412) -> None:
        """403/405/412/429 都走这里：计数、退避，达到阈值熔断。"""
        self.hits += 1
        self.log(f"   节流：第 {self.hits}/{self.trip_after} 次 {status}", "!")
        if self.hits >= self.trip_after:
            self.tripped = True
            self.log("   !! 连续被挡达到阈值 → 熔断，停止一切请求（换出口或等冷却）", "!!")
            raise ThrottleTripped("连续被挡达到阈值")
        self.sleep_for = min(self.backoff_base * (2 ** (self.hits - 1)), self.backoff_cap)
        self.log(f"   退避 {int(self.sleep_for)}s 后再试（别连打）", "~")
        self._sleep(self.sleep_for)

    def on_412(self) -> None:
        """兼容旧调用点：只处理 412。"""
        self.on_block(412)

    def on_ok(self) -> None:
        if self.hits:
            self.log(f"   恢复正常（此前连续 {self.hits} 次被挡已清零）", "√")
        self.hits = 0

    # ---------------------------------------------------------------- 状态
    def status(self) -> dict:
        return {"min_interval": self.min_interval, "hits": self.hits,
                "tripped": self.tripped, "trip_after": self.trip_after,
                "max_per_minute": self.max_per_minute,
                "window": len(self._window), "waits": self.waits}
