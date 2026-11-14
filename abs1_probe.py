# -*- coding: utf-8 -*-
"""abs1_probe.py —— 绕开 abs3/abs4 的 WAF，直接用放行票打 abs1 业务层

实测（19:25）：
  abs1.td.gov.hk = 202.128.247.53，直连 200（"Powered by eGIS~"），**没有华为 WAF**（无 Server: CW）
  业务入口不带票 → 302 回 abs3（应用层检查，不是 WAF 挑战）
  → 只要手里有 Queue-it 放行票，就能全程纯 HTTP 打 abs1，一次 412 都不用碰

用法：
  python abs1_probe.py --cookies runs/rev/browser_cookies.json
  python abs1_probe.py --cookies runs/tokens/xxx.json --proxy http://ip:port
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from curl_cffi import requests as creq

BASE = "https://abs1.td.gov.hk"
ENTRY = (BASE + "/tdab2/tdabs_di_external/AdminServlet"
         "?cmd=cmdIntoSelectAuthDI&func=apply&auth=withOutIas&country=mainland&lang=sc")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def load_cookies(path: str) -> list[dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict):
        for k in ("cookies", "jar", "data"):
            if isinstance(d.get(k), list):
                return d[k]
        return []
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookies", required=True)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--url", default=ENTRY)
    a = ap.parse_args()

    ck = load_cookies(a.cookies)
    print(f"票: {a.cookies}  {len(ck)} 条: {[c.get('name') for c in ck]}")

    kw = {"impersonate": "chrome", "timeout": 25, "verify": False,
          "allow_redirects": False, "headers": {"User-Agent": UA}}
    if a.proxy:
        kw["proxies"] = {"http": a.proxy, "https": a.proxy}
    s = creq.Session(**kw)
    for c in ck:
        try:
            s.cookies.set(c["name"], c["value"],
                          domain=c.get("domain") or "abs1.td.gov.hk",
                          path=c.get("path") or "/")
        except Exception:
            pass

    r = s.get(a.url)
    body = (r.content or b"")
    print(f"\nGET {a.url}\n  → {r.status_code}  len={len(body)}")
    for k in ("Location", "Server", "Set-Cookie", "Content-Type"):
        if r.headers.get(k):
            print(f"  {k}: {str(r.headers.get(k))[:160]}")
    txt = body.decode("utf-8", "ignore")
    if txt:
        # 判断是什么页面
        marks = {
            "选择国家页": "SelectCountry" in txt or "country" in txt.lower(),
            "第1步表单": "cmdGetOfficeQuotaDIAction" in txt or "storedAppointment" in txt,
            "无名额": "没有可供预约" in txt or "沒有可供預約" in txt,
            "登录页": "login" in txt.lower() or "登入" in txt,
        }
        print("  页面特征:", [k for k, v in marks.items() if v] or "(未识别)")
        print("  body head:", txt[:200].replace("\n", " "))
    if r.status_code == 200 and len(body) > 3000:
        out = Path("runs/abs1_entry.html")
        out.write_text(txt, encoding="utf-8")
        print(f"  → 落盘 {out}")
        print("\n★ abs1 直连 200：纯 HTTP 通路成立，不需要过 WAF")


if __name__ == "__main__":
    main()
