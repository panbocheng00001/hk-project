# -*- coding: utf-8 -*-
"""headless_check.py —— 无头浏览器自检：跑公开检测站，看哪些特征暴露了

对比：
  patchright  vs  原版 playwright
站点：
  bot.sannysoft.com   （基础指纹表，快）
  abrahamjuliot.github.io/creepjs  （深度指纹，慢，可选）

用法：
  python headless_check.py --engine patchright
  python headless_check.py --engine playwright
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
ARGS = ["--disable-blink-features=AutomationControlled",
        "--no-first-run", "--no-default-browser-check"]

LOCAL_JS = """
() => {
  const out = {};
  out.webdriver = navigator.webdriver;
  out.plugins = navigator.plugins ? navigator.plugins.length : -1;
  out.languages = navigator.languages;
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  out.chrome = typeof window.chrome;
  out.chromeRuntime = !!(window.chrome && window.chrome.runtime);
  out.outerW = window.outerWidth; out.outerH = window.outerHeight;
  out.innerW = window.innerWidth; out.innerH = window.innerHeight;
  out.screenW = screen.width; out.screenH = screen.height;
  out.availH = screen.availHeight;
  out.permissionsNotify = null;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'n/a';
      out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'n/a';
    } else { out.webgl = 'no-context'; }
  } catch (e) { out.webglErr = String(e).slice(0, 60); }
  try {
    const c = document.createElement('canvas');
    c.width = 200; c.height = 60;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top'; ctx.font = '14px Arial';
    ctx.fillStyle = '#f60'; ctx.fillRect(0,0,120,20);
    ctx.fillStyle = '#069'; ctx.fillText('headless-check', 2, 4);
    out.canvasHash = c.toDataURL().length + ':' + c.toDataURL().slice(-24);
  } catch (e) { out.canvasErr = String(e).slice(0, 60); }
  try { out.audio = !!(window.AudioContext || window.webkitAudioContext); } catch (e) { out.audio = false; }
  return out;
}
"""


def run(engine: str, headless: bool, sites: list[str]):
    if engine == "patchright":
        from patchright.sync_api import sync_playwright as sp
    else:
        from playwright.sync_api import sync_playwright as sp

    with sp() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=headless, args=ARGS)
        ctx = b.new_context(ignore_https_errors=True,
                            viewport={"width": 1366, "height": 768},
                            locale="zh-HK", timezone_id="Asia/Hong_Kong",
                            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                        "Chrome/131.0.0.0 Safari/537.36"))
        pg = ctx.new_page()
        pg.goto("about:blank")
        print(f"\n=== 本地指纹（{engine} headless={headless}）===")
        print(json.dumps(pg.evaluate(LOCAL_JS), ensure_ascii=False, indent=2))

        for url in sites:
            print(f"\n=== {url} ===")
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(6000)
                txt = pg.inner_text("body")
                # sannysoft 表格：只挑关键行
                keys = ["WebDriver", "Chrome", "Plugins", "Languages", "WebGL",
                        "Hairline", "User Agent", "Broken Image", "WebGL Vendor",
                        "WebGL Renderer", "Touchscreen"]
                for line in txt.splitlines():
                    s = line.strip()
                    if any(k.lower() in s.lower() for k in keys) and len(s) < 160:
                        print("  ", s)
            except Exception as e:
                print("  ERR", type(e).__name__, str(e)[:120])
        b.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="both", choices=["patchright", "playwright", "both"])
    ap.add_argument("--headless", default="true")
    a = ap.parse_args()
    hl = a.headless.lower() != "false"
    sites = ["https://bot.sannysoft.com/"]
    if a.engine in ("patchright", "both"):
        run("patchright", hl, sites)
    if a.engine in ("playwright", "both"):
        run("playwright", hl, sites)
