# -*- coding: utf-8 -*-
"""快速确认站点是否可用（约 30 秒）。SPA 会把导航打断，这里全程容错重试。"""
import sys
import time

from playwright.sync_api import sync_playwright

SEL = 'input[name="conditionsCheck"]'


def try_until(page, fn, seconds=60, step=2.0):
    """反复尝试，直到 fn() 返回真值或超时。导航期间抛错是正常的，忽略即可。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def main():
    with sync_playwright() as p:
        # 有头模式：实测无头（headless）时 abs3 的首屏渲染不出来，会被 WAF 拦住
        b = p.chromium.launch(headless=False)
        c = b.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        pg = c.new_page()
        try:
            r = pg.goto("https://www.gov.hk/sc/apps/tddiabs.htm",
                        wait_until="domcontentloaded", timeout=45000)
            print("entry_status =", r.status if r else "n/a")
            # abs3 首屏实测要 40 秒上下（要下 1.9MB 的 main.js 再渲染），
            # 这里只等勾选框，别拿 #root 提前放行。
            got = try_until(pg, lambda: pg.query_selector(SEL), seconds=90)
            print("final_url    =", pg.url)
            print("has_checkbox =", got is not None)
            body = ""
            try:
                body = pg.inner_text("body")[:150].replace("\n", " ")
            except Exception:
                pass
            print("body_head    =", body or "（页面还是空的）")
            if not got:
                print("提示：页面没渲染出来，可能是网络慢或站点故障，重跑一次看看")
                return 20
            return 0
        except Exception as exc:
            print("异常：", type(exc).__name__, str(exc)[:150])
            return 20
        finally:
            try:
                b.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
