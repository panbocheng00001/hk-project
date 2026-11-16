# -*- coding: utf-8 -*-
"""probe_proxy.py —— 代理可用性体检（决定「代理池抢号」方案成不成立）
=====================================================================
在三道门里，代理只能解决第一/二道，但**如果代理本身过不了 WAF，后面全白搭**。
所以用这个脚本先做一次快速体检，别等 16:30 才发现代理是废的。

每项检查对应一个已知的失败模式：

  1) 连通性        代理连不上 / TLS 握手失败
  2) 出口归属      org 是机房 ASN（AS4134 家宽 OK；Cloudflare/OVH/AWS 一律挂）
  3) WAF 挑战      GET abs3 → 412 + $_ts = 挑战页（自动化环境拿不到 token，必然挂）
                   → 200 + 749 字节左右 = 正常骨架，可用
  4) 一致性        两次请求出口 IP 是否相同（轮换型代理不能用于绑 IP 的门票）

用法：
    python probe_proxy.py --proxy http://user:pass@1.2.3.4:8080
    python probe_proxy.py --proxy socks5://1.2.3.4:1080
    python probe_proxy.py --file proxies.txt          # 一行一个代理，批量体检
    python probe_proxy.py --proxy ... --browser       # 额外用真 Chrome 走代理测一次
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENTRY = "https://abs3.td.gov.hk/?lang=sc"
IPINFO = "https://ipinfo.io/json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

# 已知会被官方判异常的 ASN / 组织关键词（数据中心、CDN、隐私中继）
BAD_ORG = ("cloudflare", "amazon", "aws", "google", "microsoft", "azure",
           "ovh", "digitalocean", "linode", "vultr", "hetzner", "contabo",
           "alibaba", "tencent cloud", "oracle", "zscaler", "akamai")


def opener_for(proxy: str):
    # SSL context 必须挂在 HTTPSHandler 上；OpenerDirector.open() 不接受 context 参数
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE      # 代理常做 MITM，证书链不完整
    if not proxy:
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ctx),
            urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def get(op, url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return op.open(req, timeout=timeout)


def check(proxy: str, use_browser: bool = False) -> dict:
    r = {"proxy": proxy or "(直连)", "ok": False, "reasons": []}
    op = opener_for(proxy)

    # 1) 出口 IP 与归属
    try:
        with get(op, IPINFO, 20) as resp:
            info = json.loads(resp.read())
        r["ip"] = info.get("ip")
        r["org"] = info.get("org", "")
        r["country"] = info.get("country", "")
        low = str(r["org"]).lower()
        if any(k in low for k in BAD_ORG):
            r["reasons"].append(f"出口是机房/CDN ASN：{r['org']}")
    except Exception as exc:
        r["reasons"].append(f"连通性失败：{type(exc).__name__} {str(exc)[:80]}")
        return r

    # 2) 一致性（排除轮换代理）
    try:
        with get(op, IPINFO, 20) as resp2:
            ip2 = json.loads(resp2.read()).get("ip")
        if ip2 != r.get("ip"):
            r["reasons"].append(f"出口 IP 不稳定：{r.get('ip')} → {ip2}（轮换代理不能绑门票）")
    except Exception:
        pass

    # 3) 入口连通性（注意：**不能用这个判断 WAF 是否放行**）
    #    abs3 的 412 是 JS 挑战，任何不执行 JS 的客户端（urllib/requests/curl）都会拿到 412，
    #    连本机家宽直连也一样。所以这一项只回答「请求能不能到达服务端」，
    #    WAF 能否通过必须在真浏览器里用 launch_ext.ps1 -Proxy 实测。
    try:
        with get(op, ENTRY, 30) as resp:
            body = resp.read()
            r["status"] = resp.status
            r["len"] = len(body)
            if resp.status == 200 and len(body) > 300:
                r["reach"] = "ok"
            elif resp.status in (403, 412):
                r["reach"] = "challenge"       # 正常现象，不代表代理坏
            else:
                r["reasons"].append(f"入口异常：HTTP {resp.status} len={len(body)}")
    except urllib.error.HTTPError as e:
        r["status"] = e.code
        r["reach"] = "challenge" if e.code in (403, 412) else "error"
        if e.code not in (403, 412):
            r["reasons"].append(f"入口 HTTP {e.code}")
    except Exception as exc:
        r["reasons"].append(f"入口请求失败：{type(exc).__name__} {str(exc)[:80]}")

    # 结论：只判「能不能用来排队」，不判 WAF（那一项交给浏览器实测）
    r["ok"] = not r["reasons"]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="", help="单个代理，如 http://user:pass@1.2.3.4:8080")
    ap.add_argument("--file", default="", help="代理列表文件，一行一个")
    ap.add_argument("--browser", action="store_true", help="额外用真 Chrome 走该代理验证（更准，但慢）")
    a = ap.parse_args()

    targets = []
    if a.proxy:
        targets.append(a.proxy)
    if a.file:
        p = Path(a.file)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent / p
        if p.exists():
            targets += [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.strip().startswith("#")]
        else:
            print(f"找不到 {p}")
    if not targets:
        targets = [""]      # 无代理 → 测直连，作为基线

    print(f"体检 {len(targets)} 个出口\n" + "=" * 70)
    good = []
    for t in targets:
        r = check(t)
        flag = "✅ 可用" if r["ok"] else "❌ 不可用"
        print(f"\n{flag}  {r['proxy']}")
        print(f"   出口 IP : {r.get('ip', '?')}  ({r.get('org', '?')})")
        print(f"   入口     : HTTP {r.get('status', '?')}  len={r.get('len', '?')}")
        for why in r["reasons"]:
            print(f"   ✗ {why}")
        if r["ok"]:
            good.append(t)

    print("\n" + "=" * 70)
    print(f"可用 {len(good)} / {len(targets)}")
    if good:
        print("可用于抢号的代理：")
        for g in good:
            print("   ", g)
    else:
        print("没有可用代理 —— 注意：机房 IP 一律过不了 WAF，必须住宅/家宽出口。")


if __name__ == "__main__":
    main()
