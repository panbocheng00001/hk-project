# -*- coding: utf-8 -*-
"""指纹自检：把「Playwright 自带的 chromium」和「本机真 Chrome」放在一起对比，
看看哪些特征会露出自动化痕迹。

用法：
    python fingerprint_check.py                 # Playwright chromium（默认）
    python fingerprint_check.py --chrome        # 本机已安装的真 Chrome（channel=chrome）
    python fingerprint_check.py --attach http://127.0.0.1:9222   # 接管调试端口的真 Chrome
    python fingerprint_check.py --headless      # 无头模式（差异最大的场景）
"""
from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

import human as H

PROBE_JS = r"""
() => {
  const out = {};
  const n = navigator, w = window;
  out.webdriver = n.webdriver;                       // 自动化铁证，应为 undefined
  out.webdriverOwn = Object.prototype.hasOwnProperty.call(n, 'webdriver');  // JS 补丁会留这个
  out.languagesOwn = Object.prototype.hasOwnProperty.call(n, 'languages');
  out.ua = n.userAgent;
  out.languages = n.languages;
  out.platform = n.platform;
  out.hardwareConcurrency = n.hardwareConcurrency;
  out.deviceMemory = n.deviceMemory;
  out.plugins = n.plugins.length;
  out.mimeTypes = n.mimeTypes.length;
  out.pdfViewerEnabled = n.pdfViewerEnabled;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.chromeKeys = (w.chrome && typeof w.chrome === 'object')
      ? Object.keys(w.chrome).sort().join(',') : 'no window.chrome';
  out.chromeRuntime = !!(w.chrome && w.chrome.runtime);
  out.chromeCsi = typeof w.chrome?.csi;              // 真 Chrome 有，chromium 常缺
  out.chromeLoadTimes = typeof w.chrome?.loadTimes;
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'n/a';
    out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'n/a';
  } catch (e) { out.webglRenderer = 'err'; }
  // 函数 toString 是否被改过（反检测脚本的常见破绽）
  try {
    out.alertNative = /native code/.test(Function.prototype.toString.call(w.alert));
    out.queryNative = /native code/.test(Function.prototype.toString.call(
        w.navigator.permissions.query));
  } catch (e) {}
  out.screen = [screen.width, screen.height, screen.availHeight].join('x');
  out.outerInner = [w.outerWidth, w.innerWidth, w.outerHeight, w.innerHeight].join('/');
  out.notificationPerm = (typeof Notification !== 'undefined') ? Notification.permission : 'n/a';
  out.hasChromeWebStore = !!w.chrome && typeof w.chrome.webstore !== 'undefined';
  out.cookieEnabled = n.cookieEnabled;
  return out;
}
"""


def run(mode: str, attach: str, headless: bool) -> dict:
    with sync_playwright() as p:
        if attach:
            browser = p.chromium.connect_over_cdp(attach)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        elif mode == "chrome":
            browser = p.chromium.launch(headless=headless, channel="chrome",
                                        args=H.LAUNCH_ARGS)
            ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                      no_viewport=True)
            page = ctx.new_page()
        else:
            browser = p.chromium.launch(headless=headless,
                                        args=H.LAUNCH_ARGS)
            ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                      no_viewport=True)
            page = ctx.new_page()
        H.apply_mask(page)          # 与 grab.py 保持一致
        page.goto("about:blank")
        data = page.evaluate(PROBE_JS)
        if not attach:
            browser.close()
        return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", action="store_true", help="用本机真 Chrome 内核")
    ap.add_argument("--attach", default="", help="接管调试端口的真 Chrome，如 http://127.0.0.1:9222")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    mode = "attach" if a.attach else ("chrome" if a.chrome else "chromium")
    data = run(mode, a.attach, a.headless)
    if a.json:
        print(json.dumps({"mode": mode, "headless": a.headless, "fp": data},
                         ensure_ascii=False, indent=1))
        return
    print(f"=== 模式 {mode}{' (headless)' if a.headless else ' (有头)'} ===")
    for k, v in data.items():
        flag = ""
        if k == "webdriver" and v not in (None, False):
            flag = "  ← 自动化铁证"
        if k == "ua" and "Headless" in str(v):
            flag = "  ← headless 会写进 UA"
        if k == "chromeCsi" and v in ("undefined",):
            flag = "  ← 真 Chrome 应有"
        if k == "webglRenderer" and any(s in str(v) for s in ("SwiftShader", "Software")):
            flag = "  ← 软件渲染，GPU 指纹异常"
        print(f"  {k:22} {v}{flag}")


if __name__ == "__main__":
    sys.exit(main())
