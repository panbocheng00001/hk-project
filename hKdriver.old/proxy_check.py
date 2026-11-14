# -*- coding: utf-8 -*-
"""proxy_check.py —— 代理体检（一行一个 `host:port:user:pass`，或 URL 形式）
=================================================================================
为什么要专门做一次体检（而不是「能连上就算能用」）：
    这三道门对出口的要求不一样——
      ① WAF 网关     只看能不能到达 + 会不会直接 412（机房 IP 常被判死）
      ② 排队/表单票  和出口 IP **绑死**，IP 一变票立刻作废 → 必须验「两次请求出口是否相同」
      ③ 抢号窗口     粘性会话的长度决定一张票能用多久（10 分钟 = 只能覆盖半个表单窗口）

所以每一条代理都验四件事：
    1) 认证与连通   —— 能不能连上、用户名密码对不对
    2) 出口归属     —— 住宅/家宽 还是 机房/CDN（后者基本过不了 WAF）
    3) 出口一致性   —— 连续两次请求是否同一个 IP（不一致 = 轮换型，不能绑票）
    4) 目标可到达   —— GET abs3 的状态码；412 是挑战（正常现象，但配合机房 ASN 就是坏信号）

用法：
    python proxy_check.py --file proxies.txt
    python proxy_check.py --line "us.ipwo.net:7878:_custom_zone_HK_sid_1_time_10:"
    python proxy_check.py --file proxies.txt --target "https://abs3.td.gov.hk/?lang=sc"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = Path(__file__).resolve().parent

BAD_ORG = ("cloudflare", "amazon", "aws", "google", "microsoft", "azure", "ovh",
           "digitalocean", "linode", "vultr", "hetzner", "contabo", "alibaba",
           "tencent cloud", "oracle", "zscaler", "akamai", "choopa", "m247",
           "hostinger", "leaseweb", "colocrossing", "datacamp", "packet")

TARGET = "https://abs3.td.gov.hk/?lang=sc"
IPINFO = "https://ipinfo.io/json"


def _curl():
    try:
        import curl_cffi  # noqa: F401
    except Exception:
        v = str(BASE / "vendor")
        if Path(v).exists() and v not in sys.path:
            sys.path.insert(0, v)
    from curl_cffi import requests as cr
    return cr


def parse_line(line: str) -> dict | None:
    """支持三种写法：
        host:port:user:pass      （ipwo 那种，密码为空就留空/省略）
        user:pass@host:port
        http://user:pass@host:port
    """
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    if "://" in s:
        scheme, rest = s.split("://", 1)
    else:
        scheme, rest = "http", s
    if "@" in rest:
        auth, hostport = rest.rsplit("@", 1)
        user, _, pwd = auth.partition(":")
    else:
        parts = rest.split(":")
        if len(parts) < 2:
            return None
        hostport = ":".join(parts[:2])
        if len(parts) == 2:                 # 光给 host:port（无认证的代理）
            user, pwd = "", ""
        elif len(parts) == 3:
            user, pwd = parts[2], ""
        else:
            user, pwd = parts[2], ":".join(parts[3:])
    host, _, port = hostport.rpartition(":")
    if not host or not port:
        return None
    return {"scheme": scheme, "host": host, "port": port, "user": user, "pass": pwd,
            "raw": s, "url": build_url(scheme, host, port, user, pwd)}


def build_url(scheme: str, host: str, port: str, user: str, pwd: str) -> str:
    # 密码为空时不要留孤零零的冒号（有的代理端会把 "user:@" 当成密码是 ":"）
    if not user:
        return f"{scheme}://{host}:{port}"
    auth = user if pwd == "" else f"{user}:{pwd}"
    return f"{scheme}://{auth}@{host}:{port}"


def start_chains(items, hop_url: str, base_port: int = 18900) -> None:
    """--hop 模式：每条代理起一个本地串联口（127.0.0.1:189xx），检查时连本地口。
    这样「来源 IP 被拒」的问题被跳板绕过，检查逻辑完全不用改。"""
    import asyncio
    import threading

    import proxy_chain as pc

    hop = pc.parse_proxy(hop_url) if hop_url else None
    loop = asyncio.new_event_loop()
    servers = []
    for i, p in enumerate(items):
        up = {"scheme": "http", "host": p["host"], "port": int(p["port"]),
              "user": p["user"], "pass": p["pass"]}
        servers.append(pc.ChainServer(("127.0.0.1", base_port + i), up, hop=hop, verbose=False))
        p["url"] = f"http://127.0.0.1:{base_port + i}"
        p["via"] = f"127.0.0.1:{base_port + i}"

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(*[s.serve() for s in servers]))
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.2)


def check(p: dict, timeout: float = 20.0, target: str = TARGET) -> dict:
    cr = _curl()
    r = {"raw": p["raw"], "url": p["url"], "ok": False, "reasons": []}
    proxies = {"http": p["url"], "https": p["url"]}
    t0 = time.time()
    ip1 = org = country = city = ""
    try:
        a = cr.get(IPINFO, proxies=proxies, timeout=timeout, verify=False,
                   impersonate="chrome", allow_redirects=True)
        d = a.json()
        ip1 = d.get("ip", "")
        org = d.get("org", "") or d.get("asn", {}).get("name", "")
        country = d.get("country", "")
        city = d.get("city", "")
        r["latency_ms"] = int((time.time() - t0) * 1000)
    except Exception as exc:
        r["latency_ms"] = int((time.time() - t0) * 1000)
        r["reasons"].append(f"连不上/认证失败：{type(exc).__name__} {str(exc)[:70]}")
        return r

    r.update(ip=ip1, org=str(org), country=country, city=city)

    low = str(org).lower()
    if any(k in low for k in BAD_ORG):
        r["reasons"].append(f"出口是机房/CDN ASN：{org}")

    # 一致性：粘性会话必须两次同 IP
    try:
        b = cr.get(IPINFO, proxies=proxies, timeout=timeout, verify=False, impersonate="chrome")
        ip2 = b.json().get("ip", "")
        r["ip2"] = ip2
        if ip2 != ip1:
            r["reasons"].append(f"出口不稳定：{ip1} → {ip2}（轮换型，不能绑票）")
    except Exception as exc:
        r["reasons"].append(f"第二次取样失败：{type(exc).__name__}")

    # 目标可到达（不判挑战过没过，只判能不能到达网关）
    try:
        t0 = time.time()
        g = cr.get(target, proxies=proxies, timeout=timeout, verify=False,
                   impersonate="chrome", allow_redirects=False)
        body = g.text or ""
        r["target_status"] = g.status_code
        r["target_len"] = len(body)
        r["target_ms"] = int((time.time() - t0) * 1000)
        r["challenge"] = ("$_ts" in body[:6000]) or ("nsd=" in body[:6000])
        if g.status_code in (403, 412):
            # 重要：412 是 WAF 对"不执行 JS 的客户端"的统一挑战，直连也一样。
            # 它不能证明代理坏 —— 真正的判据是浏览器里能不能拿到 HIK0kat3rt9xO。
            r["challenge"] = True
            r["note_412"] = f"目标 {g.status_code}（挑战窗口，非代理问题）"
        elif g.status_code >= 500:
            r["reasons"].append(f"目标 {g.status_code}")
    except Exception as exc:
        r["reasons"].append(f"目标不可达：{type(exc).__name__} {str(exc)[:60]}")

    r["ok"] = not r["reasons"]
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--line", action="append", default=[])
    ap.add_argument("--target", default=TARGET)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--json", default="")
    ap.add_argument("--hop", default="",
                    help="跳板：socks5://user:pass@host:port 或 http://host:port（本机来源被拒时用）")
    a = ap.parse_args()

    lines = list(a.line)
    if a.file:
        p = Path(a.file)
        if not p.is_absolute():
            p = BASE / p
        lines += p.read_text(encoding="utf-8").splitlines()

    items = [x for x in (parse_line(ln) for ln in lines) if x]
    if not items:
        print("没有可解析的代理行")
        return 2

    if a.hop:
        print(f"经跳板 {a.hop} 串联后体检（每条代理一个本地口 189xx）")
        start_chains(items, a.hop)
    print(f"体检 {len(items)} 条代理，目标 {a.target}\n" + "=" * 96)
    with ThreadPoolExecutor(max_workers=min(10, len(items))) as ex:
        results = list(ex.map(lambda p: check(p, a.timeout, a.target), items))

    good = []
    for i, r in enumerate(results, 1):
        flag = "✅ 可用" if r["ok"] else "❌"
        print(f"\n{flag}  #{i}  {r['raw']}" + (f"   via {items[i-1].get('via')}" if items[i-1].get("via") else ""))
        print(f"   出口   : {r.get('ip', '?')}  {r.get('country', '')} {r.get('city', '')}  "
              f"({str(r.get('org', '?'))[:52]})  延迟 {r.get('latency_ms', '?')}ms")
        if r.get("note_412"):
            print(f"   挑战   : {r['note_412']}")
        if r.get("target_status") is not None:
            print(f"   abs3   : HTTP {r['target_status']}  len={r.get('target_len')}  "
                  f"{r.get('target_ms', '?')}ms  挑战页={r.get('challenge')}")
        if r.get("ip2") and r.get("ip2") != r.get("ip"):
            print(f"   复测   : 变了 → {r['ip2']}")
        for why in r["reasons"]:
            print(f"   ✗ {why}")
        if r["ok"]:
            good.append(r)

    print("\n" + "=" * 96)
    print(f"可用 {len(good)} / {len(results)}")
    if good:
        ips = {g.get("ip") for g in good}
        print(f"去重后出口 IP 数：{len(ips)}（每条 sid 应给一个不同 IP）")
        print("可直接喂给票池（粘性会话保持不变）：")
        for g in good:
            print("   ", g["url"])
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n明细已写入 {a.json}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
