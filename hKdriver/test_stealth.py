# -*- coding: utf-8 -*-
"""【历史记录 · 已停用】实测 playwright-stealth 的收益与代价。

结论：playwright-stealth 1.0.6 在 Chrome 156 + Playwright 1.57 上会注入一个
引用未定义变量 opts 的 navigator.userAgent getter，页面一读 UA 就抛
ReferenceError: opts is not defined —— 直接把页面搞崩。

该库已于 2026-11-08 卸载。本文件保留作证据，重新运行需要先
    python -m pip install playwright-stealth
否则会打印「stealth 应用失败」并按裸配置跑完。

对比三组：
  A 裸配置（当前 grab.py 的做法）
  B 加了 playwright-stealth
重点看 hasOwnProperty 痕迹与 nativeChecks —— 这是补丁最容易被抓的地方。
"""
import warnings

warnings.filterwarnings("ignore")

from playwright.sync_api import sync_playwright

import human as H

from audit_fingerprint import AUDIT_JS

KEYS = [
    "webdriver", "webdriverOwn", "languagesOwn", "pluginsOwn",
    "languages", "plugins", "mimeTypes", "pluginNames",
    "chromeKeys", "nativeChecks", "webglVendor", "webglRenderer",
    "dpr", "screen", "notification", "uachBrands", "fonts",
]


def snapshot(use_stealth: bool) -> dict:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, channel="chrome", args=H.LAUNCH_ARGS)
        ctx = b.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", no_viewport=True)
        page = ctx.new_page()
        if use_stealth:
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
            except Exception as e:
                print("stealth 应用失败:", type(e).__name__, e)
        page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
        d = page.evaluate(AUDIT_JS)
        b.close()
    return d


def main():
    print("=== A：裸配置（当前做法）===")
    a = snapshot(False)
    for k in KEYS:
        print(f"  {k:16} {str(a.get(k))[:95]}")

    print("\n=== B：playwright-stealth ===")
    b = snapshot(True)
    for k in KEYS:
        print(f"  {k:16} {str(b.get(k))[:95]}")

    print("\n=== 差异 ===")
    diff = 0
    for k in sorted(set(a) | set(b)):
        if k.startswith("_"):
            continue
        if a.get(k) != b.get(k):
            diff += 1
            print(f"  ~ {k}")
            print(f"      裸  : {str(a.get(k))[:100]}")
            print(f"      stealth: {str(b.get(k))[:100]}")
    print(f"\n共 {diff} 项不同")

    print("\n=== 关键判断 ===")
    trace = []
    if b.get("webdriverOwn") is not False:
        trace.append(f"webdriverOwn={b.get('webdriverOwn')}（在 navigator 实例上留了属性）")
    if b.get("languagesOwn") is not False:
        trace.append(f"languagesOwn={b.get('languagesOwn')}（在 navigator 上留了属性）")
    if b.get("pluginsOwn") is not False:
        trace.append(f"pluginsOwn={b.get('pluginsOwn')}（在 navigator 上留了属性）")
    nc = b.get("nativeChecks", "")
    if '"false"' in nc:
        trace.append(f"函数不再是 native：{nc}")
    if trace:
        print("  ⚠ stealth 留下了痕迹：")
        for t in trace:
            print("    -", t)
    else:
        print("  stealth 没有留下可测的 hasOwnProperty / native 痕迹")


if __name__ == "__main__":
    main()
