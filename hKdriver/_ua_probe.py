# -*- coding: utf-8 -*-
"""_ua_probe.py —— 找出「无窗口 + UA/UA-CH 是正常 Chrome」的启动组合

背景：patchright 默认 headless=True 出来的 UA 带 "HeadlessChrome"，
     navigator.userAgentData 是 {}、window.chrome.runtime 缺失。
     拿这种身份铸票，R1 的对齐就建立在假身份上（而且 WAF 一眼能看出）。

本脚本只开 data: 页，不发任何网络请求。测试组合：
  A  headless=True                     （patchright 默认）
  B  headless=False + --headless=new   （新无头，产品名应为 Chrome）
  C  headless=False                    （真有窗口，作参照）
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")

BASE_ARGS = ["--disable-blink-features=AutomationControlled",
             "--no-first-run", "--no-default-browser-check",
             "--disable-dev-shm-usage", "--lang=zh-HK"]

JS = r"""
async () => {
  const o = {ua: navigator.userAgent, wd: navigator.webdriver,
             rt: !!(window.chrome && window.chrome.runtime),
             lang: navigator.language, langs: navigator.languages};
  try {
    const u = navigator.userAgentData;
    o.uaData = u ? {brands: u.brands, mobile: u.mobile, platform: u.platform} : null;
    if (u && u.getHighEntropyValues) {
      const h = await u.getHighEntropyValues(['fullVersionList','platformVersion',
                                              'architecture','bitness']);
      o.full = {brands: h.fullVersionList, pv: h.platformVersion,
                arch: h.architecture, bits: h.bitness};
    }
  } catch (e) { o.err = String(e).slice(0, 80); }
  o.screen = [screen.width, screen.height];
  o.outer = [window.outerWidth, window.outerHeight];
  o.inner = [window.innerWidth, window.innerHeight];
  o.plugins = navigator.plugins ? navigator.plugins.length : -1;
  return o;
}
"""

CASES = [
    ("A headless=True", True, []),
    ("B --headless=new", False, ["--headless=new"]),
    ("C 有窗口", False, []),
]


def main() -> int:
    from patchright.sync_api import sync_playwright
    print(f"内核 exe: {CHROME}\n")
    out = {}
    with sync_playwright() as pw:
        for label, hl, extra in CASES:
            try:
                b = pw.chromium.launch(executable_path=CHROME, headless=hl,
                                       args=BASE_ARGS + extra,
                                       ignore_default_args=["--enable-automation"])
            except Exception as e:
                print(f"=== {label} ===\n  启动失败 {type(e).__name__}: {str(e)[:140]}\n")
                continue
            try:
                ctx = b.new_context(ignore_https_errors=True, locale="zh-HK",
                                    timezone_id="Asia/Hong_Kong",
                                    viewport={"width": 1366, "height": 768})
                pg = ctx.new_page()
                pg.goto("data:text/html,<title>p</title><h1>x</h1>", wait_until="commit")
                d = pg.evaluate(JS)
                d["version"] = b.version
                out[label] = d
                ua = d.get("ua", "")
                headless_mark = "HeadlessChrome" in ua
                print(f"=== {label} ===  内核 {d['version']}")
                print(f"  UA       : {ua[:100]}")
                print(f"  Headless 标记: {'❌ 有' if headless_mark else '✅ 无'}")
                print(f"  UA-CH    : {json.dumps(d.get('uaData'), ensure_ascii=False)}")
                print(f"  fullVer  : {json.dumps(d.get('full'), ensure_ascii=False)[:160]}")
                print(f"  webdriver: {d.get('wd')}   chrome.runtime: {d.get('rt')}")
                print(f"  screen/outer/inner: {d.get('screen')} / {d.get('outer')} / "
                      f"{d.get('inner')}  plugins={d.get('plugins')}")
                print()
            except Exception as e:
                print(f"=== {label} ===  异常 {type(e).__name__}: {str(e)[:140]}\n")
            finally:
                try:
                    b.close()
                except Exception:
                    pass

    p = BASE / "runs" / "ua_probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {p.relative_to(BASE)}")

    best = [k for k, v in out.items() if "HeadlessChrome" not in v.get("ua", "")]
    print("\n结论：无 Headless 标记的组合 = " + (", ".join(best) or "无"))
    newmode = out.get("B --headless=new", {})
    if newmode and "HeadlessChrome" not in newmode.get("ua", ""):
        print("→ 铸票请用：headless=False + args=['--headless=new']（不弹窗，UA 是正常 Chrome）")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
