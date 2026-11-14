# -*- coding: utf-8 -*-
"""adapters.py —— 唯一的浏览器耦合层

把"页面"抽象成 PageAdapter 三个方法：evaluate / click_selector / bring_to_front。
好处：状态机（session.py）可以在完全离线的情况下被测试，
不需要真的开浏览器；换 Playwright 版本也只改这一个文件。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

CHECKBOX_SELECTORS = (
    'iframe[title*="reCAPTCHA"]',
    'iframe[src*="recaptcha/api2/anchor"]',
    'iframe[src*="recaptcha/enterprise/anchor"]',
)
CHECKBOX_INNER = ("#recaptcha-anchor, .recaptcha-checkbox-border, span.recaptcha-checkbox")


@runtime_checkable
class PageAdapter(Protocol):
    """状态机需要的全部页面能力。故意保持极小。"""

    def evaluate(self, js: str) -> Any: ...

    def click_checkbox(self) -> bool: ...

    def bring_to_front(self) -> None: ...


class PlaywrightPage:
    """Playwright sync page 的薄封装。跨域 iframe 里的勾选框走 frame_locator。"""

    def __init__(self, page) -> None:
        self._page = page

    def evaluate(self, js: str) -> Any:
        return self._page.evaluate(js)

    def click_checkbox(self) -> bool:
        for sel in CHECKBOX_SELECTORS:
            try:
                fl = self._page.frame_locator(sel)
                fl.locator(CHECKBOX_INNER).first.click(timeout=4000)
                return True
            except Exception:
                continue
        for css in (".g-recaptcha", ".cf-turnstile", ".h-captcha"):
            try:
                self._page.click(css, timeout=2500)
                return True
            except Exception:
                continue
        return False

    def bring_to_front(self) -> None:
        try:
            self._page.bring_to_front()
        except Exception:
            pass


class FakePage:
    """测试用假页面：按脚本来源分别回放 detect / solved 的返回。

    两条流分开是必须的——detect 和 solved 探针语义不同，
    混成一条会让测试结果依赖调用顺序，测出来的东西没有意义。

    >>> fp = FakePage(detect=[{"kind": "checkbox"}],
    ...               solved=[{"tokenLen": 32, "visible": False}])
    >>> fp.evaluate(DETECT_MARKER)["kind"]
    'checkbox'
    """

    def __init__(self, detect=None, solved=None, clicks: int = 1,
                 click_ok: bool = True, front_ok: bool = True) -> None:
        self.detect_payloads = list(detect or [{"kind": "none"}])
        self.solved_payloads = list(solved or [{"tokenLen": -1, "visible": False}])
        self.clicks = clicks
        self.click_ok = click_ok
        self.front_ok = front_ok
        self.n_detect = 0
        self.n_solved = 0
        self.n_click = 0
        self.fronted = 0
        self.js_seen: list[str] = []

    def evaluate(self, js: str) -> Any:
        self.js_seen.append(js)
        if _is_solved_probe(js):
            return self._next(self.solved_payloads, "solved")
        return self._next(self.detect_payloads, "detect")

    def _next(self, stream: list, which: str) -> Any:
        idx = self.n_solved if which == "solved" else self.n_detect
        if which == "solved":
            self.n_solved += 1
        else:
            self.n_detect += 1
        idx = min(idx, len(stream) - 1)
        payload = stream[idx]
        if isinstance(payload, BaseException):
            raise payload
        return payload

    def click_checkbox(self) -> bool:
        self.n_click += 1
        return self.click_ok and self.n_click <= self.clicks

    def bring_to_front(self) -> None:
        self.fronted += 1


def _is_solved_probe(js: str) -> bool:
    """solved 探针只返回 tokenLen/visible；detect 探针一定含 'kind'。"""
    return "visible" in js and "kind:" not in js
