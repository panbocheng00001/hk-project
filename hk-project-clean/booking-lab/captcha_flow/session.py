# -*- coding: utf-8 -*-
"""session.py —— 状态机：把 Decision 执行到底

执行模型（单次 run）
--------------------
    loop:
        info = detect()
        if solved(): -> PASSED
        decision = decide(info)
        switch action:
            PASS_THROUGH -> return
            WAIT_TOKEN   -> 等 token，最多 token_wait_s，然后复核
            CLICK_CHECKBOX -> 点一次，等 3s，复核；失败则降级人工
            HAND_TO_HUMAN -> 抬窗口 + 通知 + 轮询，最多 human_wait_s；
                             期间按递增间隔再提醒，最多 renotify_max_n 次
            ABORT        -> return
        if 超过 deadline: -> TIMEOUT

所有时间、次数都走 Policy，不在代码里埋魔法数字。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .adapters import PageAdapter
from .detect import detect, solved
from .model import CaptchaKind, Outcome, Result
from .policy import Action, Decision, Policy, decide

Log = Callable[[str], None]

# 再提醒间隔的封顶值；若调用方配置的 renotify_every_s 更大，以配置为准
RENOTIFY_CAP_S = 120.0


@dataclass
class GuardEvent:
    """一次状态迁移，给上层做留痕 / 报告用。

    data 是给机器读的结构化字段（handoff: kind；renotify: n / waited_s；
    passed_human / timeout: waited_s / renotify_n），waited_s 从交接时刻起算。
    """

    at: float
    action: str
    detail: str
    data: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.at:.0f}s] {self.action}: {self.detail}"


class Guard:
    """验证码交互守卫。

    参数
    ----
    page       : PageAdapter（PlaywrightPage 或 FakePage）
    policy     : Policy，默认保守值
    log        : 日志函数，默认 print
    on_event   : 可选回调，收 GuardEvent（用于写证据包 / 上报）
    notify     : 可选回调，签名 notify(kind:str, waited_s:int)，用于喊人
    clock      : 可注入时钟，测试里替换成假时钟即可不 sleep
    sleep      : 可注入 sleep
    """

    def __init__(self, page: PageAdapter, policy: Policy | None = None,
                 log: Log = print, on_event: Callable[[GuardEvent], None] | None = None,
                 notify: Callable[[str, int], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.page = page
        self.policy = policy or Policy()
        self.log = log
        self.on_event = on_event
        self.notify = notify
        self.clock = clock
        self.sleep = sleep

    # ------------------------------------------------------------------ 内部
    def _emit(self, action: str, detail: str, t0: float, events: list[GuardEvent],
              **data) -> None:
        ev = GuardEvent(self.clock() - t0, action, detail, data)
        events.append(ev)
        self.log(str(ev))
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:
                pass

    def _notify(self, kind: str, waited: int = 0) -> None:
        if self.notify is None:
            return
        try:
            self.notify(kind, waited)
        except Exception:
            pass

    # ------------------------------------------------------------------ 主体
    def run(self, who: str = "", deadline_s: float | None = None) -> Outcome:
        p = self.policy
        t0 = self.clock()
        events: list[GuardEvent] = []
        outcome = Outcome(result=Result.NO_CHALLENGE, waited_s=0.0)
        auto_used = 0
        hard_deadline = t0 + (deadline_s if deadline_s is not None else
                              p.token_wait_s + p.human_wait_s)

        while True:
            info = detect(self.page)
            outcome.kind = info.kind

            if solved(self.page):
                result = Result.PASSED_AUTO if auto_used else Result.PASSED_HUMAN
                if not info.present and auto_used == 0:
                    result = Result.NO_CHALLENGE
                self._emit("solved", info.summary, t0, events)
                outcome.result = result
                outcome.waited_s = self.clock() - t0
                outcome.auto_attempts = auto_used
                outcome.events = [str(e) for e in events]
                return outcome

            decision = decide(info, p, auto_used)
            self._emit("decide", str(decision), t0, events)

            if decision.action is Action.PASS_THROUGH:
                outcome.result = Result.NO_CHALLENGE
                break

            if decision.action is Action.ABORT:
                outcome.result = Result.ABORTED
                break

            if decision.action is Action.WAIT_TOKEN:
                auto_used += 1
                self._emit("wait_token", f"最多 {p.token_wait_s:.0f}s", t0, events)
                self._wait_token(t0, p.token_wait_s, events)
                if self.clock() > hard_deadline:
                    outcome.result = Result.TIMEOUT
                    break
                continue

            if decision.action is Action.CLICK_CHECKBOX:
                auto_used += 1
                ok = False
                try:
                    ok = self.page.click_checkbox()
                except Exception:
                    ok = False
                self._emit("click_checkbox", "clicked" if ok else "click failed", t0, events)
                if ok:
                    self.sleep(3.0)
                    if solved(self.page):
                        continue
                outcome.auto_attempts = auto_used
                # 点了没过：降级人工（不再循环重试，避免把自动路径变成暴力尝试）
                result = self._hand_to_human(who, info, t0, events, hard_deadline)
                outcome.result = result
                break

            if decision.action is Action.HAND_TO_HUMAN:
                outcome.auto_attempts = auto_used
                outcome.result = self._hand_to_human(who, info, t0, events, hard_deadline)
                break

            outcome.result = Result.ABORTED
            break

        outcome.waited_s = self.clock() - t0
        outcome.auto_attempts = auto_used
        outcome.events = [str(e) for e in events]
        return outcome

    # ------------------------------------------------------------------ 分支
    def _wait_token(self, t0: float, budget: float, events: list[GuardEvent]) -> None:
        start = self.clock()
        while self.clock() - start < budget:
            if solved(self.page):
                self._emit("token_ready", f"{self.clock() - start:.1f}s", t0, events)
                return
            self.sleep(min(1.2, self.policy.poll_s))

    def _front(self) -> None:
        try:
            self.page.bring_to_front()
        except Exception:
            pass

    def _alert(self, kind: str, waited: int) -> None:
        # 提醒（蜂鸣/推送）可能把焦点抢走，所以提醒前后各抬一次窗口
        self._front()
        self._notify(kind, waited)
        self._front()

    def renotify_interval(self, k: int) -> float:
        """第 k 次再提醒距上一次的间隔：base * backoff^(k-1)，封顶 RENOTIFY_CAP_S。"""
        p = self.policy
        base = p.renotify_every_s
        cap = max(RENOTIFY_CAP_S, base)
        return min(cap, base * max(1.0, p.renotify_backoff) ** (k - 1))

    def _hand_to_human(self, who: str, info, t0: float,
                       events: list[GuardEvent], hard_deadline: float) -> Result:
        p = self.policy
        kind = info.kind.value
        t_hand = self.clock()
        deadline = min(hard_deadline, t_hand + p.human_wait_s)
        self._emit("handoff", f"{who} 需要人完成 {kind}", t0, events, kind=kind)
        self._alert(kind, 0)

        n = 0
        renotify_on = p.renotify_every_s > 0 and p.renotify_max_n > 0
        next_at = t_hand + self.renotify_interval(1) if renotify_on else float("inf")
        while self.clock() < deadline:
            if solved(self.page):
                waited = self.clock() - t_hand
                self._emit("passed_human", f"{self.clock() - t0:.0f}s", t0, events,
                           waited_s=round(waited, 3), renotify_n=n)
                return Result.PASSED_HUMAN
            now = self.clock()
            if now >= next_at:
                n += 1
                self._alert(kind, int(now - t0))
                self._emit("renotify", f"{int(now - t0)}s #{n}/{p.renotify_max_n}", t0, events,
                           n=n, waited_s=round(now - t_hand, 3))
                if n >= p.renotify_max_n:
                    next_at = float("inf")
                else:
                    # 按计划时刻排下一次，避免轮询抖动累积；卡顿过久则从现在重排，不补发
                    next_at += self.renotify_interval(n + 1)
                    if next_at <= self.clock():
                        next_at = self.clock() + self.renotify_interval(n + 1)
            self.sleep(p.poll_s)
        waited = self.clock() - t_hand
        self._emit("timeout", f"{p.human_wait_s:.0f}s", t0, events,
                   waited_s=round(waited, 3), renotify_n=n)
        return Result.TIMEOUT
