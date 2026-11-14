# -*- coding: utf-8 -*-
"""_drill_412.py —— 412 挑战的离线复放（零目标访问）

目的：在不碰 abs3 的前提下，验一件事 ——
     **这个内核（patchright 156 / CfT）能不能把 357KB 混淆挑战 JS 跑起来**
     具体看到三档：① 解出配置 ② 写出 cookie ③ 发出验证请求

素材（都是 11-11 当天抓下来的）：
    runs/20261109-163025/w1/01_entry.html   412 挑战页（38 KB）
    runs/rev/abs3_waf_js.js                 挑战 JS（304 KB / 357557 字节）

做法：
    本地 HTTP 服务把页面还原，把 <script src="/RGOj.../xxx.js"> 改指到本地副本，
    然后让真内核打开 http://127.0.0.1:PORT/ ，全程记录：
      · document.cookie 有没有被写
      · $_ts 被改了哪些键（nsd/cd → lcd/cp/jf/aebi/scj）
      · 有没有发出 fetch（它要拿这个去服务端换确认）
      · 控制台报错 / 页面异常 / 是否卡住（反调试）

局限（先说清楚，别把结论用过头）：
    · 页面里的 $_ts.cd 与 meta 是 11-11 那一次的，服务端早已换代 → 验的是"能否执行"，
      不是"能否换到真票"
    · fetch 会 404（本地没有那个端点），所以"换票成功"这条永远为假，这是预期的
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PAGE_SRC = BASE / "runs" / "20261109-163025" / "w1" / "01_entry.html"
JS_SRC = BASE / "runs" / "rev" / "abs3_waf_js.js"
OUT_DIR = BASE / "runs" / "drill"
CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")

ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
        "--no-default-browser-check", "--disable-dev-shm-usage",
        "--window-position=-32000,-32000", "--window-size=1366,820",
        "--js-flags=--max-old-space-size=512"]


# ---------------------------------------------------------------- 还原页面
def build_page() -> str:
    html = io.open(PAGE_SRC, encoding="utf-8", errors="replace").read()
    # 挑战 JS 改到本地副本
    html, n1 = re.subn(r'(src=")/RGOj2pQjqEKb/8qcykgusbcwc\.5057567\.js(")',
                       r"\1/chall.js\2", html)
    # SPA 主包我们没存，指到空 stub，免得多出无意义的 404
    html, n2 = re.subn(r'<script[^>]*src="/static/js/main\.[^"]*"[^>]*>\s*</script>',
                       "", html)
    print(f"  挑战 JS 重写 {n1} 处；移除 SPA 主包 {n2} 处")
    return html


def serve(html: str, js: str):
    class H(BaseHTTPRequestHandler):
        # HTTP/1.0 + 显式 close：实测 HTTP/1.1 keep-alive 下浏览器会取消大响应
        # （304KB 的 chall.js），解析器一直阻塞在 <script>，挑战 JS 根本没执行。
        protocol_version = "HTTP/1.0"

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except Exception:
                pass

        def do_GET(self):
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            elif p.endswith("chall.js"):
                self._send(js.encode("utf-8"), "application/javascript; charset=utf-8")
            else:
                # 其余一律 200 空脚本/空 JSON，减少噪音
                self._send(b"", "application/javascript")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            SEEN_POST.append({"path": self.path, "len": len(body),
                              "body": body[:400].decode("utf-8", "replace")})
            self._send(b'{"ok":true}', "application/json")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


SEEN = []
SEEN_POST = []


# ---------------------------------------------------------------- 观测量
PROBE_JS = r"""
() => {
  const t = window.$_ts;
  const keys = t ? Object.keys(t) : [];
  const scripts = Array.from(document.scripts).map(
    s => [s.src ? s.src.split('/').pop().slice(0, 20) : 'inline',
          (s.textContent || '').length]);
  return {
    cookie: document.cookie,
    ts_keys: keys,
    ts_type: typeof window.$_ts,
    c3_type: typeof window._$c3,
    nsd: t ? t.nsd : null,
    cd_len: t && t.cd ? String(t.cd).length : 0,
    decoded: t ? {lcd: !!t.lcd, cp: !!t.cp, jf: !!t.jf, aebi: !!t.aebi, scj: !!t.scj} : null,
    ready: document.readyState,
    hasTs: !!t,
    scripts: scripts,
    n_scripts: document.scripts.length,
  };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="412 挑战离线复放")
    ap.add_argument("--engine", default="patchright", choices=["patchright", "playwright"])
    ap.add_argument("--seconds", type=float, default=18.0)
    ap.add_argument("--headless", action="store_true", help="默认有窗口+屏幕外")
    ap.add_argument("--keep", action="store_true", help="把改写后的页面存到 runs/drill/")
    a = ap.parse_args()

    if not PAGE_SRC.exists() or not JS_SRC.exists():
        print(f"素材缺失：{PAGE_SRC.exists()=} {JS_SRC.exists()=}")
        return 2
    print(f"素材：页面 {PAGE_SRC.stat().st_size} B   JS {JS_SRC.stat().st_size} B")
    html = build_page()
    js = io.open(JS_SRC, encoding="utf-8", errors="replace").read()

    if a.keep:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "replay.html").write_text(html, encoding="utf-8")
        print(f"  改写后页面 → {(OUT_DIR/'replay.html').relative_to(BASE)}")

    srv = serve(html, js)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"本地还原：{url}\n")

    if a.engine == "patchright":
        from patchright.sync_api import sync_playwright
    else:
        from playwright.sync_api import sync_playwright

    timeline = []
    errs = []
    console = []
    t0 = time.time()

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, headless=a.headless, args=ARGS,
                               ignore_default_args=["--enable-automation"])
        print(f"内核：{a.engine} / {b.version} / headless={a.headless}\n")
        ctx = b.new_context(ignore_https_errors=True,
                            viewport={"width": 1366, "height": 768},
                            locale="zh-HK", timezone_id="Asia/Hong_Kong")
        pg = ctx.new_page()
        pg.set_default_timeout(4000)
        pg.on("console", lambda m: console.append(f"{m.type}: {m.text[:160]}"))
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("request", lambda r: SEEN.append({"m": r.method, "u": r.url[:120],
                                                "rt": r.resource_type}))

        st = None
        try:
            resp = pg.goto(url, wait_until="commit", timeout=15000)
            st = resp.status if resp else None
        except Exception as e:
            errs.append(f"goto: {type(e).__name__}: {str(e)[:120]}")
        timeline.append((round(time.time() - t0, 2), f"goto → {st}"))

        hung = False
        last = None
        first = True
        while time.time() - t0 < a.seconds:
            time.sleep(0.6)
            try:
                s = pg.evaluate(PROBE_JS)
            except Exception as e:
                hung = True
                timeline.append((round(time.time() - t0, 2),
                                 f"evaluate 超时/失败：{type(e).__name__} —— 疑似反调试暂停"))
                break
            if first:
                first = False
                timeline.append((round(time.time() - t0, 2),
                                 f"首帧 ts_type={s.get('ts_type')} c3_type={s.get('c3_type')} "
                                 f"ready={s.get('ready')} n_scripts={s.get('n_scripts')} "
                                 f"scripts={s.get('scripts')}"))
            sig = (s.get("cookie"), tuple(s.get("ts_keys") or []), s.get("cd_len"))
            if sig != last:
                last = sig
                timeline.append((round(time.time() - t0, 2),
                                 f"cookie={s.get('cookie')!r} ts_keys={s.get('ts_keys')} "
                                 f"decoded={s.get('decoded')}"))
            if s.get("cookie"):
                timeline.append((round(time.time() - t0, 2), "★ 已写出 cookie"))
                break

        peak = None
        try:
            peak = pg.evaluate("() => performance.memory ? performance.memory.usedJSHeapSize / 1048576 : null")
        except Exception:
            pass
        try:
            b.close()
        except Exception:
            pass

    srv.shutdown()

    print("=== 时间线 ===")
    for t, msg in timeline:
        print(f"  {t:6.2f}s  {msg}")
    print(f"\n=== 请求（前 12）===")
    for r in SEEN[:12]:
        print(f"  {r['m']:4} {r['rt']:10} {r['u']}")
    print(f"  合计 {len(SEEN)} 条；其中 POST {len(SEEN_POST)} 条"
          + (f" → {json.dumps(SEEN_POST[:2], ensure_ascii=False)[:300]}" if SEEN_POST else ""))
    print(f"\n=== 控制台（前 8）=== {console[:8] or '（无）'}")
    print(f"=== 页面异常 === {errs[:5] or '（无）'}")
    if peak:
        print(f"=== 页面 JS 堆峰值 ≈ {peak:.1f} MB ===")

    # ---- 判定
    wrote_cookie = any("★" in m for _, m in timeline)
    decoded = any("decoded" in m and ("'lcd': True" in m or "'cp': True" in m) for _, m in timeline)
    posted = len(SEEN_POST) > 0

    # 用共用口径解析最后一份 cookie，认出 HIK0 究竟是哪个后缀
    last_cookie = ""
    for _, m in timeline:
        if m.startswith("cookie="):
            last_cookie = m.split("cookie=", 1)[1].split(" ts_keys")[0].strip("'\"")
    pairs = []
    for seg in (last_cookie or "").split(";"):
        if "=" in seg:
            n, v = seg.split("=", 1)
            pairs.append({"name": n.strip(), "value": v.strip()})
    try:
        from waf_cookies import strength
        st = strength(pairs)
    except Exception:
        st = {"level": "?", "names": [], "lens": []}
    print(f"\n=== HIK0 判定 === level={st['level']} names={st['names']} 长度={st['lens']}")
    print(f"=== cookie 总数 === {len(pairs)} 条")

    print("\n=== 判定 ===")
    if hung:
        print("  ❌ 卡住：疑似反调试（debugger）在 evaluate 时被触发 → 这个引擎不适合过挑战")
    elif st["level"] == "strong":
        print(f"  ✅ 挑战 JS 完整跑完并写出令牌（{st['names']}，{st['lens']} 字符）")
        print("     页面同时被整体重写（n_scripts=0），符合挑战完成后的形态。")
        print("     没看到 POST 是预期的：令牌靠 document.cookie + 重载提交，离线无服务端点头。")
    elif wrote_cookie and posted:
        print("  ✅ 跑通到「写 cookie + 发验证请求」")
    elif wrote_cookie:
        print("  ⚠️ 写出了 cookie，但不是 HIK0 令牌 → 需在真站复核")
    elif decoded:
        print("  ⚠️ 配置已解码（$_ts 被改），但没写 cookie → 可能缺服务端会话")
    else:
        print("  ❌ 没有明显进展：$_ts 未被改、无 cookie。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"drill-{time.strftime('%H%M%S')}-{a.engine}.json"
    out.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "engine": a.engine,
        "version": None, "headless": a.headless, "url": url,
        "timeline": timeline, "requests": SEEN[:60], "posts": SEEN_POST,
        "console": console[:40], "errors": errs[:20], "hung": hung,
        "verdict": {"wrote_cookie": wrote_cookie, "decoded": decoded, "posted": posted},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out.relative_to(BASE)}")
    return 0 if (wrote_cookie or posted) else 3


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
