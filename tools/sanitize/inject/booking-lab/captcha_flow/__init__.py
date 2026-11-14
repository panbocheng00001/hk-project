# -*- coding: utf-8 -*-
"""captcha_flow —— 验证码交互状态机（分类 → 策略 → 动作 → 复核）

为什么重写
==========
旧实现（`captcha.py`）把三件事混在一个函数里：识别、动作、等待。
后果是：没法单测、没法解释"为什么这一步自动、那一步喊人"、
也没法在评审时说明边界在哪。

这里把它们拆开：

    detect.py   只看 DOM，输出 ChallengeInfo（不含任何动作）
    policy.py   纯函数：ChallengeInfo + Policy → Decision（可单测、可审）
    session.py  状态机：按 Decision 执行动作，管理超时、重试、喊人节奏
    adapters.py 唯一的浏览器耦合层（Playwright 实现 + 测试用假实现）

能力边界（明确写进代码，不是免责声明，是设计约束）
==================================================
本模块处理的是**自己正在使用的页面**上的验证码交互，做三件事：

  1. 分类——告诉调用方"现在出现的是哪一类验证"
  2. 等待——隐形验证（reCAPTCHA v3 这类）只等 token 生成，不做任何推断
  3. 交接——需要人判断的题（图形码/点选/图片挑战）立刻把窗口交给人

它不实现、也不接受"用图像识别算出答案再提交"这类扩展。
理由不是道德，是工程：那类实现对目标站点是入侵式的，且答案错了会
污染会话状态，反而降低整体成功率。需要自动化时，正确做法是
在**自己运营**的验证码上做（见 captcha-lab/）。

用法
====
    from captcha_flow import Guard, Policy
    guard = Guard(page_adapter, Policy(), log=print)
    out = guard.run("w1")
    if out.result is Result.PASSED: ...
"""
from __future__ import annotations

from .adapters import FakePage, PlaywrightPage, PageAdapter
from .detect import detect, detect_from_payload
from .model import CaptchaKind, ChallengeInfo, Outcome, Result
from .policy import Decision, Policy, decide
from .session import Guard, GuardEvent

__all__ = [
    "CaptchaKind", "ChallengeInfo", "Outcome", "Result",
    "Policy", "Decision", "decide",
    "Guard", "GuardEvent",
    "detect", "detect_from_payload",
    "PageAdapter", "FakePage", "PlaywrightPage",
]
__version__ = "2.0.0"
