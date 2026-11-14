# -*- coding: utf-8 -*-
"""audit_fingerprint.py —— 自动化痕迹审计
=====================================================================
把「脚本拉起的浏览器」和「普通方式启动的真 Chrome」放在一起逐项比对，
找出还能被网站检测出来的差异，再逐条修。

用法：
    python audit_fingerprint.py              # 只审当前模式（--channel chrome）
    python audit_fingerprint.py --compare    # 审完再连 9222 的真 Chrome 做基准对比
"""
from __future__ import annotations

import argparse
import hashlib
import json

from playwright.sync_api import sync_playwright

import human as H

AUDIT_JS = r"""
async () => {
  const out = {};
  const n = navigator, w = window, s = screen;

  // 1) 自动化标记
  out.webdriver = n.webdriver;
  out.webdriverOwn = Object.prototype.hasOwnProperty.call(n, 'webdriver');
  out.languagesOwn = Object.prototype.hasOwnProperty.call(n, 'languages');
  out.pluginsOwn = Object.prototype.hasOwnProperty.call(n, 'plugins');

  // 2) 身份信息
  out.ua = n.userAgent;
  out.appVersion = n.appVersion;
  out.platform = n.platform;
  out.vendor = n.vendor;
  out.languages = (n.languages || []).join(',');
  out.language = n.language;
  out.hardwareConcurrency = n.hardwareConcurrency;
  out.deviceMemory = n.deviceMemory;
  out.maxTouchPoints = n.maxTouchPoints;
  out.pdfViewerEnabled = n.pdfViewerEnabled;
  out.doNotTrack = n.doNotTrack;

  // 3) UA-CH
  try {
    const d = await n.userAgentData.getHighEntropyValues(['platform','platformVersion','architecture','model','uaFullVersion','bitness','wow64']);
    out.uach = JSON.stringify(d);
    out.uachBrands = (n.userAgentData.brands || []).map(b => b.brand + '/' + b.version).join(',');
    out.uachMobile = n.userAgentData.mobile;
  } catch (e) { out.uach = 'err:' + e.message; }

  // 4) 视口与屏幕
  out.screen = [s.width, s.height, s.availWidth, s.availHeight, s.colorDepth, s.pixelDepth].join('x');
  out.window = [w.outerWidth, w.outerHeight, w.innerWidth, w.innerHeight].join('x');
  out.dpr = w.devicePixelRatio;
  out.screenOrientation = (s.orientation && s.orientation.type) || '';

  // 5) 插件 / 字体
  out.plugins = n.plugins.length;
  out.mimeTypes = n.mimeTypes.length;
  out.pluginNames = [...n.plugins].map(p => p.name).join('|');
  const fonts = ['Arial','Times New Roman','Microsoft YaHei','SimSun','Segoe UI','Calibri','Cambria','Consolas'];
  out.fonts = fonts.map(f => (document.fonts.check(`12px "${f}"`) ? 1 : 0)).join('');

  // 6) WebGL
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    out.webglVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'n/a';
    out.webglRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'n/a';
    out.webglParams = gl ? [gl.getParameter(gl.MAX_TEXTURE_SIZE), gl.getParameter(gl.MAX_RENDERBUFFER_SIZE)].join('x') : '';
  } catch (e) { out.webglRenderer = 'err'; }

  // 7) Canvas 指纹（同一台机器同一浏览器应稳定）
  try {
    const c = document.createElement('canvas');
    c.width = 220; c.height = 60;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(10, 5, 60, 20);
    ctx.fillStyle = '#069';
    ctx.fillText('指纹审计 fp-audit', 2, 30);
    out.canvas = c.toDataURL().slice(-64);
  } catch (e) { out.canvas = 'err'; }

  // 8) 函数是否被改（反检测脚本的破绽）
  const natives = {};
  [['alert', w.alert], ['query', n.permissions.query], ['rtc', w.RTCPeerConnection],
   ['canvastoDataURL', HTMLCanvasElement.prototype.toDataURL],
   ['now', performance.now]].forEach(([k, fn]) => {
    try { natives[k] = /native code/.test(Function.prototype.toString.call(fn)); } catch (e) { natives[k] = 'err'; }
  });
  out.nativeChecks = JSON.stringify(natives);

  // 9) 其他环境
  out.chromeKeys = (w.chrome && typeof w.chrome === 'object') ? Object.keys(w.chrome).sort().join(',') : 'none';
  out.grecaptcha = typeof w.grecaptcha;
  out.notification = (typeof Notification !== 'undefined') ? Notification.permission : 'n/a';
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.timezoneOffset = new Date().getTimezoneOffset();
  out.rtc = (typeof w.RTCPeerConnection !== 'undefined');
  try {
    if (n.mediaDevices && n.mediaDevices.enumerateDevices) {
      const devs = await n.mediaDevices.enumerateDevices();
      out.mediaDevices = devs.length;
    }
  } catch (e) { out.mediaDevices = 'err'; }
  out.connection = (n.connection && n.connection.effectiveType) || '';
  out.storageQuota = (n.storage && n.storage.estimate) ? 'yes' : 'no';
  return out;
}
"""


