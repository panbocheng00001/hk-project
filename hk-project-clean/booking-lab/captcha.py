# -*- coding: utf-8 -*-
"""captcha.py —— captcha_flow 包的兼容适配器（保留旧调用签名）

原来的实现把「识别 + 动作 + 等待」混在一个函数里，既没法单测，也说不清
每一步为什么这样做。现在真实逻辑在 captcha_flow/，本文件只做签名适配：

    captcha.guard(page, who, shot=..., say=..., on_captcha=...)

行为对照（与旧实现的关键差异）
------------------------------
1. 滑块 / 图形码 / 点选：旧实现会尝试自动识别并拖动；现在**不做任何自动动作**，
   直接抬窗口 + 通知 + 等待人工完成。
2. 勾选框：旧实现会重试多次；现在只点一次，不过就交人。
3. 返回值统一成 "none" / "solved" / "timeout" 三个字符串，兼容既有调用点。
4. 新增 on_event 回调，把状态迁移交给 journal 记录。
5. 交接耗时落 journal：每次再提醒记 captcha/renotify，交接结束记 captcha/handoff_done
   （who / kind / waited_s / result / renotify_n / timeout），供 captcha_report.py 统计。
"""
from __future__ import annotations

import os
from typing import Callable

from captcha_flow import (  # noqa: F401  (对外转出，供旧代码 import)
    CaptchaKind, ChallengeInfo, FakePage, Guard, Outcome, Policy,
    PlaywrightPage, Result,
)
from captcha_flow.detect import detect as _detect
from captcha_flow.session import Guard as _Guard
from captcha_auto import auto_enabled

# 旧代码里用 `kind in captcha.NEED_HUMAN` 判断是否必须人工。
# 现在由 CaptchaKind.needs_human 决定，这里保留同名常量做兼容（值保持一致）。
NEED_HUMAN = tuple(k.value for k in CaptchaKind if k.needs_human)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def beep(times: int | None = None, hz: int | None = None) -> None:
    """提示音。钉钉之外的第二条通道，纯本地。

    次数 / 音调可用环境变量 CAPTCHA_BEEP_TIMES（默认 6）/ CAPTCHA_BEEP_HZ（默认 1400）覆盖。
    """
    if times is None:
        times = _env_int("CAPTCHA_BEEP_TIMES", 6, 0, 20)
    if hz is None:
        hz = _env_int("CAPTCHA_BEEP_HZ", 1400, 37, 32767)   # winsound.Beep 的合法范围
    try:
        import time
        import winsound
        for _ in range(times):
            winsound.Beep(hz, 160)
            time.sleep(0.07)
    except Exception:
        print("\a" * times, end="", flush=True)


def focus_browser_window(page=None, say=print) -> bool:
    """把浏览器窗口提到前台。Playwright 层 + Windows 层各试一次，都失败也不影响流程。"""
    if page is not None:
        try:
            page.bring_to_front()
        except Exception:
            pass
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        hits: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
            if "Chrome" in title or "Chromium" in title:
                hits.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        for hwnd in hits:
            try:
                # 只对最小化的窗口 RESTORE；对最大化窗口调用会把它还原成普通尺寸
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                user32.FlashWindow(hwnd, True)
            except Exception:
                continue
        return bool(hits)
    except Exception:
        return False


def _notify(kind: str, waited: int = 0, who: str = "", n: int = 0) -> None:
    beep()
    try:
        import notifier as _N
        _N.captcha_need_human(kind, waited, who=who, n=n)
    except Exception:
        pass


def _journal_write(ev: str, **fields) -> None:
    from journal import J
    J().log("captcha", ev, **fields)


class HandoffRecorder:
    """把一次 guard 的人工交接落成结构化记录。只消费 GuardEvent，不参与任何决策。

    t_appear 取首次 handoff 事件的时刻（与 Outcome.waited_s 同一时钟、同一起点），
    finish() 时 waited_s = Outcome.waited_s - t_appear。
    写入失败只打日志，不向上抛。
    """

    def __init__(self, who: str, write: Callable[..., None] | None = None,
                 log: Callable[[str], None] = print) -> None:
        self.who = who
        self.write = write or _journal_write
        self.log = log
        self.t_appear: float | None = None
        self.kind = ""
        self.renotify_n = 0

    def on_event(self, ev) -> None:
        if ev.action == "handoff" and self.t_appear is None:
            self.t_appear = ev.at
            self.kind = ev.data.get("kind", "")
        elif ev.action == "renotify":
            self.renotify_n = int(ev.data.get("n", self.renotify_n + 1))
            self._write("renotify", who=self.who, kind=self.kind, n=self.renotify_n,
                        waited_s=ev.data.get("waited_s"))

    def finish(self, out, result: str) -> dict | None:
        if self.t_appear is None:
            return None
        rec = {"who": self.who, "kind": self.kind,
               "waited_s": round(max(0.0, out.waited_s - self.t_appear), 3),
               "result": result, "renotify_n": self.renotify_n,
               "timeout": result == "timeout"}
        self._write("handoff_done", **rec)
        return rec

    def _write(self, ev: str, **fields) -> None:
        try:
            self.write(ev, **fields)
        except Exception as e:
            try:
                self.log(f"[captcha] journal 写入失败（{ev}），已跳过：{type(e).__name__}: {e}")
            except Exception:
                pass


