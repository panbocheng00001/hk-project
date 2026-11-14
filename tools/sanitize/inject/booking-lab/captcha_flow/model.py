# -*- coding: utf-8 -*-
"""model.py —— 纯数据：验证码分类、探测结果、执行结果

全部是 frozen dataclass / Enum，没有 IO、没有浏览器依赖，
因此可以在任何环境里单测、也能直接被别的模块 import 而不拖依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CaptchaKind(str, Enum):
    """验证码形态。命名对齐业界通用叫法，避免自定义黑话。"""

    NONE = "none"                    # 页面上没有验证码
    TOKEN_ONLY = "token_only"        # 隐形验证：只有 token 字段 + SDK 脚本
    CHECKBOX = "checkbox"            # 「我不是机器人」勾选框
    SLIDER = "slider"                # 滑块/拖动拼图
    IMAGE_CODE = "image_code"        # 图形字符码
    IMAGE_GRID = "image_grid"        # 图片点选（红绿灯那类）
    UNKNOWN = "unknown"              # 认出来了，但不属于以上任何一类

    @property
    def needs_human(self) -> bool:
        """是否必须由人完成判断。"""
        return self in (CaptchaKind.SLIDER, CaptchaKind.IMAGE_CODE,
                        CaptchaKind.IMAGE_GRID, CaptchaKind.UNKNOWN)

    @property
    def can_auto(self) -> bool:
        """是否存在"不涉及内容判断"的合法自动路径。"""
        return self in (CaptchaKind.TOKEN_ONLY, CaptchaKind.CHECKBOX)


class Result(str, Enum):
    """一次 guard 调用的结局。"""

    NO_CHALLENGE = "no_challenge"    # 压根没有验证码，直接放行
    PASSED_AUTO = "passed_auto"      # 隐形验证自动完成 / 勾选框一次通过
    PASSED_HUMAN = "passed_human"    # 人工完成后复核通过
    TIMEOUT = "timeout"              # 等超时，仍然没有通过
    ABORTED = "aborted"              # 被调用方主动中止

    @property
    def ok(self) -> bool:
        return self in (Result.NO_CHALLENGE, Result.PASSED_AUTO, Result.PASSED_HUMAN)

    @property
    def passed(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class ChallengeInfo:
    """一次 DOM 探测的结果。字段全部可空，缺信息时保持保守默认。"""

    kind: CaptchaKind = CaptchaKind.NONE
    detail: tuple[str, ...] = ()
    token_len: int = -1          # -1 = 页面上没有 token 字段
    has_sdk: bool = False        # 页面是否真的加载了验证 SDK
    url: str = ""
    title: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CaptchaKind):
            object.__setattr__(self, "kind", CaptchaKind(str(self.kind)))
        if not isinstance(self.detail, tuple):
            object.__setattr__(self, "detail", tuple(self.detail or ()))

    @property
    def present(self) -> bool:
        return self.kind is not CaptchaKind.NONE

    @property
    def summary(self) -> str:
        if not self.present:
            return "no challenge"
        bits = ", ".join(self.detail[:3])
        return f"{self.kind.value}" + (f" ({bits})" if bits else "")


@dataclass
class Outcome:
    """guard.run() 的返回值。"""

    result: Result
    kind: CaptchaKind = CaptchaKind.NONE
    waited_s: float = 0.0
    auto_attempts: int = 0
    events: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.result.passed

    def __bool__(self) -> bool:
        return self.passed
