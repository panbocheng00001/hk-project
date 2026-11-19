# -*- coding: utf-8 -*-
"""edge_probe.py —— 412 分诊：一眼看清是「被封(DROP)」「挑战(412)」还是「通了(200)」

为什么要它：
  412 挑战页和 TCP 层 DROP 长得完全不一样，但脚本里都表现为"失败"。
  一路重试 412 会把本机网络出口 打进 WAF 限流名单（实测：80/443 直接丢包，ping 还通）。
  先用它分诊，再决定「换 IP」还是「解挑战」还是「直接打 API」。

跑法：
  python edge_probe.py --urls urls.txt
  python edge_probe.py --url "https://abs3.example.test/?lang=sc" --proxy http://1.2.3.4:8080
  python edge_probe.py --proxies proxies.txt --url "https://abs3.example.test/?lang=sc"

判定：
  DROP       连接超时/被 reset（TCP 层就没了）→ 换出口，别重试
  CHALLENGE  412 + $_ts/nsd 挑战页          → 需要过 JS 挑战（edge_token.py）
  OK         200/302                        → 直接可用
  OTHER      其它状态码/网关错
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
import argparse, json, sys, time
from pathlib import Path
from tls_profile import tls_kwargs

try:
    from curl_cffi import requests as creq
except ImportError:
    creq = None

CHALLENGE_MARKS = ("$_ts", "nsd=", "HIK0kat3rt9xO", "HWWAFSESID")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def classify(r=None, err: str = "") -> str:
    if err:
        e = err.lower()
        # 证书链问题 = TLS 握手到了但有中间人/链不全 → 通道其实是通的
        if "certificate" in e or "cert" in e:
            return "TLS_CERT"
        if any(k in e for k in ("timeout", "timed out", "reset", "refused", "connect", "ssl")):
            return "DROP"
        return "OTHER"
    if r is None:
        return "OTHER"
    body = (r.content or b"")[:8192]
    text = body.decode("utf-8", "ignore")
    if r.status_code == 412 or any(m in text for m in CHALLENGE_MARKS):
        return "CHALLENGE"
    if r.status_code in (200, 201, 202, 204, 301, 302, 303, 307, 308):
        return "OK"
    return f"HTTP{r.status_code}"


def probe(url: str, proxy: str = "", timeout: float = 15.0, ua: str = UA) -> dict:
    if creq is None:
        return {"url": url, "proxy": proxy, "verdict": "OTHER", "err": "缺 curl_cffi"}
    kw = {**tls_kwargs(_tls), "timeout": timeout, "allow_redirects": False,
          "verify": False,
          "headers": {"User-Agent": ua, "Accept": "*/*"}}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    t0 = time.time()
    try:
        r = creq.get(url, **kw)
        dt = time.time() - t0
        v = classify(r)
        return {"url": url, "proxy": proxy, "verdict": v, "status": r.status_code,
                "len": len(r.content or b""), "sec": round(dt, 2),
                "server": r.headers.get("server", ""),
                "edge_challenge": next((m for m in CHALLENGE_MARKS if m in (r.content or b"").decode("utf-8", "ignore")), ""),
                "loc": r.headers.get("location", "")}
    except Exception as e:
        return {"url": url, "proxy": proxy, "verdict": classify(err=str(e)),
                "err": f"{type(e).__name__}: {str(e)[:100]}", "sec": round(time.time() - t0, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--urls", default="")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--proxies", default="")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    urls = list(a.url)
    if a.urls and Path(a.urls).exists():
        urls += [l.strip() for l in Path(a.urls).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not urls:
        urls = ["https://abs3.example.test/?lang=sc"]

    proxies = [a.proxy] if a.proxy else [""]
    if a.proxies and Path(a.proxies).exists():
        proxies = [l.strip() for l in Path(a.proxies).read_text(encoding="utf-8").splitlines() if l.strip()]

    rows = []
    for p in proxies:
        tag = p or "直连"
        for u in urls:
            r = probe(u, p, a.timeout)
            rows.append(r)
            v = r["verdict"]
            extra = ""
            if r.get("status"):
                extra = f"status={r['status']} len={r['len']} {r['sec']}s"
            if r.get("err"):
                extra = r["err"]
            print(f"[{v:10}] {tag:28} {u}  {extra}")
            if r.get("loc"):
                print(f"{'':12}→ Location: {r['loc'][:120]}")

    agg = {}
    for r in rows:
        agg[r["verdict"]] = agg.get(r["verdict"], 0) + 1
    print("\n=== 汇总 ===")
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {k:10} {v}")

    print("\n=== 下一步 ===")
    if agg.get("CHALLENGE"):
        print("  有 CHALLENGE：用 python edge_token.py --proxy <该出口> 过一次挑战拿 HIK0 票；")
        print("                或直接试 POST {ABS4}/spa-api/queue/{c}/{e}/enqueue（历史上不校验动态参数）")
    if agg.get("DROP"):
        print("  DROP：该出口已进 WAF 限流名单（TCP 丢包）。停手 10~30 分钟或换出口；不要继续重试。")
    if agg.get("OK"):
        print("  OK：该出口可用，直接跑 http_flow.py / pool_runner.py。")

    if a.json_out:
        Path(a.json_out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细 → {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