def guard(page, who: str, shot=None, say=None, timeout: int = 180,
          on_captcha=None, realert_every: int = 45,
          on_event: Callable | None = None) -> str:
    """兼容旧签名的入口。返回 "none" / "solved" / "timeout"。

    shot(page, tag, note)       截图回调，遇到关键节点调用一次
    on_captcha(page, info, note) 取证回调，保留旧接口
    on_event(GuardEvent)        状态迁移回调（新增）
    """
    say = say or print
    policy = Policy(human_wait_s=float(timeout),
                    renotify_every_s=float(realert_every or 0))
    adapter = PlaywrightPage(page)

    def _log(msg: str) -> None:
        try:
            say(msg)
        except Exception:
            print(msg)

    rec = HandoffRecorder(who, log=_log)
    n_alert = [0]

    def _event(ev) -> None:
        try:
            rec.on_event(ev)
        except Exception as e:
            _log(f"[captcha] 交接记录异常，已忽略：{type(e).__name__}: {e}")
        if on_event is not None:
            try:
                on_event(ev)
            except Exception:
                pass

    def _notify_who(kind: str, waited: int) -> None:
        _notify(kind, waited, who=who, n=n_alert[0])
        n_alert[0] += 1

    g = _Guard(adapter, policy, log=_log, on_event=_event, notify=_notify_who)

    info = _detect(adapter)

    # ---- 可选加速器：滑块自动求解 + 自动拖动（CAPTCHA_AUTO=0 可关闭）----
    # 只有 Playwright page（有 .mouse）才走；FakePage 会自动跳过，离线测试不受影响。
    if info.present and info.kind.needs_human and auto_enabled():
        try:
            import captcha_auto
            r = captcha_auto.solve_page(page, who=who, log=_log)
            if r.get("ok"):
                _log(f"[captcha_auto] 已通过（{r.get('note')}）dx={r.get('dx')} "
                     f"slider_dx={r.get('slider_dx')} tries={r.get('tries')}")
                try:
                    _journal_write("auto_solved", who=who, kind=info.kind.value,
                                   dx=r.get("dx"), slider_dx=r.get("slider_dx"),
                                   tries=r.get("tries"), note=r.get("note"))
                except Exception:
                    pass
                if shot is not None:
                    try:
                        shot(page, "captcha_auto", "自动求解通过")
                    except Exception:
                        pass
                return "solved"
            _log(f"[captcha_auto] 未通过，回落到人工交接：{r.get('note')}")
        except Exception as e:
            _log(f"[captcha_auto] 异常，回落到人工交接：{type(e).__name__}: {e}")

    if shot is not None and info.present:
        try:
            shot(page, "captcha", f"验证码出现：{info.summary}")
        except Exception:
            pass
    if on_captcha is not None and info.present:
        try:
            on_captcha(page, {"type": info.kind.value, "detail": list(info.detail)},
                       f"验证码出现：{info.kind.value}")
        except Exception:
            pass

    out = g.run(who, deadline_s=float(policy.token_wait_s + policy.human_wait_s))

    if shot is not None:
        try:
            shot(page, "captcha_ok" if out.passed else "captcha_timeout", str(out.result))
        except Exception:
            pass
    if on_captcha is not None:
        try:
            on_captcha(page, {"type": out.kind.value, "result": out.result.value},
                       f"验证码结果：{out.result.value}")
        except Exception:
            pass

    if out.result is Result.NO_CHALLENGE:
        ret = "none"
    elif out.passed:
        ret = "solved"
    else:
        ret = "timeout"
    try:
        rec.finish(out, ret)
    except Exception as e:
        _log(f"[captcha] 交接记录异常，已忽略：{type(e).__name__}: {e}")
    return ret


def detect(page) -> dict:
    """兼容旧接口：旧代码（recorder.py 等）把返回值当 dict 用。

    新代码请直接用 captcha_flow.detect.detect() 拿 ChallengeInfo。
    这里返回的 dict 只保留旧代码实际读过的字段，避免两套结构长期并存。
    """
    info = _detect(PlaywrightPage(page))
    return {
        "type": info.kind.value,
        "tokenLen": info.token_len,
        "v3": info.has_sdk,
        "detail": list(info.detail),
        "title": info.title,
        "url": info.url,
    }


def solved(page) -> bool:
    from captcha_flow.detect import solved as _solved
    return _solved(PlaywrightPage(page))
