# -*- coding: utf-8 -*-
"""验证 1：启动参数里到底有没有 --enable-automation；验证 2：webdriver 是否为 false。
再配合桌面截图，看顶部提示条是否消失。
"""
import sys
import time

from playwright.sync_api import sync_playwright

import human as H

CHECKS = r"""
() => ({
  webdriver: navigator.webdriver,
  webdriverOwn: Object.prototype.hasOwnProperty.call(navigator, 'webdriver'),
  ua: navigator.userAgent,
  automationFlag: !!(window.chrome && window.chrome.app && window.chrome.app.isInstalled === undefined),
  plugins: navigator.plugins.length,
})
"""


def run(mode: str):
    with sync_playwright() as p:
        if mode == "chrome":
            browser = p.chromium.launch(headless=False, channel="chrome", args=H.LAUNCH_ARGS)
            print("启动参数:", H.LAUNCH_ARGS)
        else:
            browser = p.chromium.launch(headless=False, args=H.LAUNCH_ARGS)
            print("启动参数:", H.LAUNCH_ARGS)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto("about:blank")
        info = page.evaluate(CHECKS)
        for k, v in info.items():
            print(f"  {k:14} = {v}")

        # 通过 CDP 看浏览器自己的命令行（确认 enable-automation 是否被排除）
        try:
            sess = ctx.new_cdp_session(page)
            ver = sess.send("Browser.getVersion")
            print("  product        =", ver.get("product"))
            print("  userAgent      =", ver.get("userAgent")[:90])
        except Exception as e:
            print("  CDP getVersion 失败:", type(e).__name__)

        print("\n窗口保持 8 秒，请观察顶部有没有黄色提示条…")
        page.goto("https://example.com", wait_until="domcontentloaded")
        time.sleep(8)
        browser.close()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "chrome")
