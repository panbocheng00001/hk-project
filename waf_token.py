# -*- coding: utf-8 -*-
"""waf_token.py —— 用无头 Chrome 过一次华为云 WAF 412 挑战，产出可复用的 cookie 票

为什么是它：
  412 是华为云 WAF 动态防护（$_ts.nsd/.cd + 混淆 JS 反调试）。
  纯 vm 伪造环境会掉进它的死循环（实测 OOM 4GB），不是几个小时能补完的活；
  而无头 Chrome 执行真 JS，一次 3~5 秒拿 HIK0kat3rt9xO —— 这是「脚本化」的最小成本路径。

  拿到票之后，后续全部走纯 HTTP（curl_cffi / sniper.py），不再开浏览器。

跑法：
  # 直连一次
  python waf_token.py
  # 走某个出口（每个 IP 各跑一次，票绑 IP）
  python waf_token.py --proxy http://1.2.3.4:8080 --out runs/tokens/ip_1.json
  # 批量：每个代理一行
  python waf_token.py --proxies runs/fleet/proxies.txt --outdir runs/tokens
  # 只看不跑（校验已有 cookie 还有效）
  python waf_token.py --verify runs/tokens/ip_1.json

产物：
  runs/tokens/<name>.json   {cookies:[...], proxy, ua, got_hik0, at, verify:{...}}
  可直接喂： python sniper.py --cookies runs/tokens/ip_1.json --step1
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path

URL = "https://abs3.td.gov.hk/?lang=sc"
CHROME = str(Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-HK','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
const _q = window.navigator.permissions && window.navigator.permissions.query;
if (_q) window.navigator.permissions.query = (p) => (
  p && p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : _q(p));
"""


def cookies_to_json(ctx) -> list[dict]:
    out = []
    for c in ctx.cookies():
        out.append({"name": c["name"], "value": c["value"], "domain": c.get("domain", ""),
                    "path": c.get("path", "/"), "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False), "expires": c.get("expires", -1)})
    return out


def verify_http(cookies: list[dict], url: str = URL, proxy: str = "") -> dict:
    try:
        from curl_cffi import requests as creq
    except ImportError:
        return {"err": "缺 curl_cffi"}
    kw = {"impersonate": "chrome", "timeout": 20, "allow_redirects": False, "verify": False,
          "headers": {"User-Agent": UA}}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    s = creq.Session(**kw)
    for c in cookies:
        try:
            s.cookies.set(c["name"], c["value"], domain=c.get("domain") or None, path=c.get("path") or "/")
        except Exception:
            pass
    t0 = time.time()
    try:
        r = s.get(url)
        body = (r.content or b"").decode("utf-8", "ignore")
        return {"status": r.status_code, "len": len(r.content or b""),
                "sec": round(time.time() - t0, 2),
                "challenge": ("$_ts" in body or "nsd=" in body),
                "has_hik0": any(k.startswith("HIK0kat3rt9x") for k in s.cookies)}
    except Exception as e:
        return {"err": f"{type(e).__name__}: {str(e)[:120]}"}


def mint(proxy: str, url: str, headless: bool, wait: float, out: Path, name: str) -> dict:
    from playwright.sync_api import sync_playwright
    rec = {"name": name, "proxy": proxy, "url": url, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "ua": UA, "cookies": [], "got_hik0": False}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME, headless=headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check"],
        )
        ctx = browser.new_context(
            proxy={"server": proxy} if proxy else None,
            user_agent=UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
            viewport={"width": 1366, "height": 768}, color_scheme="light",
            ignore_https_errors=True,
        )
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()
        t0 = time.time()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            rec["err"] = f"goto: {type(e).__name__}: {str(e)[:120]}"

        deadline = time.time() + wait
        last = {}
        while time.time() < deadline:
            ck = {c["name"]: c["value"] for c in ctx.cookies()}
            last = ck
            if any(k.startswith("HIK0kat3rt9x") for k in ck):
                break
            try:
                page.wait_for_timeout(500)
            except Exception:
                break
        rec["sec"] = round(time.time() - t0, 2)
        rec["cookies"] = cookies_to_json(ctx)
        rec["got_hik0"] = any(str(c["name"]).startswith("HIK0kat3rt9x") for c in rec["cookies"])
        rec["names"] = sorted({c["name"] for c in rec["cookies"]})
        try:
            rec["final_url"] = page.url
            rec["title"] = page.title()[:80]
            rec["body_len"] = len(page.content() or "")
        except Exception:
            pass
        browser.close()

    # 立刻用纯 HTTP 复验这张票
    rec["verify"] = verify_http(rec["cookies"], url, proxy)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--proxies", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--outdir", default="runs/tokens")
    ap.add_argument("--wait", type=float, default=25.0, help="等 HIK0 的最长秒数")
    ap.add_argument("--headful", action="store_true", help="有头模式（headless 被识别时用）")
    ap.add_argument("--verify", default="", help="只校验已有 cookie 文件")
    a = ap.parse_args()

    if a.verify:
        data = json.loads(Path(a.verify).read_text(encoding="utf-8"))
        v = verify_http(data.get("cookies", []), a.url, a.proxy or data.get("proxy", ""))
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0

    proxies = [a.proxy] if a.proxy else [""]
    if a.proxies and Path(a.proxies).exists():
        proxies = [l.strip() for l in Path(a.proxies).read_text(encoding="utf-8").splitlines() if l.strip()]

    ok = 0
    for i, px in enumerate(proxies, 1):
        name = f"ip_{i:02d}" if px else "direct"
        out = Path(a.out) if (a.out and len(proxies) == 1) else Path(a.outdir) / f"{name}.json"
        print(f"\n[{i}/{len(proxies)}] {px or '直连'} → {a.url}")
        rec = mint(px, a.url, not a.headful, a.wait, out, name)
        v = rec.get("verify", {})
        flag = "★" if rec.get("got_hik0") else " "
        print(f"  {flag} HIK0={rec.get('got_hik0')}  {rec.get('sec')}s  cookies={len(rec.get('cookies', []))}"
              f"  final={rec.get('final_url', '')[:60]}")
        print(f"    verify: {json.dumps(v, ensure_ascii=False)}")
        if rec.get("got_hik0") and v.get("status") in (200, 302):
            ok += 1
        print(f"    → {out}")
    print(f"\n=== 成功票 {ok}/{len(proxies)} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
