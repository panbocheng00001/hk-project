# -*- coding: utf-8 -*-
"""waf_replay.py —— 用浏览器票复验：缺头 / 指纹 / 动态参数，逐个排除 400 的原因"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from curl_cffi import requests as creq

URL = "https://abs3.td.gov.hk/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
FULL = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def run(tag, cookies, headers, proxy, imp="chrome"):
    kw = {"impersonate": imp, "timeout": 25, "allow_redirects": False,
          "verify": False, "headers": headers}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    s = creq.Session(**kw)
    for c in cookies:
        try:
            s.cookies.set(c["name"], c["value"], domain=c["domain"] or "abs3.td.gov.hk", path=c.get("path") or "/")
        except Exception:
            pass
    t0 = time.time()
    try:
        r = s.get(URL)
        b = (r.content or b"")
        print(f"  {tag:38} {r.status_code} len={len(b):6} {time.time()-t0:.2f}s {b[:60]!r}")
        return r
    except Exception as e:
        print(f"  {tag:38} EXC {type(e).__name__}: {str(e)[:90]}")
        return None


def main():
    f = sys.argv[1] if len(sys.argv) > 1 else "runs/tokens/ip_test.json"
    proxy = sys.argv[2] if len(sys.argv) > 2 else ""
    d = json.loads(Path(f).read_text(encoding="utf-8"))
    ck = d["cookies"]
    print(f"票: {f}  proxy: {proxy or '直连'}  cookies={[c['name'] for c in ck]}")
    run("1 只有 UA + HIK0", ck, {"User-Agent": UA}, proxy)
    run("2 全套浏览器头", ck, FULL, proxy)
    run("3 全套头 + chrome124", ck, FULL, proxy, imp="chrome124")
    run("4 全套头 + chrome120", ck, FULL, proxy, imp="chrome120")
    run("5 全套头 + safari", ck, FULL, proxy, imp="safari17_0")
    print("\n判读：全是 400 → 票与 TLS/浏览器指纹绑定，纯 HTTP 复用不通；要 200 → 纯 HTTP 可用")


if __name__ == "__main__":
    main()
