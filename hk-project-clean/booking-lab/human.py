# -*- coding: utf-8 -*-
"""human.py —— 真人节奏 / 环境参数收敛工具
====================================================================
把 site_monitor.py 里那套「像人」的做法移植到 Playwright：
  · 停顿用三角分布（有众数，不是均匀随机），偶尔"走神"
  · 点击前把鼠标移过去、悬停一下再按
  · 输入逐字敲、可带偶发打错再退格
  · 页面会滚两下
  · 按香港时间分时段决定节奏（维护 / 等候 / 放号 / 高峰 / 深夜）
  · 失败指数退避 + 熔断，绝不狂点
  · 抹掉 navigator.webdriver 这类一眼就假的标记

供 fill_form.py / check.py 共用，本模块不自己连网。
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

HK_TZ = timezone(timedelta(hours=8), "HKT")

# ---------------------------------------------------------------- 时间 / 时段
def hk_now() -> datetime:
    return datetime.now(HK_TZ)


def is_workday(dt: datetime | None = None) -> bool:
    return (dt or hk_now()).weekday() < 5


def _in(dt, h1, m1, h2, m2) -> bool:
    cur = dt.hour * 60 + dt.minute
    return h1 * 60 + m1 <= cur <= h2 * 60 + m2


def phase_now(dt: datetime | None = None) -> str:
    """返回当前所处时段，决定脚本的呼吸节奏。

    maintenance 16:00-16:30 维护，别碰
    waiting     16:30-17:00 等候排队（位置随机，低干扰）
    rush        17:00-18:30 放号窗口，手要快
    quiet       01:00-07:00 深夜，几乎没人，慢
    weekend     周末不放号
    normal      其它时间（可能有取消回流）
    """
    dt = dt or hk_now()
    if dt.weekday() >= 5:
        return "weekend"
    if _in(dt, 16, 0, 16, 30):
        return "maintenance"
    if _in(dt, 16, 30, 16, 59):
        return "waiting"
    if _in(dt, 17, 0, 18, 30):
        return "rush"
    if 1 <= dt.hour < 7:
        return "quiet"
    return "normal"


# ---------------------------------------------------------------- 停顿
def tri(low: float, high: float, mode: float | None = None) -> float:
    if high <= low:
        return max(0.0, low)
    if mode is None:
        mode = low * 0.4 + high * 0.6
    mode = min(max(mode, low), high)
    return random.triangular(low, high, mode)


def dwell(low: float, high: float, why: str = "", mode: float | None = None,
          wander: float = 0.0, hurry: bool = False, logger=None) -> float:
    """两次操作之间停一下。hurry=True（放号窗口）时整体压到 low~low*1.6。"""
    if hurry:
        low, high = low * 0.5, min(high, low * 1.6 + 0.3)
        wander = 0.0
    secs = tri(low, high, mode)
    if wander and random.random() < wander:
        secs += tri(12, 40, 18)
        if logger:
            logger(f"停了 {secs:.0f}s（{why}，像人一样走神）")
    time.sleep(secs)
    return secs


def jitter(base: float, watching: bool = False) -> float:
    """循环间隔。盯着屏幕时抖动小；平时偏长，约一成会多离开一会儿。"""
    if watching:
        return base * random.triangular(0.88, 1.22, 1.0) + random.uniform(0.4, 3.5)
    gap = base * random.triangular(0.75, 1.40, 0.98) + random.uniform(0.0, 7.0)
    if random.random() < 0.10:
        gap += random.triangular(25, 160, 45)
    return gap


# ---------------------------------------------------------------- 退避 / 熔断
class Backoff:
    """连续异常时指数退避，超过阈值长暂停一次。移植自 site_monitor.py。"""

    def __init__(self, base: float = 45, cap: float = 900, trip_after: int = 5,
                 trip_sleep: float = 1800):
        self.base, self.cap = base, cap
        self.trip_after, self.trip_sleep = trip_after, trip_sleep
        self.streak = 0

    def ok(self) -> None:
        self.streak = 0

    def fail(self) -> float:
        self.streak += 1
        if self.streak >= self.trip_after:
            return self.trip_sleep
        n = max(0, self.streak - 1)
        return min(self.cap, self.base * (2 ** n))


# ---------------------------------------------------------------- 环境参数收敛
# 注意：这里刻意只做最小干预。
#  - navigator.webdriver 由启动参数 --disable-blink-features=AutomationControlled 原生关掉，
#    **不要**再用 JS 覆盖，因为 defineProperty 会在实例上留下 property descriptor，
#    有检测器专门看 hasOwnProperty(navigator,'webdriver')。
#  - navigator.languages / timezone / UA 交给 context 的 locale / timezone_id / user_agent，
#    Playwright 处理这些比手写补丁干净。
#  - 曾经改过 navigator.permissions.query，导致 Function.prototype.toString 不再含
#    "native code"，反而是最容易被抓的破绽 —— 已删除。
MASK_JS = r"""
window.chrome = window.chrome || {};
"""


def apply_mask(page, light: bool = True) -> None:
    """默认只做兜底（确保 window.chrome 存在）。真正的环境参数对齐靠启动参数与 context 选项。"""
    try:
        page.add_init_script(MASK_JS)
    except Exception:
        pass


VIEWPORTS = [(1366, 768), (1440, 900), (1536, 864), (1600, 900), (1280, 800)]


def pick_viewport() -> dict:
    w, h = random.choice(VIEWPORTS)
    return {"width": w, "height": h}


# 启动参数：既要关掉 navigator.webdriver，也要去掉浏览器顶部那条
# 「Chrome 正受到自动测试软件的控制」提示条（--excludeSwitches=enable-automation）。
# 那条提示只在你本机可见，服务端看不到；但它的成因（--enable-automation）
# 同时会让 navigator.webdriver=true，所以两个一起处理最干净。
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--excludeSwitches=enable-automation",
    "--exclude-switches=enable-automation",
    "--disable-infobars",
    # 让窗口尺寸像真人在用的尺寸，而不是 Playwright 默认的 800x600
    "--window-size=1536,864",
    "--window-position=120,80",
    "--disable-features=IsolateOrigins,site-per-process",
]


# ---------------------------------------------------------------- 鼠标 / 键盘
def human_move(page, locator) -> bool:
    """把鼠标挪到元素上：带弧度、带轻微过冲和抖动，而不是一条直线插值。"""
    try:
        box = locator.bounding_box()
        if not box:
            return False
        tx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        # 起点取一个"上一次落点附近"的猜测，避免每次都从 0,0 直线飞来
        sx = tx + random.uniform(-260, 260)
        sy = ty + random.uniform(-180, 180)
        steps = random.randint(14, 26)
        # 二次贝塞尔控制点，制造自然弧线
        cx = (sx + tx) / 2 + random.uniform(-70, 70)
        cy = (sy + ty) / 2 + random.uniform(-55, 55)
        for i in range(1, steps + 1):
            t = i / steps
            # 缓入缓出
            e = t * t * (3 - 2 * t)
            x = (1 - e) ** 2 * sx + 2 * (1 - e) * e * cx + e ** 2 * tx
            y = (1 - e) ** 2 * sy + 2 * (1 - e) * e * cy + e ** 2 * ty
            x += random.uniform(-1.5, 1.5)
            y += random.uniform(-1.5, 1.5)
            page.mouse.move(x, y)
            if random.random() < 0.18:
                time.sleep(random.uniform(0.004, 0.018))
        # 落点前轻微过冲再回拉，人点按钮时常有
        if random.random() < 0.35:
            page.mouse.move(tx + random.uniform(-4, 4), ty + random.uniform(-4, 4))
            page.mouse.move(tx, ty)
        return True
    except Exception:
        return False


def human_click(page, locator, timeout: int = 8000, hurry: bool = False,
                logger=None) -> bool:
    """移动 → 悬停 → 短暂停顿 → 按下。
    元素在 React 重渲染时可能一直「不稳定」，逐级降级：正常点 → force → 直接派发事件。"""
    try:
        if not hurry:
            human_move(page, locator)
            dwell(0.08, 0.45, "落点悬停", mode=0.18, logger=logger)
        locator.click(timeout=timeout)
        return True
    except Exception as exc:
        if logger:
            logger(f"人类式点击失败，降级重试: {exc.__class__.__name__}")
    try:
        locator.click(timeout=3000, force=True)
        return True
    except Exception:
        pass
    try:
        locator.dispatch_event("click")
        return True
    except Exception:
        return False


def human_type(locator, text: str, hurry: bool = False, typo_rate: float = 0.0,
               logger=None) -> bool:
    """逐字输入，按键间隔用对数正态分布（人打字有节奏，不是均匀随机）。"""
    def key_delay(lo: float, hi: float) -> int:
        # 对数正态：多数落在 lo~hi 的低段，偶尔慢一下
        import math
        mu = math.log((lo + hi) / 2)
        v = random.lognormvariate(mu, 0.35)
        return int(max(lo, min(hi * 1.6, v)))

    lo, hi = (12, 60) if hurry else (35, 150)
    try:
        locator.click()
        locator.fill("")
        if typo_rate and random.random() < typo_rate and len(text) > 2:
            cut = random.randint(1, len(text) - 1)
            locator.press_sequentially(text[:cut], delay=key_delay(lo, hi))
            locator.press_sequentially(random.choice("qwerty123"), delay=key_delay(lo, hi))
            time.sleep(tri(0.2, 0.7))
            for _ in range(len(text[:cut]) + 1 - cut + 1):
                locator.press("Backspace")
                time.sleep(tri(0.05, 0.16))
            if logger:
                logger("手滑打错一个字符，已退格重来")
            locator.fill("")
        locator.press_sequentially(text, delay=key_delay(lo, hi))
        return True
    except Exception as exc:
        if logger:
            logger(f"逐字输入失败，改填值: {exc.__class__.__name__}")
        try:
            locator.fill(text)
            return True
        except Exception:
            return False


def human_scroll(page, times: int = 1, hurry: bool = False) -> None:
    """滚动分小段推进，像人用手指/滚轮一点点看。"""
    if hurry:
        return
    for _ in range(times):
        total = random.randint(120, 480)
        moved = 0
        while moved < total:
            step = random.randint(40, 110)
            try:
                page.mouse.wheel(0, step)
            except Exception:
                return
            moved += step
            time.sleep(random.uniform(0.03, 0.12))
        time.sleep(tri(0.25, 0.9))


def look_around(page, logger=None, hurry: bool = False) -> None:
    """页面间的小动作：滚动 + 停顿。不做任何会掉排队的操作（不刷新、不后退）。"""
    human_scroll(page, times=random.randint(1, 2), hurry=hurry)
    dwell(0.6, 2.4, "看页面", mode=1.1, logger=logger, hurry=hurry)


def title_flicker(page) -> None:
    """偶尔把窗口标题改一下再改回（真实用户切标签页的副作用）。"""
    try:
        t = page.title()
        page.evaluate("t => {document.title = '… ' + t}", t)
        time.sleep(tri(0.3, 1.2))
        page.evaluate("t => {document.title = t}", t)
    except Exception:
        pass
