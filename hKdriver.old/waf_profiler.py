# -*- coding: utf-8 -*-
"""waf_profiler.py —— 第 3 条：curl-impersonate 系（curl_cffi 的各 TLS profile）+ 浏览器 xP

思路：patchright 已经证明"真浏览器指纹 + xP = 200"。
     如果 curl_cffi 的某个 profile 也能 200，就能纯 HTTP 复用浏览器票。
     如果全 400，说明 WAF 拉黑的是"伪造指纹库"本身，这条路封死。

用法：
  python waf_profiler.py --cookie runs/tokens/patchright_175.6.31.214.json --proxy http://ip:port
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from curl_cffi import requests as creq

URL = "https://abs3.td.gov.hk/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PROFILES = ["chrome", "chrome136", "chrome133a", "chrome131", "chrome124",
            "chrome120", "chrome110", "safari17_0", "safari18_0",
            "firefox133", "firefox135", "edge101"]


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    ck = d.get("cookies", d if isinstance(d, list) else [])
    return [c for c in ck if c.get("name")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", required=True)
    ap.add_argument("--proxy", default="")
    a = ap.parse_args()
    ck = load(a.cookie)
    print(f"票: {a.cookie}  {len(ck)} 条  xP={[len(c['value']) for c in ck if c['name'].startswith('HIK0kat3rt9xP')]}")
    print(f"proxy={a.proxy or '直连'}\n")
    hit = []
    for prof in PROFILES:
        try:
            kw = {"impersonate": prof, "timeout": 20, "verify": False,
                  "allow_redirects": False, "headers": {"User-Agent": UA}}
            if a.proxy:
                kw["proxies"] = {"http": a.proxy, "https": a.proxy}
            s = creq.Session(**kw)
            for c in ck:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain") or "abs3.td.gov.hk",
                              path=c.get("path") or "/")
            t0 = time.time()
            r = s.get(URL)
            b = r.content or b""
            mark = "★" if r.status_code in (200, 302) else ""
            print(f"  {prof:14} {r.status_code}  len={len(b):6}  {time.time()-t0:.2f}s {mark}")
            if r.status_code in (200, 302):
                hit.append(prof)
        except Exception as e:
            print(f"  {prof:14} EXC {type(e).__name__}: {str(e)[:70]}")
        time.sleep(0.8)
    print("\n=== 结论 ===")
    if hit:
        print(f"  ★ 这些 profile 能过: {hit}")
    else:
        print("  全 400/412：WAF 拉黑的是伪造指纹库本身，curl-impersonate 这条路封死")


if __name__ == "__main__":
    main()
