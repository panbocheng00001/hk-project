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

规则（三个阈值）
----------------
1. `min_interval`  —— 同一出口两次请求的最小间隔（默认 3.0s；rush 阶段可降到 1.5s）
2. `backoff`       —— 命中 412 后的指数退避（60 → 120 → 240 → 480s）
3. `trip_after`    —— 连续 412 达到 N 次就**熔断**（默认 3），之后整个进程只读不写

用法
----
    from throttle import Throttle
    th = Throttle(min_interval=3.0)

    th.wait()              # 每次请求前
    r = sess.get(url)
    if r.status_code == 412:
        th.on_412()        # 记录 + 退避（必要时抛 TripError）
    else:
        th.on_ok()
"""

from __future__ import annotations

import random
import time


class ThrottleTripped(RuntimeError):
    """熔断：继续打只会让封禁更深，必须停。"""


class Throttle:
    def __init__(self, min_interval: float = 3.0, trip_after: int = 3,
                 backoff_base: float = 60.0, backoff_cap: float = 480.0,
                 log=None):
        self.min_interval = min_interval
        self.trip_after = trip_after
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.log = log or (lambda m, lv="·": print(m, flush=True))
        self._last = 0.0
        self.hits = 0          # 连续 412 计数
        self.tripped = False
        self.sleep_for = 0.0

    # ---------------------------------------------------------------- 闸门
    def wait(self) -> None:
        if self.tripped:
            raise ThrottleTripped("已熔断（连续 412 达到阈值），不再发请求")
        gap = time.time() - self._last
        need = self.min_interval - gap
        if need > 0:
            time.sleep(need + random.uniform(0, 0.4))
        self._last = time.time()

    def on_412(self) -> None:
        self.hits += 1
        self.log(f"   节流：第 {self.hits}/{self.trip_after} 次 412", "!")
        if self.hits >= self.trip_after:
            self.tripped = True
            self.log("   !! 连续 412 达到阈值 → 熔断，停止一切请求（换出口或等冷却）", "!!")
            raise ThrottleTripped("连续 412 达到阈值")
        self.sleep_for = min(self.backoff_base * (2 ** (self.hits - 1)), self.backoff_cap)
        self.log(f"   退避 {int(self.sleep_for)}s 后再试（别连打）", "~")
        time.sleep(self.sleep_for)

    def on_ok(self) -> None:
        if self.hits:
            self.log(f"   恢复正常（此前连续 {self.hits} 次 412 已清零）", "√")
        self.hits = 0

    # ---------------------------------------------------------------- 状态
    def status(self) -> dict:
        return {"min_interval": self.min_interval, "hits": self.hits,
                "tripped": self.tripped, "trip_after": self.trip_after}
