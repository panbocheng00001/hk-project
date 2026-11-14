# -*- coding: utf-8 -*-
"""名额雷达：判断「内地驾照免试签发」此刻有没有可预约时段。
用法：
    python check.py                 # 探一次，约 20-60 秒
    python check.py --loop 180      # 每 180 秒探一次，直到有名额（Ctrl+C 停）
    python check.py --loop 120 --ring
退出码：0=有名额  10=无名额  20=没探到（排队超时/异常）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import human as H
import notifier as N
import edge_challenge

BASE = Path(__file__).resolve().parent
ENTRY = "https://portal.example.test/apps/booking.htm"
NO_QUOTA = "没有可供预约的时段"
GATEWAY_ERROR = ("502 Bad Gateway", "503 Service", "504 Gateway",
                 "Service Temporarily Unavailable", "Application Error",
                 "系统正在维修", "系統正在維修")
# 华为云 WAF 按本机网络出口 拦截（实测：Cloudflare/WARP 网段会直接触发）。
# 这类结果与「有没有名额」无关，且重试无意义——必须先换回真实家宽/移动网络。
RESTRICT_ERROR = ("access has been restricted", "access has been blocked",
                  "restricted access", "已被限制存取", "存取已被限制")


def ring(times=5):
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(1000, 250)
            time.sleep(0.1)
    except Exception:
        print("\a" * times, end="", flush=True)


def _tap(page, locator, hurry: bool) -> bool:
    """人类式点击：移鼠标 + 悬停 + 按下。"""
    try:
        return H.human_click(page, locator, hurry=hurry, logger=lambda m: print("  ·", m))
    except Exception:
        try:
            locator.click()
            return True
        except Exception:
            return False


def _body(page, limit: int = 6000) -> str:
    try:
        return page.inner_text("body")[:limit]
    except Exception:
        return ""


def _has(text: str, markers) -> bool:
    low = text.lower()
    return any(m.lower() in low for m in markers)


def _enter_flow(page, hurry: bool) -> str:
    """从入口页走：勾同意 → 预约 → 不使用智方便 → 继续。

    返回 ok / challenge / restricted / gateway / fail。
    实测（2026-11-09）：第一次点「继续」后被弹回入口页是常态，重走一遍就能进排队；
    但一旦撞上 HTTP 412（WAF JS 挑战），自动化环境拿不到 token，重试没有意义。
    """
    try:
        resp = page.goto(ENTRY, wait_until="domcontentloaded", timeout=45000)
        status = resp.status if resp is not None else None
        try:
            html = page.content()[:6000]
        except Exception:
            html = ""
        if edge_challenge.is_challenge(status, html):
            return "challenge"
        # SPA 首屏要下 ~1.9MB 的 main.js，慢的时候 25 秒不够；wait_for_selector 能跨导航继续等
        page.wait_for_selector('input[name="conditionsCheck"]', timeout=70000)
    except Exception:
        body = _body(page, 4000)
        if edge_challenge.detect(page) == "challenge":
            return "challenge"
        if _has(body, RESTRICT_ERROR):
            return "restricted"
        if _has(body, GATEWAY_ERROR):
            return "gateway"
        return "fail"
    cb = page.query_selector('input[name="conditionsCheck"]')
    if cb:
        _tap(page, cb, hurry)
    for b in page.query_selector_all("button"):
        if (b.inner_text() or "").strip() in ("预约", "預約", "MAKE AN APPOINTMENT"):
            H.dwell(0.3, 0.9, "点预约", mode=0.45, hurry=hurry)
            _tap(page, b, hurry)
            break
    page.wait_for_timeout(2500)
    try:
        page.wait_for_selector('input[type="radio"][value="withOutIas"]', timeout=30000)
    except Exception:
        return "fail"
    r = page.query_selector('input[type="radio"][value="withOutIas"]')
    if r:
        _tap(page, r, hurry)
    page.wait_for_timeout(300)
    for b in page.query_selector_all("button"):
        if (b.inner_text() or "").strip() in ("继续", "繼續", "CONTINUE", "Continue",
                                              "NEXT", "Next"):
            _tap(page, b, hurry)
            break
    return "ok"


def probe_once(headless=True, keep_open=False, attach="") -> tuple[int, str]:
    """返回 (退出码, 说明)。attach 非空时接管已开的真 Chrome（与 fill_form.py 同一套用法）。"""
    hurry = H.phase_now() == "rush"
    with sync_playwright() as p:
        own_browser = False
        if attach:
            browser = p.chromium.connect_over_cdp(attach)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context(
                locale="zh-CN", timezone_id="Asia/Shanghai", no_viewport=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            # 优先用本机已装的 Google Chrome（UA/版本与真人一致，也省去下载 Playwright 自带内核）；
            # 没有 Chrome 才退回自带 chromium。
            try:
                browser = p.chromium.launch(headless=headless, channel="chrome",
                                            args=H.LAUNCH_ARGS)
            except Exception:
                browser = p.chromium.launch(headless=headless,
                                            args=H.LAUNCH_ARGS)
            own_browser = True
            # locale/tz 必须与本机网络出口 自洽：人在上海就别设成香港（时区打架反而是异常特征）
            ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                      no_viewport=True)
            page = ctx.new_page()
        H.apply_mask(page)
        try:
            state = _enter_flow(page, hurry)
            if state == "challenge":
                return 41, ("被 WAF 挑战挡住（HTTP 412）：需要人工验证交互，"
                            "然后用 resume.py 接管（不要重试）")
            if state == "restricted":
                return 40, f"本机网络出口 被官方 WAF 限制（restricted access），不是名额问题 url={page.url[:70]}"
            if state == "gateway":
                return 30, f"官方返回错误页（502/维护），不是名额问题 url={page.url[:80]}"
            if state != "ok":
                return 20, f"入口页/认证页没走通 url={page.url[:80]}"

            # 排队 + 国家选择 + JSP 页
            t0 = time.time()
            landed = False
            bounces = 0
            while time.time() - t0 < 240:
                url = page.url
                if "abs4.example.test" in url:          # 排队中，放行前什么都别做
                    page.wait_for_timeout(1500)
                    continue
                if "SelectCountry" in url:
                    for r in page.query_selector_all('input[type="radio"]'):
                        lab = (r.evaluate("e => (e.closest('label')?.innerText || '')") or "").strip()
                        if "内地" in lab or "Mainland" in lab:
                            r.check(force=True)
                            page.wait_for_timeout(800)
                            break
                    for b in page.query_selector_all("button"):
                        if (b.inner_text() or "").strip() in ("继续", "繼續", "CONTINUE", "Continue"):
                            b.click()
                            break
                    page.wait_for_timeout(2500)
                    continue
                body = _body(page)
                if NO_QUOTA in body:
                    return 10, f"无名额（{int(time.time()-t0)}s）"
                if _has(body, RESTRICT_ERROR):
                    return 40, f"本机网络出口 被官方 WAF 限制（restricted access）（{int(time.time()-t0)}s）"
                if _has(body, GATEWAY_ERROR):
                    return 30, f"官方返回错误页（502/维护），不是名额问题（{int(time.time()-t0)}s）"
                if page.query_selector("#continueBtn1"):
                    landed = True
                    if keep_open:
                        print("★ 有名额，浏览器保持打开，请立刻接手填表（20 分钟内完成）")
                        while True:
                            page.wait_for_timeout(5000)
                    return 0, f"★ 有名额（{int(time.time()-t0)}s）url={page.url[:90]}"
                # 回到入口页 = 被弹回。实测这是常态（第一次点「继续」必被弹回），
                # 正确做法是重走一遍，而不是判成「IP 被拦」。
                if "abs3.example.test" in url and page.query_selector('input[name="conditionsCheck"]'):
                    bounces += 1
                    if bounces > 3:
                        return 20, "连续 3 次被弹回入口页：先换网络/清 cookie 再试"
                    print(f"  · 被弹回入口页（第 {bounces} 次），重走勾选流程", flush=True)
                    st = _enter_flow(page, hurry)
                    if st == "restricted":
                        return 40, "本机网络出口 被官方 WAF 限制（restricted access），不是名额问题"
                    if st == "gateway":
                        return 30, "官方返回错误页（502/维护），不是名额问题"
                    continue
                page.wait_for_timeout(1500)
            return 20, f"超时未判定，url={page.url[:110]}"
        except Exception as exc:
            return 20, f"异常 {exc.__class__.__name__}: {str(exc)[:160]}"
        finally:
            if own_browser and not (keep_open and 'landed' in dir() and landed):
                try:
                    ctx.close()
                    browser.close()
                except Exception:
                    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="循环间隔秒数，0=只探一次")
    ap.add_argument("--ring", action="store_true")
    ap.add_argument("--show", action="store_true",
                    help="（保留兼容；现在默认就是有头，因为无头会被系统拦住不渲染）")
    ap.add_argument("--headless", action="store_true",
                    help="无头模式（实测：abs3 首屏渲染不出来，仅备用）")
    ap.add_argument("--handoff", action="store_true", help="有名额时保持浏览器打开等人工接手")
    ap.add_argument("--attach", default="",
                    help="接管已开的真 Chrome，如 http://127.0.0.1:9222（先跑 launch_chrome.ps1）")
    a = ap.parse_args()
    gap = max(60, a.loop) if a.loop else 0
    bo = H.Backoff(base=60, cap=900, trip_after=5, trip_sleep=1800)

    while True:
        t = datetime.now().strftime("%H:%M:%S")
        code, msg = probe_once(headless=a.headless, keep_open=a.handoff, attach=a.attach)
        print(f"{t} [{code}] {msg}  (时段={H.phase_now()})", flush=True)
        if code == 0:
            if a.ring:
                ring()
            N.seat_open(url=msg[:160])
            sys.exit(0)
        if code == 41:
            # 撞上 WAF 挑战：绝不能在循环里继续请求（越试越进不去）
            N.edge_blocked(msg[:160])
            print("  处理：人工验证交互 → python resume.py --attach http://127.0.0.1:9223", flush=True)
            sys.exit(41)
        if code == 40:
            N.ip_restricted(msg[:160])
            sys.exit(40)
        if code == 30:
            N.site_error(msg[:120])
            wait = max(gap, 300) if gap else 0
            if not wait:
                sys.exit(30)
            print(f"  站点本身故障（502/维护），{wait:.0f}s 后再看", flush=True)
            time.sleep(H.jitter(wait, watching=False))
            continue
        if code == 20:
            wait = bo.fail()
            print(f"  连续异常，退避 {wait:.0f}s 再试", flush=True)
            time.sleep(wait)
            continue
        bo.ok()
        if not gap:
            sys.exit(code)
        # 官方明确「取消/更改的时段名额会重新开放」：别用固定心跳，抖动一下更像人
        time.sleep(H.jitter(gap, watching=True))


if __name__ == "__main__":
    main()
