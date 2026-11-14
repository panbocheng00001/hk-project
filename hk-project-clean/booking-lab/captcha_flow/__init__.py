# -*- coding: utf-8 -*-
"""captcha_flow —— 验证码交互状态机（分类 → 策略 → 动作 → 复核）

定位
====
本包只回答一个问题：**这次出现的验证码该怎么处理**，然后按时序执行到出结果。
拆开写是为了可核验：只看 policy.py 的 86 行，就能判断每一步为什么会自动、为什么会交人。

    detect.py   只看 DOM，输出 ChallengeInfo（不含任何动作）
    policy.py   纯函数：ChallengeInfo + Policy → Decision（可单测、可审）
    session.py  状态机：按 Decision 执行动作，管理超时、重试、交接节奏
    adapters.py 唯一的浏览器耦合层（Playwright 实现 + 测试用假实现）

四类形态的默认处置（写在 policy.py，测试守着）
============================================
    隐形 token        → 等 token 生成，最长 token_wait_s
    勾选框            → 点一次 → 复核；不过就交人，不重试
    滑块 / 图形码 / 点选 → 抬窗口 + 通知 + 轮询，交给操作者
    探测失败（unknown） → 按最保守路径：交人 + 留档

与同级模块的分工
================
本包不含图像处理、不读像素、不生成轨迹。需要「先算出位移」的能力由 captcha_auto.py
在进入本包之前完成（开关 CAPTCHA_AUTO，缺省启用，设 0/false/off 关闭；任何一步失败即
回落本包的人工交接路径）。所以：加速层可以整层关掉，本包的行为不随之改变。

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
