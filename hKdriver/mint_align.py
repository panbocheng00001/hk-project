# -*- coding: utf-8 -*-
"""mint_align.py —— 铸票并对齐：把「铸票那一刻」完整拍下来

R1 的前半件。它和 token_pool.MintWorker 的区别只有一条，但很关键：

    MintWorker 记的是「cookie + 一个手写 UA」
    mint_align 记的是「浏览器当时真实发出的整套请求头 + navigator 全家 + cookie」

上次 400 的根因就在这里：回放时 UA 头写 131、UA-CH 报 156、TLS 是别的画像，
三个身份互相打架。WAF 不需要多聪明就能判出来。

本脚本产出 runs/tickets/<ip_id>.json，供 align_client.py 逐字重放。

用法
---------------------------------------------------------------------------
  # 零网络自检：只验证「抓头 + 抓指纹」这套代码在真内核上跑得通
  python mint_align.py --selftest

  # 铸票（会打目标站，一次出口别超过 2~3 次访问）
  python mint_align.py --ip-id w1
  python mint_align.py --ip-id p3 --proxy http://user:pass@host:port

  # 指定内核/是否无窗口
  python mint_align.py --ip-id w1 --engine patchright --headless

产物字段（align_client 逐项用）
---------------------------------------------------------------------------
  headers      文档导航请求的真实头（含 UA / sec-ch-ua 全家 / sec-fetch-*）
  ua           navigator.userAgent（与 headers 里的 UA 必须一致，自检会比对）
  ua_meta      userAgentData.brands / fullVersionList / platform / mobile
  nav          语言、时区、屏幕、webdriver、并发数、deviceMemory、WebGL 渲染器
  cookies      含 HIK0（挑战票）与 HWWAFSESID
  engine       内核名/版本/是否 headless
  entry        这次导航的状态码、是否过了挑战、最终 URL
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

TICKET_DIR = BASE / "runs" / "tickets"
ENTRY = "https://abs3.td.gov.hk/?lang=sc"
CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")

ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run", "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--lang=zh-HK",
    "--disable-background-networking",
    # 有窗口模式也「不出现」：把窗口挪到屏幕外，任务栏一闪而过都不该有
    "--window-position=-32000,-32000",
    "--window-size=1366,820",
    "--js-flags=--max-old-space-size=512",   # 挑战 JS 会吃内存，但别放开到 4GB
]

# 采集 navigator / 环境事实。不修改任何值 —— 记录的是真相，不是伪装。
CAPTURE_JS = r"""
() => {
  const o = {};
  o.userAgent   = navigator.userAgent;
  o.appVersion  = navigator.appVersion;
  o.platform    = navigator.platform;
  o.languages   = Array.from(navigator.languages || []);
  o.language    = navigator.language;
  o.hardwareConcurrency = navigator.hardwareConcurrency;
  o.deviceMemory = navigator.deviceMemory || null;
  o.webdriver   = navigator.webdriver;
  o.plugins     = navigator.plugins ? navigator.plugins.length : -1;
  o.timezone    = Intl.DateTimeFormat().resolvedOptions().timeZone;
  o.screen      = [screen.width, screen.height, screen.availWidth, screen.availHeight,
                   screen.colorDepth];
  o.window      = [window.outerWidth, window.outerHeight,
                   window.innerWidth, window.innerHeight, window.devicePixelRatio];
  o.hasChromeRuntime = !!(window.chrome && window.chrome.runtime);
  try {
    const u = navigator.userAgentData;
    o.uaData = u ? {brands: u.brands, mobile: u.mobile, platform: u.platform} : null;
    o.uaFull = (u && u.getHighEntropyValues)
      ? null : null;   // 高熵值在下面异步取
  } catch (e) { o.uaData = null; }
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    o.webgl = dbg ? [gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
                     gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)] : null;
  } catch (e) { o.webgl = null; }
  return o;
}
"""

CAPTURE_UA_FULL_JS = r"""
async () => {
  try {
    const u = navigator.userAgentData;
    if (!u || !u.getHighEntropyValues) return null;
    const h = await u.getHighEntropyValues(['fullVersionList','platformVersion',
                                            'architecture','bitness','model','uaFullVersion']);
    return h;
  } catch (e) { return null; }
}
"""


# ---------------------------------------------------------------------------
def _launch(engine: str, headless: bool):
    if engine == "patchright":
        from patchright.sync_api import sync_playwright
    else:
        from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    kw: Dict[str, Any] = dict(headless=headless, args=ARGS,
                              ignore_default_args=["--enable-automation"])
    if Path(CHROME).exists():
        kw["executable_path"] = CHROME
    return pw, pw.chromium.launch(**kw)


def headless_ua_warning(ua: str, headless: bool) -> None:
    """headless 模式的 UA 里带 HeadlessChrome —— 这是可被一眼识别的假身份。

    实测（_ua_probe.py，2026-11-11）：
      headless=True / --headless=new  →  UA 含 "HeadlessChrome/156"
      headless=False（窗口在屏幕外） →  UA 是正常 "Chrome/156.0.0.0"
    所以铸票默认走「有窗口 + 屏幕外」，否则 R1 复用的是假身份票。
    """
    if "HeadlessChrome" in (ua or ""):
        print("\n!! UA 里带 HeadlessChrome —— 假身份。"
              "\n   WAF 不需要多聪明就能看出来，而 R1 会把这个 UA 原样重放到业务层。"
              "\n   改法：去掉 --headless（默认就是有窗口+屏幕外，不弹到眼前）。")


def _new_context(browser, proxy: str):
    """注意：这里**不传** user_agent。

    传了就会出现「UA 头是 A、UA-CH 报 B、TLS 是 C」的三头打架 —— 正是上次 400 的成因。
    让内核自己决定，我们再把它如实记下来。
    """
    kw: Dict[str, Any] = dict(
        ignore_https_errors=True,
        locale="zh-HK",
        timezone_id="Asia/Hong_Kong",
        viewport={"width": 1366, "height": 768},
        device_scale_factor=1,
        extra_http_headers={"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"},
    )
    if proxy:
        kw["proxy"] = {"server": proxy}
    return browser.new_context(**kw)


def _cookie_names(ctx) -> set:
    return {c["name"] for c in ctx.cookies()}


def selftest(engine: str, headless: bool, url: str = "") -> int:
    """零网络（默认）/ 可选 https 自检：验证采集代码在真内核上跑得通。

    传 --selftest-url 时改用该 URL —— 用来验证两件在 data: 页上测不到的事：
      ① page.on("request") 能不能抓到真实文档请求头
      ② navigator.userAgentData 在安全上下文里有没有值
    """
    from align_client import clean_headers, xhr_headers

    pw, b = _launch(engine, headless)
    try:
        ctx = _new_context(b, "")
        pg = ctx.new_page()
        got: List[Dict[str, Any]] = []
        pg.on("request", lambda r: got.append({"url": r.url, "method": r.method,
                                               "rt": r.resource_type,
                                               "headers": dict(r.headers)}))
        target = url or "data:text/html,<title>mint-selftest</title><h1>ok</h1>"
        pg.goto(target, wait_until="commit" if not url else "domcontentloaded",
                timeout=30000)
        nav = pg.evaluate(CAPTURE_JS)
        full = pg.evaluate(CAPTURE_UA_FULL_JS)
        print(f"内核      : {engine} / {b.version} / headless={headless}")
        print(f"UA        : {nav.get('userAgent', '')[:95]}")
        print(f"UA-CH     : {json.dumps((nav.get('uaData') or {}), ensure_ascii=False)}")
        print(f"fullVer   : {json.dumps(full, ensure_ascii=False)[:150]}")
        print(f"languages : {nav.get('languages')}  tz={nav.get('timezone')}")
        print(f"screen    : {nav.get('screen')}  window={nav.get('window')}")
        print(f"webdriver : {nav.get('webdriver')}  rt={nav.get('hasChromeRuntime')}  "
              f"webgl={str(nav.get('webgl'))[:60]}")
        print(f"捕获请求  : {len(got)} 条")
        if got:
            doc = next((g for g in got if g.get("rt") == "document"), got[0])
            navh = clean_headers(doc["headers"])
            print(f"文档请求  : {doc['method']} {doc['url'][:70]}  （{len(navh)} 头）")
            for k in sorted(navh):
                print(f"    {k}: {navh[k][:90]}")
            xh = xhr_headers(navh)
            print(f"XHR 派生  : {len(xh)} 项（改了 "
                  f"{[k for k in xh if navh.get(k) != xh[k]]}）")
        ok = bool(nav.get("userAgent")) and nav.get("webdriver") is False
        print("\n自检" + ("通过：采集链路可用" if ok else "未通过：WebDriver 未隐藏或 UA 为空"))
        return 0 if ok else 1
    finally:
        try:
            b.close()
        finally:
            pw.stop()


def mint(ip_id: str, proxy: str, engine: str, headless: bool,
         wait_ms: int, egress: bool, before_mint: bool = True) -> int:
    """铸票。before_mint=True 时先做一次分诊：死出口直接拦，不启浏览器。"""
    pre = {}
    if before_mint:
        try:
            from preflight import gate
            ok, pre = gate(proxy, log=print)
        except Exception as e:
            print(f"  分诊模块异常（{type(e).__name__}），跳过分诊继续铸票")
            ok = True
        if not ok:
            print(f"\n拦截：出口状态 {pre.get('verdict')} — {pre.get('hint')}")
            print("  没有启动浏览器，也没有发第 2 个请求。")
            print("  下一步：python preflight.py --proxies proxies.txt   # 换个出口再分诊")
            return 44
        print()
    pw, b = _launch(engine, headless)
    t0 = time.time()
    try:
        ctx = _new_context(b, proxy)
        pg = ctx.new_page()

        docs: List[Dict[str, Any]] = []
        allreq: List[str] = []

        def on_req(r):
            allreq.append(f"{r.method} {r.url[:110]}")
            if r.resource_type == "document":
                docs.append({"url": r.url, "method": r.method,
                             "headers": dict(r.headers)})

        pg.on("request", on_req)

        status = None
        try:
            resp = pg.goto(ENTRY, wait_until="commit", timeout=25000)
            status = resp.status if resp else None
        except Exception as e:
            print(f"goto 异常（挑战页常直接掐断导航）：{type(e).__name__}")

        # 等挑战跑完：HIK0 令牌出现即可；不挑战时等 HWWAFSESID。
        # 注意：**认前缀不认后缀** —— 实测会下发 ...xO（109 字符）与 ...xP（490+）
        # 两种（runs/session_form_noquota_w1_171304.json 里甚至并存）。
        # 写死 O 会把下发了 P 的好票误判成「挑战没跑完」。
        from waf_cookies import find_hik0
        deadline = time.time() + wait_ms / 1000
        challenge_seen = status == 412
        while time.time() < deadline:
            names = _cookie_names(ctx)
            if find_hik0(names):
                break
            if not challenge_seen and "HWWAFSESID" in names:
                pg.wait_for_timeout(400)
                break
            if challenge_seen and not find_hik0(names):
                # 挑战 JS 可能还在算，给它时间
                pg.wait_for_timeout(300)
                continue
            pg.wait_for_timeout(250)

        # 挑战通过后页面会 reload，等最终文档稳定
        pg.wait_for_timeout(1500)
        # 采指纹前先等窗口摆好：实测（_win_pos_probe.py）同一坐标下，
        # 窗口刚起来时 outerWidth 可能还是 160（被系统夹着），1~2 秒后才回到 1382。
        # 采早了就会把 160x28 记进票里，而 outerWidth 是 WAF 环境采集项之一。
        try:
            for _ in range(10):
                w, h = pg.evaluate("() => [window.outerWidth, window.outerHeight]")
                if w >= 1200 and h >= 600:
                    break
                pg.wait_for_timeout(250)
            else:
                print(f"! 窗口外框仍异常（{w}x{h}）—— 环境可疑，但票仍生成")
        except Exception:
            pass
        try:
            final_url = pg.url
            final_status = pg.evaluate("() => document.readyState")
        except Exception:
            final_url, final_status = "", ""

        navfacts = {}
        try:
            navfacts = pg.evaluate(CAPTURE_JS) or {}
            full = pg.evaluate(CAPTURE_UA_FULL_JS)
            if full:
                navfacts["uaData_full"] = full
        except Exception as e:
            print(f"指纹采集异常：{type(e).__name__} {str(e)[:80]}")

        cookies = ctx.cookies()
        names = {c["name"] for c in cookies}
        hik0_names = find_hik0(names)
        has_hik0 = bool(hik0_names)
        has_ses = "HWWAFSESID" in names

        # 文档请求取「最后一次」——挑战通过后的 reload 才是真正被接受的那次
        doc = docs[-1] if docs else {"url": ENTRY, "method": "GET", "headers": {}}
        hdrs = doc.get("headers") or {}
        ua = navfacts.get("userAgent") or ""

        # 一致性检查：三处 UA 必须相同
        h_ua = next((v for k, v in hdrs.items() if k.lower() == "user-agent"), "")
        ua_ch = ""
        for k, v in hdrs.items():
            if k.lower() == "sec-ch-ua":
                ua_ch = v
        major = re.search(r"Chrome/(\d+)", ua)
        ver = major.group(1) if major else ""

        egress_ip = ""
        if egress:
            try:
                egress_ip = pg.evaluate(
                    "async () => (await (await fetch('https://ipinfo.io/json')).json()).ip")
            except Exception:
                egress_ip = ""

        t = {
            "ip_id": ip_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "proxy": proxy,
            "egress_ip": egress_ip or "",
            "ua": ua,
            "ua_meta": navfacts.get("uaData"),
            "ua_meta_full": navfacts.get("uaData_full"),
            "nav": {k: v for k, v in navfacts.items()
                    if k not in ("userAgent", "uaData", "uaData_full")},
            "headers": hdrs,
            "cookies": cookies,
            "engine": {"name": engine, "version": b.version, "headless": headless},
            "entry": {"url": ENTRY, "status": status, "final_url": final_url,
                      "doc_requests": len(docs), "total_requests": len(allreq),
                      "challenge_seen": challenge_seen,
                      "has_hik0": has_hik0, "hik0_names": hik0_names,
                      "has_hwwafsesid": has_ses,
                      "cost_s": round(time.time() - t0, 2)},
            "impersonate": "",
            "probe": {},
            "preflight": pre,
        }
        p = TICKET_DIR / f"{ip_id}.json"
        TICKET_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"\n内核      : {engine} / {b.version} / headless={headless}  "
              f"{t['entry']['cost_s']}s")
        print(f"导航      : 首包 {status}  挑战={challenge_seen}  "
              f"文档请求 {len(docs)} 次  总请求 {len(allreq)} 次")
        print(f"cookie    : {len(cookies)} 条  HIK0={'有' if has_hik0 else '无'}  "
              f"HWWAFSESID={'有' if has_ses else '无'}")
        print(f"UA        : {ua[:90]}")
        print(f"UA 一致性 : 头={h_ua[:50] or '—'}  navigator={ua[:50] or '—'}  "
              f"{'一致 ✅' if h_ua and h_ua == ua else '不一致 ⚠️'}")
        print(f"sec-ch-ua : {ua_ch[:90] or '（浏览器未发，正常：低熵提示按需下发）'}")
        print(f"头部项数  : {len(hdrs)}   画像候选见 align_client")
        print(f"→ {p.relative_to(BASE)}")
        headless_ua_warning(ua, headless)
        if "HeadlessChrome" in (ua or ""):
            t["ua_is_headless"] = True
            p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")

        if not cookies:
            print("\n!! 一条 cookie 都没拿到 —— 出口可能直接被掐断，换出口")
            return 42
        if challenge_seen and not has_hik0:
            print("\n!! 挑战没跑完（无 HIK0）—— 这张票不是「过挑战之后」的会话，"
                  "纯 HTTP 复用必 412")
            return 41
        if not hdrs:
            print("\n!! 没抓到文档请求头 —— 无法对齐，票不可用")
            return 43
        print("\n下一步：python align_client.py --ticket %s --probe" % p.name)
        return 0
    finally:
        try:
            b.close()
        finally:
            pw.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="R1 铸票并对齐（记录真实请求头）")
    ap.add_argument("--ip-id", default="direct", help="票的标识（建议用出口/工人名）")
    ap.add_argument("--proxy", default="", help="出口；留空=直连（票与出口绑定）")
    ap.add_argument("--engine", default="patchright", choices=["playwright", "patchright"],
                    help="patchright 才能 headless 过挑战（实测 412→200）")
    ap.add_argument("--headless", nargs="?", const="true", default="false",
                    help="默认 false=有窗口但挪到屏幕外（UA 正常）；"
                         "true=真无头，但 UA 会带 HeadlessChrome，不建议铸 R1 票")
    ap.add_argument("--headful", action="store_true", help="显式有窗口（默认即是）")
    ap.add_argument("--wait-ms", type=int, default=20000, help="等挑战通过的上限")
    ap.add_argument("--egress", action="store_true",
                    help="顺带测出口 IP（多打一次第三方请求，默认关）")
    ap.add_argument("--before-mint", nargs="?", const="true", default="true",
                    help="铸票前先分诊（默认开）。死出口只摸 1 次就被拦下，不启浏览器")
    ap.add_argument("--no-before-mint", action="store_true", help="关掉前置分诊")
    ap.add_argument("--selftest", action="store_true", help="零网络自检")
    ap.add_argument("--selftest-url", default="",
                    help="配合 --selftest：换成 https 页，验证抓头与 UA-CH（如 https://example.com）")
    a = ap.parse_args()
    hl = str(a.headless).lower() != "false"
    if a.headful:
        hl = False

    if a.selftest:
        return selftest(a.engine, hl, a.selftest_url)
    bm = not a.no_before_mint and str(a.before_mint).lower() != "false"
    return mint(a.ip_id, a.proxy, a.engine, hl, a.wait_ms, a.egress, before_mint=bm)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
