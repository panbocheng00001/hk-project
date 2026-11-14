# -*- coding: utf-8 -*-
"""policy.py —— 纯函数策略：ChallengeInfo → Decision

这一层是"为什么这样处理"的**唯一**出口。
把它写成纯函数，是为了让评审（或另一个模型）只看这 80 行就能判断行为是否合理，
不用读浏览器代码。

决策原则，四条：
  1. 有合法自动路径的（隐形 token / 勾选框）才自动。
  2. 需要内容判断的（图形码 / 点选 / 滑块）一律交给人，不做图像推断。
  3. 探测失败（UNKNOWN）按最保守路径处理：喊人 + 存档，不猜。
  4. 任何自动动作都有次数上限，触顶即降级为人工。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import CaptchaKind, ChallengeInfo


class Action(str, Enum):
    """状态机可执行的动作。刻意做得很少——少才好审。"""

    PASS_THROUGH = "pass_through"    # 什么都不做，直接继续
    WAIT_TOKEN = "wait_token"        # 等隐形验证自动生成 token
    CLICK_CHECKBOX = "click_checkbox"  # 点一次「我不是机器人」
    HAND_TO_HUMAN = "hand_to_human"  # 把窗口交给人
    ABORT = "abort"                  # 放弃本轮


@dataclass(frozen=True)
class Policy:
    """可调参数。所有默认值都偏保守：等得久一点、自动次数少一点。"""

    token_wait_s: float = 20.0        # 隐形验证最多等多久
    human_wait_s: float = 180.0       # 人工最多等多久
    checkbox_retries: int = 1         # 勾选框最多自动点几次
    renotify_every_s: float = 45.0     # 人工等待期间多久再提醒一次（0=只提醒一次）
    poll_s: float = 1.5               # 轮询间隔
    max_auto: int = 2                 # 任何自动动作的总上限


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str

    def __str__(self) -> str:
        return f"{self.action.value}: {self.reason}"


def decide(info: ChallengeInfo, policy: Policy | None = None,
           auto_used: int = 0) -> Decision:
    """核心决策。无副作用，给定输入永远返回同一结果。"""
    p = policy or Policy()

    # 没有挑战面
    if not info.present:
        if info.token_len == 0 and info.has_sdk:
            return Decision(Action.WAIT_TOKEN, "SDK 在、token 字段为空，等它自己生成")
        return Decision(Action.PASS_THROUGH, "页面上没有验证码")

    # 有挑战面但探测结果不可信
    if info.kind is CaptchaKind.UNKNOWN:
        return Decision(Action.HAND_TO_HUMAN, "探测失败或形态未知，交给人判断")

    # 需要内容判断的：不做推断
    if info.kind.needs_human:
        return Decision(Action.HAND_TO_HUMAN, f"{info.kind.value} 需要人工判断")

    # 隐形验证
    if info.kind is CaptchaKind.TOKEN_ONLY:
        if auto_used >= p.max_auto:
            return Decision(Action.HAND_TO_HUMAN, "自动次数已用完")
        return Decision(Action.WAIT_TOKEN, "隐形验证，等待 token 生成")

    # 勾选框
    if info.kind is CaptchaKind.CHECKBOX:
        if auto_used >= p.max_auto:
            return Decision(Action.HAND_TO_HUMAN, "自动次数已用完")
        return Decision(Action.CLICK_CHECKBOX, "勾选框，点一次后复核")

    return Decision(Action.HAND_TO_HUMAN, "未覆盖的形态，保守处理")
