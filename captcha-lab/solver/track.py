"""
鼠标轨迹生成 + 提交。

行为风控（极验 aa / 腾讯 collect / 易盾 / 顶象）不是看你拖到没拖到，而是看整条轨迹：
  - 时间戳分布（非均匀、含停顿）
  - 加速度曲线（先加速、后减速、末尾微调）
  - y 轴抖动、过冲回拉
  - 按下/抬起坐标与 DOM 事件是否自洽

这里给纯数学轨迹 + Playwright / CDP 两种落地方式。
"""
from __future__ import annotations

import math
import random
from typing import List, Tuple

Point = Tuple[float, float, float]  # x, y, t_ms(相对按下时刻)


def _ease_out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


def human_track(distance: float,
                y0: float = 0.0,
                duration_ms: float | None = None,
                overshoot_px: float = 0.0,
                jitter_px: float = 1.0,
                seed: int | None = None) -> List[Point]:
    """
    生成一条人类化水平拖动轨迹。

    distance     : 目标位移（CSS px）
    duration_ms  : 总耗时；None 时按距离自适应
    overshoot_px : 过冲量，0 表示不过冲；常用 2~6
    jitter_px    : y 轴抖动幅度
    """
    rng = random.Random(seed)

    if duration_ms is None:
        # 经验：600~1300ms，距离越大越久，但边际递减
        duration_ms = 480 + distance * 3.2 + rng.uniform(-80, 120)
        duration_ms = max(420.0, min(duration_ms, 1800.0))

    n = int(max(18, min(60, duration_ms / 18 + rng.uniform(-4, 6))))  # 采样点

    # 非均匀时间轴：中段密、两端疏
    raw = [1.0 + 0.55 * math.sin(math.pi * i / n) + rng.uniform(0.0, 0.35)
           for i in range(n)]
    scale = duration_ms / sum(raw)
    ts, acc = [], 0.0
    for w in raw:
        acc += w * scale
        ts.append(acc)

    end = distance + overshoot_px
    pts: List[Point] = []
    phase1 = 0.55          # 加速+匀速段占比
    for i, t in enumerate(ts):
        p = t / duration_ms

        if p <= phase1:
            # 前段：ease-out 主导（手先快后慢）
            u = p / phase1
            x = end * _ease_out_quint(u) * (0.97 + 0.03 * u)
        else:
            # 后段：从 end 回到真实 distance（过冲回拉 + 微调）
            u = (p - phase1) / (1 - phase1)
            if overshoot_px > 0:
                x = end - (end - distance) * _ease_in_out(u)
            else:
                # 无过冲时做亚像素微调
                wob = math.sin(u * math.pi * 2.5) * min(0.8, distance * 0.01) * (1 - u)
                x = end + wob - (end - distance) * u

        # y 抖动：低频漂移 + 高斯噪声，收敛到 y0
        y = y0 + math.sin(p * math.pi * rng.uniform(1.2, 2.0)) * jitter_px \
               + rng.gauss(0, jitter_px * 0.45) * (1 - p)

        pts.append((round(x, 2), round(y, 2), round(t, 1)))

    # 收尾：强制落到目标点，避免因取整误差差 1px 失败
    pts.append((round(distance, 2), round(y0, 2), round(duration_ms + rng.uniform(40, 120), 1)))
    return pts


def to_cdp_events(pts: List[Point], x0: float, y0: float) -> List[dict]:
    """转换成 Playwright/CDP 的 mouse 事件序列。"""
    evts = [{"type": "mousePressed", "x": x0, "y": y0, "button": "left", "clickCount": 1}]
    for x, y, _ in pts:
        evts.append({"type": "mouseMoved", "x": x0 + x, "y": y0 + y, "button": "left"})
    evts.append({"type": "mouseReleased", "x": x0 + pts[-1][0], "y": y0 + pts[-1][1],
                 "button": "left", "clickCount": 1})
    return evts


# --------------------------------------------------------------------------- #
# 落地示例一：Playwright（推荐，事件走真实浏览器输入管线）
# --------------------------------------------------------------------------- #
PLAYWRIGHT_SNIPPET = '''
import asyncio, random
from playwright.async_api import async_playwright
from solver.track import human_track

async def drag(handle_sel: str, distance: float):
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False,
                args=["--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(viewport={"width":1440,"height":900},
                                  locale="zh-CN")
        page = await ctx.new_page()
        await page.goto("TARGET_URL")

        box = await page.locator(handle_sel).bounding_box()
        x0, y0 = box["x"] + box["width"]/2, box["y"] + box["height"]/2

        await page.mouse.move(x0, y0, steps=random.randint(4, 9))   # 先悬停
        await page.mouse.down()
        pts = human_track(distance, y0=y0, overshoot_px=random.uniform(2, 6))
        prev_t = 0.0
        for x, y, t in pts:
            await page.wait_for_timeout(max(0, t - prev_t))
            prev_t = t
            await page.mouse.move(x0 + x, y)
        await page.mouse.up()
        await page.wait_for_timeout(1200)
        return await page.content()
'''

# --------------------------------------------------------------------------- #
# 落地示例二：CDP 直发（更可控，绕过部分合成事件检测）
# --------------------------------------------------------------------------- #
CDP_SNIPPET = '''
sess = await ctx.new_cdp_session(page)
await sess.send("Input.dispatchMouseEvent", {"type":"mouseMoved","x":x0,"y":y0,"buttons":0})
await sess.send("Input.dispatchMouseEvent", {"type":"mousePressed","x":x0,"y":y0,
        "button":"left","buttons":1,"clickCount":1})
for x, y, _t in pts:
    await sess.send("Input.dispatchMouseEvent", {"type":"mouseMoved",
            "x":x0+x,"y":y0+y,"button":"left","buttons":1})
await sess.send("Input.dispatchMouseEvent", {"type":"mouseReleased",
        "x":x0+pts[-1][0],"y":y0+pts[-1][1],"button":"left","buttons":0,"clickCount":1})
'''

# --------------------------------------------------------------------------- #
# 落地示例三：如果站点把轨迹加密进参数（极验 aa / 腾讯 collect），
# 直接调用页面内 SDK 的加密函数，把自定义轨迹喂进去，而不是伪造请求。
# --------------------------------------------------------------------------- #
SDK_HOOK_SNIPPET = '''
// 在页面上下文执行：拿到验证码实例后替换其轨迹来源
const inst = window.__geetest_instance || window.initGeetest && window.__captcha__;
// 通用思路：先用 page.evaluate 拿到 SDK 挂载的加密入口，
// 再把 human_track 生成的点位序列通过 window.__track 暴露给加密函数
await page.evaluate((pts) => { window.__track = pts; }, pts);
await page.evaluate(() => {
  const enc = window.__encryptTrack;      // 从 SDK 里 hook 到的函数
  window.__encrypted = enc(window.__track);
});
const payload = await page.evaluate(() => window.__encrypted);
'''


if __name__ == "__main__":
    for d in (120, 240, 320):
        pts = human_track(d, overshoot_px=4, jitter_px=1.2, seed=42)
        print(f"distance={d} points={len(pts)} "
              f"duration={pts[-1][2]:.0f}ms "
              f"last=({pts[-1][0]},{pts[-1][1]})")
        print("  sample:", pts[:3], "...", pts[-2:])
