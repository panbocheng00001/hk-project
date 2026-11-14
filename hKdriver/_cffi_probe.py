# -*- coding: utf-8 -*-
"""最小化定位：curl_cffi 0.13 走带认证 HTTP 代理到 abs3 的组合矩阵。"""
from __future__ import annotations

import sys
import time

from curl_cffi import CurlHttpVersion, requests as cr

HOST = "http://14.215.163.195:2018"
AUTH = ("jlipiLP7OB", "boOKSVho")
URL_URI = "http://jlipiLP7OB:boOKSVho@14.215.163.195:2018"
URL = "https://abs3.td.gov.hk/?lang=sc"


def run(tag: str, *, proxy=None, proxy_auth=None, impersonate="chrome136",
        http_version=None, verify=False):
    t0 = time.time()
    kw = {}
    if proxy:
        kw["proxy"] = proxy
    if proxy_auth:
        kw["proxy_auth"] = proxy_auth
    if impersonate:
        kw["impersonate"] = impersonate
    if http_version is not None:
        kw["http_version"] = http_version
    try:
        s = cr.Session(verify=verify, **({"impersonate": impersonate} if impersonate else {}))
        s.get(URL, timeout=25, allow_redirects=False, **kw)
        # 上面若代理参数只在 Session 级传，这次不会生效；改为逐请求显式传，重来
        r = s.get(URL, timeout=25, allow_redirects=False, **kw)
        body = r.text or ""
        print(f"[{tag}] -> {r.status_code} len={len(body)} {(time.time()-t0)*1000:.0f}ms "
              f"cookies={[c.name for c in s.cookies.jar]}")
        return r.status_code
    except Exception as e:
        print(f"[{tag}] !! {type(e).__name__}: {str(e)[:150]} ({(time.time()-t0)*1000:.0f}ms)")
        return None


def main() -> int:
    res = {}
    res["uri+chrome+h1"] = run("uri+chrome+h1", proxy=URL_URI, impersonate="chrome136",
                               http_version=CurlHttpVersion.V1_1)
    res["split+chrome+h1"] = run("split+chrome+h1", proxy=HOST, proxy_auth=AUTH,
                                 impersonate="chrome136", http_version=CurlHttpVersion.V1_1)
    res["uri+noimp+h1"] = run("uri+noimp+h1", proxy=URL_URI, impersonate=None,
                              http_version=CurlHttpVersion.V1_1)
    res["split+chrome+default"] = run("split+chrome+default", proxy=HOST, proxy_auth=AUTH,
                                      impersonate="chrome136")
    print(res)
    return 0 if any(v == 200 for v in res.values()) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