def collect(page) -> dict:
    req_headers = {}

    def on_req(req):
        if req.resource_type == "document" and "headers" not in req_headers:
            req_headers["headers"] = req.headers

    page.on("request", on_req)
    page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1200)
    data = page.evaluate(AUDIT_JS)
    h = req_headers.get("headers", {})
    data["_hdr_accept_language"] = h.get("accept-language", "")
    data["_hdr_sec_ch_ua"] = h.get("sec-ch-ua", "")
    data["_hdr_sec_ch_ua_platform"] = h.get("sec-ch-ua-platform", "")
    data["_hdr_ua"] = h.get("user-agent", "")
    data["_hdr_sec_fetch"] = f"{h.get('sec-fetch-site','')}/{h.get('sec-fetch-mode','')}/{h.get('sec-fetch-dest','')}"
    return data


def run_launch():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, channel="chrome", args=H.LAUNCH_ARGS)
        # 关键：no_viewport=True，否则 Playwright 会把 screen/DPR 覆盖成 1280x720@1x
        ctx = b.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", no_viewport=True)
        page = ctx.new_page()
        H.apply_mask(page)
        d = collect(page)
        b.close()
    return d


def run_attach(url="http://127.0.0.1:9222"):
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(url)
        ctx = b.contexts[0] if b.contexts else b.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        d = collect(page)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true")
    a = ap.parse_args()

    print("=== 模式 1：脚本拉起（--channel chrome + LAUNCH_ARGS）===")
    base = run_launch()
    for k in sorted(base):
        if k.startswith("_"):
            continue
        v = str(base[k])
        print(f"  {k:22} {v[:100]}")

    if not a.compare:
        print("\n（加 --compare 可连真 Chrome 做基准对比）")
        return

    print("\n=== 模式 2：普通方式启动的真 Chrome（接管 9222）===")
    try:
        ref = run_attach()
    except Exception as e:
        print("  连不上 9222：", type(e).__name__, str(e)[:120])
        print("  先跑：powershell -ExecutionPolicy Bypass -File .\\launch_chrome.ps1")
        return

    print("\n=== 差异（只列不一样的）===")
    diff = 0
    for k in sorted(set(base) | set(ref)):
        if k.startswith("_"):
            continue
        if base.get(k) != ref.get(k):
            diff += 1
            print(f"  ✗ {k}")
            print(f"      脚本: {str(base.get(k))[:110]}")
            print(f"      真实: {str(ref.get(k))[:110]}")
    print(f"\n共 {diff} 项差异")
    print("\n=== 请求头对比 ===")
    for k in ("_hdr_ua", "_hdr_accept_language", "_hdr_sec_ch_ua", "_hdr_sec_ch_ua_platform", "_hdr_sec_fetch"):
        if base.get(k) != ref.get(k):
            print(f"  ✗ {k}\n      脚本: {str(base.get(k))[:110]}\n      真实: {str(ref.get(k))[:110]}")

    with open("runs/audit_launch.json", "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=1)
    with open("runs/audit_real.json", "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)
    print("\n明细已存：runs/audit_launch.json / runs/audit_real.json")


if __name__ == "__main__":
    main()
