# -*- coding: utf-8 -*-
"""edge_path_probe.py —— 路径/后缀兼容性探测矩阵

用途
----
给同一批接口路径发同一组探测请求，用响应码判断请求停在哪一层，从而区分
「网关层就没放行」和「到了后端但路由不存在」。产出是一张排查表。

判定
----
  412 = 仍在挑战层（请求没进业务）
  400 = 网关判非法（后缀未匹配放行规则，或后端拒）
  404 = 已到后端但路径不存在
  200 = 请求正常返回

用法：
  python edge_path_probe.py --proxy http://ip:port
  python edge_path_probe.py --proxy http://ip:port --only 0,2,4
"""
from __future__ import annotations

# TLS 画像说明
# -------------
# curl_cffi 支持指定 TLS/HTTP2 特征预设（对应库参数）。
# 本项目**不默认启用**这一项：
#   · 默认用 curl_cffi 自身的 TLS 栈发请求；
#   · 需要时通过环境变量 BOOKING_TLS_PROFILE 显式指定（例如 chrome131）。
# 如果某个出口在默认 TLS 下拿不到正常响应，说明该出口与站点不兼容，
# 正确做法是换出口或回到浏览器路径，而不是把 TLS 画像改成别的浏览器。

def tls_profile(explicit: str = "") -> str:
    """解析本次要用的 TLS 画像。默认空（用 curl_cffi 默认栈）。

    显式指定优先级：参数 > 环境变量 BOOKING_TLS_PROFILE。
    返回空字符串表示"不指定"。
    """
    import os
    val = (explicit or os.environ.get("BOOKING_TLS_PROFILE", "") or "").strip()
    if val.lower() in ("none", "off", "0", "default"):
        return ""
    return val


_tls = tls_profile()
import argparse, sys, time
from curl_cffi import requests as creq

HOST3, HOST4 = "https://abs3.example.test", "https://abs4.example.test"
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
        kw = {**({"impersonate": _tls} if _tls else {}), "timeout": 20, "verify": False,
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
