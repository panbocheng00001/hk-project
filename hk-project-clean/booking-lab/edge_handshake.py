# -*- coding: utf-8 -*-
"""edge_handshake.py —— 验证「412 是否只是发牌」：收下 Set-Cookie 重放能不能 200

实测背景：412 响应头里已经带了
  Set-Cookie: HWWAFSESID / HWWAFSESTIME / HIK0kat3rt9x*
（后缀有 O 和 P 两种，长度 109 / 343 / 492 都见过 —— 见 edge_cookies.py）
如果重放就 200，那"过 412"根本不需要跑 JS，纯 HTTP 两跳握手即可。
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
import json, sys
from curl_cffi import requests as creq

try:
    from edge_cookies import is_hik0
except Exception:                                    # 兜底，避免单文件搬运时报错
    def is_hik0(name: str) -> bool:
        return str(name or "").startswith("HIK0kat3rt9x")

URL = "https://abs3.example.test/?lang=sc"
C, E = "tdabsditest", "tdabsd261110kla"
ENQ = f"https://abs4.example.test/spa-api/queue/{C}/{E}/enqueue"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def mk(proxy: str):
    kw = {**({"impersonate": _tls} if _tls else {}), "timeout": 25, "verify": False,
          "headers": {"User-Agent": UA, "Accept": "*/*"}}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    return creq.Session(**kw)


def hik0_map(sess) -> dict:
    """取出会话里所有 HIK0 令牌（O/P/任意后缀），返回 {name: value}。"""
    out = {}
    try:
        for k, v in sess.cookies.items():
            if is_hik0(k):
                out[k] = v
    except Exception:
        pass
    return out


def copy_hik0(sess, hk: dict, domain: str) -> int:
    n = 0
    for k, v in hk.items():
        try:
            sess.cookies.set(k, v, domain=domain, path="/")
            n += 1
        except Exception:
            pass
    return n


def dump(tag, r):
    body = (r.content or b"")
    head = body[:200].decode("utf-8", "ignore").replace("\n", " ")
    print(f"  {tag:28} {r.status_code}  len={len(body):6}  {head[:110]}")
    return r


def main():
    proxy = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"=== 出口: {proxy or '直连'} ===")

    # 0) 裸 enqueue（无任何 cookie）
    s0 = mk(proxy)
    dump("裸 POST enqueue", s0.post(ENQ, json={}, headers={"Content-Type": "application/json"}))
    print("     set-cookie:", [k for k in s0.cookies.keys()])

    # 1) 第一次 GET：412 + 发牌
    s1 = mk(proxy)
    r1 = dump("GET #1", s1.get(URL, allow_redirects=False))
    print("     cookies:", {k: (v[:28] + '…') for k, v in s1.cookies.items()})
    hk = hik0_map(s1)
    print("     HIK0 名称/长度:", {k: len(v) for k, v in hk.items()} or "（无）")

    # 2) 同 session 重放
    r2 = dump("GET #2 同session", s1.get(URL, allow_redirects=False))

    # 3) 手工只带 HIK0（新 session）—— 有几个带几个，不再写死后缀
    s2 = mk(proxy)
    print(f"     → GET #3 灌入 HIK0 {copy_hik0(s2, hk, 'abs3.example.test')} 条")
    dump("GET #3 只带HIK0", s2.get(URL, allow_redirects=False))

    # 4) 手工带 HIK0+HWWAFSESID（新 session）
    s3 = mk(proxy)
    print(f"     → GET #4 灌入 HIK0 {copy_hik0(s3, hk, 'abs3.example.test')} 条", end="")
    for k in ("HWWAFSESID", "HWWAFSESTIME"):
        v = s1.cookies.get(k)
        if v:
            s3.cookies.set(k, v, domain="abs3.example.test", path="/")
    print(" + SID/STIME")
    dump("GET #4 带HIK0+SID", s3.get(URL, allow_redirects=False))

    # 5) 带 cookie 打 enqueue
    s4 = mk(proxy)
    print(f"     → enqueue 灌入 HIK0 {copy_hik0(s4, hk, 'abs4.example.test')} 条", end="")
    for k in ("HWWAFSESID", "HWWAFSESTIME"):
        v = s1.cookies.get(k)
        if v:
            s4.cookies.set(k, v, domain="abs4.example.test", path="/")
    print(" + SID/STIME")
    dump("带HIK0 POST enqueue", s4.post(ENQ, json={}, headers={"Content-Type": "application/json"}))

    print("\n=== 判定 ===")
    if not hk:
        print("  本次 412 没发 HIK0 牌（或站点当前不挑战）→ 这条握手实验不适用")
    if r2.status_code in (200, 302):
        print("  ★ 412 只是发牌：收 cookie 重放即通 → 纯 HTTP 两跳握手就够，不需要 JS")
    elif r2.status_code == 400:
        print("  412→400：WAF 认 token 但校验不过（HIK0 是 seed，需 JS 算正式值 / 或缺动态参数）")
    else:
        print(f"  重放 {r2.status_code}：需要进一步看响应")


if __name__ == "__main__":
    main()
