# -*- coding: utf-8 -*-
"""preflight.py —— 生成会话票据前置分诊闸（`--before-mint`）

===========================================================================
它要拦住的那件事
---------------------------------------------------------------------------
2026-11-11 16:30 之后的真实剧本：
   出口已进限流名单 → 生成会话票据器照样启动浏览器 → goto 抛 SSL 错 →
   仍然把半死的 cookie 写成一张票 → 后面 probe 再花 3 次请求 → 全 400。

代价不是"多花 3 秒"，是**把一个已经被打的出口又摸了 4 次**，
而 WAF 的限流名单是"越摸越深"（你的实测：2~3 次就 400，再重就 80/443 丢包）。

所以顺序必须是：**先分诊 1 次 → 判活才生成会话票据**。
===========================================================================

四态判定（与 edge_probe.py 同一套口径，但把传输层错误拆细）
---------------------------------------------------------------------------
  OK          200/302                     站点不挑战，可以直接裸 HTTP
  CHALLENGE   412 且 body 含 $_ts/nsd      挑战模式，**正是需要生成会话票据的时候**
  DROP        超时 / RST / 连不上          传输层断了，换出口，别谈生成会话票据
  BANNED      400（空 body）               出口已判死，生成会话票据只会更深
  TLS_WRONG   443 上回的不是 TLS           代理不支持 CONNECT / scheme 写错
  TLS_CERT    证书链问题                   通道其实是通的

闸门规则
---------------------------------------------------------------------------
  OK / CHALLENGE  → 放行去生成会话票据（这两种情况下生成会话票据有意义）
  其它一律        → 拦截，退出码 44，不启浏览器、不发第 2 个请求

用法
---------------------------------------------------------------------------
  python preflight.py --proxy "http://ip:port"          # 单出口分诊
  python preflight.py --proxies proxies.txt             # 批量分诊（每个只打 1 次）
  python preflight.py --proxy "..." --json
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

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from tls_profile import tls_kwargs

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

TARGET = "https://abs3.example.test/?lang=sc"
CHALLENGE_MARKS = ("$_ts", "nsd=", "HIK0kat3rt9x", "HWWAFSESID")

# 分诊必须与生成会话票据用同一套身份：生成会话票据是 CfT 156（UA 156 + sec-ch-ua 156）。
# 2026-11-12 16:36 实测：分诊用 curl_cffi 默认（136）、生成会话票据用 156，
# 同一出口换环境参数后第 2 次访问直接 400 —— 环境参数切换本身就是异常信号。
UA_ALIGN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
HDR_ALIGN = {
    "User-Agent": UA_ALIGN,
    "sec-ch-ua": '"Not:A-Brand";v="8", "Chromium";v="156"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
}

# 退出码约定（http_flow / mint_align 共用）
EXIT_OK = 0
EXIT_BLOCKED = 44


def _curl_err(e: Exception) -> Tuple[str, str]:
    """curl 错误号 → (verdict, 人话)"""
    s = str(e)
    low = s.lower()
    if "curl: (35)" in s:
        if "wrong_version_number" in low or "wrong version number" in low:
            return "TLS_WRONG", ("443 上回的不是 TLS：代理不支持 CONNECT，"
                                 "或地址被写成了 https")
        if "reset" in low:
            return "DROP", "TLS 握手被 RST：本机网络出口 大概率已进限流名单"
        return "DROP", "TLS 握手失败（35）"
    if "curl: (28)" in s or "timed out" in low:
        return "DROP", "超时（28）：TCP 层被丢包，ping 可能还通"
    if "curl: (56)" in s:
        return "DROP", "连接被重置（56）"
    if "curl: (7)" in s or "refused" in low:
        return "DROP", "连不上（7）：端口关或代理死"
    if "curl: (52)" in s:
        return "DROP", "空回复（52）：被网关直接掐"
    if "certificate" in low:
        return "TLS_CERT", "证书链问题：通道其实是通的"
    return "OTHER", f"{type(e).__name__}: {s[:100]}"


def triage(url: str = TARGET, proxy: str = "", timeout: float = 12.0,
           tls_pref: str = "") -> Dict[str, Any]:
    """一次请求定生死。绝不重试 —— 分诊本身也在花出口配额。

    头部默认按生成会话票据内核（CfT 156）对齐，避免「分诊一套环境参数、生成会话票据另一套」被判异常。
    """
    try:
        from curl_cffi import requests as cr
    except ImportError:
        return {"verdict": "OTHER", "hint": "缺 curl_cffi", "err": "ImportError"}

    # 注意：curl_cffi 0.13 的模块级 get() 不接受 trust_env / proxies 之外的
    # Session 参数，统一走 Session 构造，避免 TypeError。
    kw: Dict[str, Any] = {**tls_kwargs(tls_pref), "timeout": timeout,
                          "verify": False, "allow_redirects": False,
                          "headers": dict(HDR_ALIGN)}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    else:
        kw["trust_env"] = False
    sess = None
    t0 = time.time()
    try:
        sess = cr.Session(**kw)
        r = sess.get(url)
    except Exception as e:
        v, hint = _curl_err(e)
        return {"url": url, "proxy": proxy, "verdict": v, "hint": hint,
                "err": str(e)[:160], "ms": round((time.time() - t0) * 1000)}
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
    body = r.content or b""
    text = body[:8192].decode("utf-8", "ignore")
    ms = round((time.time() - t0) * 1000)
    base = {"url": url, "proxy": proxy, "status": r.status_code, "len": len(body),
            "ms": ms, "server": r.headers.get("Server", ""),
            "set_cookie": [x.split(";")[0].split("=")[0]
                           for x in (getattr(r.headers, "get_list", lambda *_: [])("set-cookie") or [])]}

    if r.status_code == 412 or any(m in text for m in CHALLENGE_MARKS):
        base.update(verdict="CHALLENGE",
                    hint="挑战模式 —— 正是要生成会话票据的时候（patchright 有窗口能过）")
    elif r.status_code in (200, 201, 204, 301, 302, 303, 307, 308):
        base.update(verdict="OK", hint="站点不挑战，裸 HTTP 就能打，票可选")
    elif r.status_code == 400 and len(body) < 64:
        base.update(verdict="BANNED",
                    hint="400 + 空 body：出口已判死，生成会话票据只会更深，换出口")
    elif r.status_code == 400:
        base.update(verdict="BANNED", hint="400：被判非法（出口/环境参数异常）")
    elif r.status_code in (502, 503, 504):
        base.update(verdict="GATEWAY", hint="网关错：多半是代理侧，换代理再分诊")
    else:
        base.update(verdict=f"HTTP{r.status_code}", hint=f"未归类：HTTP {r.status_code}")
    return base


def gate(proxy: str = "", log=print, url: str = TARGET,
         timeout: float = 12.0) -> Tuple[bool, Dict[str, Any]]:
    """生成会话票据前的闸门。返回 (是否放行, 分诊结果)。"""
    log(f"前置分诊：GET {url}  （{proxy or '直连'}）")
    r = triage(url, proxy, timeout)
    v = r.get("verdict", "OTHER")
    icon = {"OK": "✅", "CHALLENGE": "⚠️"}.get(v, "❌")
    log(f"  {icon} {v:10} HTTP={r.get('status', '—')} "
        f"len={r.get('len', 0)} {r.get('ms', 0)}ms  {r.get('hint', '')}")
    allowed = v in ("OK", "CHALLENGE")
    if not allowed:
        log("  → 拦住，不启动浏览器、不发后续请求（省配额、免得把出口打更深）")
    return allowed, r


def main() -> int:
    ap = argparse.ArgumentParser(description="生成会话票据前置分诊")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--proxies", default="", help="批量：每行 http://ip:port")
    ap.add_argument("--url", default=TARGET)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    targets: List[str] = []
    if a.proxies:
        p = Path(a.proxies)
        if not p.is_absolute():
            p = BASE / p
        targets = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                   if ln.strip()] if p.exists() else []
    else:
        targets = [a.proxy]

    rows = []
    for px in targets:
        r = triage(a.url, px, a.timeout)
        rows.append(r)
        if not a.json:
            icon = {"OK": "✅", "CHALLENGE": "⚠️"}.get(r.get("verdict"), "❌")
            print(f"  {icon} {(px or '直连'):34} {r.get('verdict'):10} "
                  f"HTTP={r.get('status', '—')} {r.get('ms', 0):5}ms  {r.get('hint', '')}")
        time.sleep(1.0)          # 批量分诊也要留间隔

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    good = [r for r in rows if r.get("verdict") in ("OK", "CHALLENGE")]
    print(f"\n可用 {len(good)}/{len(rows)}："
          f"{', '.join((r['proxy'] or '直连') for r in good) or '无'}")
    return EXIT_OK if good else EXIT_BLOCKED


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
