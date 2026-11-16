# -*- coding: utf-8 -*-
"""probe412.py —— 定位 412 的边界：哪一层被挑战、哪种入口不挑战
跑法：python probe412.py
"""
from __future__ import annotations
import json, sys, time
from curl_cffi import requests as creq

ABS3 = "https://abs3.td.gov.hk"
ABS4 = "https://abs4.td.gov.hk"
C = "tdabsditest"
E = "tdabsd261110kla"

MARK = ("$_ts", "nsd=", "HIK0kat3rt9xO", "HWWAFSESID")
IMP = "chrome"

def show(tag, r, t0):
    body = r.content or b""
    head = body[:400].decode("utf-8", "ignore")
    chal = any(m in head for m in ("$_ts", "nsd="))
    print(f"\n### {tag}")
    print(f"    {r.status_code}  len={len(body)}  {time.time()-t0:.2f}s  challenge={chal}")
    sc = r.headers.get("set-cookie", "")
    for line in str(r.headers).splitlines():
        if line.lower().startswith(("set-cookie", "server", "x-waf", "x-via", "x-cache", "eagleid")):
            print("    H:", line.strip())
    if not chal:
        print("    B:", head.replace("\n", " ")[:300])
    return r

def req(name, method, url, **kw):
    t0 = time.time()
    try:
        s = kw.pop("session", None)
        if s is None:
            s = creq.Session(impersonate=IMP)
        r = s.request(method, url, timeout=kw.pop("timeout", 12), **kw)
        return show(name, r, t0)
    except Exception as e:
        print(f"\n### {name}\n    EXC {type(e).__name__}: {str(e)[:160]}  {time.time()-t0:.2f}s")
        return None

def main():
    print("=" * 70, "\nA. HTML 导航层（abs3）")
    s1 = creq.Session(impersonate=IMP)
    req("A1 GET abs3/?lang=sc", "GET", ABS3 + "/?lang=sc", session=s1)
    req("A2 GET abs3/SelectCountry", "GET",
        ABS3 + "/SelectCountry?lang=sc&actionType=apply&authMethod=withOutIas", session=s1)
    req("A3 GET abs4/ (HTML)", "GET", ABS4 + "/", session=s1)

    print("\n" + "=" * 70, "\nB. spa-api 层（关键：是否也不挑战）")
    s2 = creq.Session(impersonate=IMP)
    req("B0 GET abs3/?lang=sc (先取会话)", "GET", ABS3 + "/?lang=sc", session=s2)
    print("    cookies:", list(s2.cookies.keys()))
    req("B1 POST enqueue (同会话)", "POST",
        f"{ABS4}/spa-api/queue/{C}/{E}/enqueue",
        session=s2, json={},
        headers={"Accept": "application/json", "Origin": ABS3, "Referer": ABS3 + "/"})

    s3 = creq.Session(impersonate=IMP)
    req("B2 POST enqueue (裸会话 无cookie)", "POST",
        f"{ABS4}/spa-api/queue/{C}/{E}/enqueue",
        session=s3, json={},
        headers={"Accept": "application/json", "Content-Type": "application/json"})

    print("\n" + "=" * 70, "\nC. 头变形（看 WAF 按什么放行）")
    for name, hd in [
        ("C1 XFF 127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}),
        ("C2 XFF 10.0.0.8", {"X-Forwarded-For": "10.0.0.8"}),
        ("C3 X-Real-IP", {"X-Real-IP": "127.0.0.1"}),
        ("C4 Client-IP", {"Client-IP": "127.0.0.1"}),
        ("C5 UA Googlebot", {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}),
        ("C6 UA 空", {"User-Agent": ""}),
        ("C7 Host localhost", {"Host": "localhost"}),
        ("C8 X-Forwarded-Host", {"X-Forwarded-Host": "127.0.0.1"}),
    ]:
        req("C " + name, "GET", ABS3 + "/?lang=sc", headers=hd)

    print("\n" + "=" * 70, "\nD. 路径/方法变形")
    for name, m, path in [
        ("D1 //", "GET", "//?lang=sc"),
        ("D2 /./", "GET", "/./?lang=sc"),
        ("D3 大小写", "GET", "/?LANG=sc"),
        ("D4 ;", "GET", "/;?lang=sc"),
        ("D5 HEAD", "HEAD", "/?lang=sc"),
        ("D6 POST /", "POST", "/?lang=sc"),
        ("D7 OPTIONS", "OPTIONS", "/?lang=sc"),
    ]:
        req("D " + name, m, ABS3 + path)

if __name__ == "__main__":
    main()
