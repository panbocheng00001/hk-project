# -*- coding: utf-8 -*-
"""align_client.py —— R1：票 + 指纹对齐（真零浏览器的落地件）

===========================================================================
它解决的那一个问题
---------------------------------------------------------------------------
浏览器铸出来的票（HIK0），为什么换成 curl_cffi 就被 400？
可能绑三样东西：出口 IP、UA（含 sec-ch-ua 全家）、TLS/HTTP2 指纹。

2026-11-11 那次实验把「UA」和「TLS」**同时**换掉，全 400，
于是被记成「与浏览器指纹绑定」。这是双因子混淆，不能作数。

本模块的做法：
  1. 铸票时（mint_align.py）把**浏览器当时真实发出的请求头**逐字记进票里，
     而不是事后手写一套「看起来像」的头；
  2. 复用时把这套头**原样重放**，一个字节都不改；
  3. TLS 画像不猜 —— 每张新票用 ≤3 次请求做一次「画像发现」，
     命中即写回票里，此后这张票只用命中的那个画像。

于是「三元组对齐」变成可验证的工程动作，而不是信念。
===========================================================================

用法
---------------------------------------------------------------------------
  # 1) 探测这张票在哪个 TLS 画像下能过（≤3 次请求，结果写回票）
  python align_client.py --ticket runs/tickets/w1.json --probe

  # 2) 单次复验（1 次请求）
  python align_client.py --ticket runs/tickets/w1.json --verify

  # 3) 打印这张票会被重放成什么样（不发请求，用来核对）
  python align_client.py --ticket runs/tickets/w1.json --dump

  # 4) 接到全流程（sniper 内部用同一个 session）
  python sniper.py --align runs/tickets/w1.json --all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
# 注意：**不要**把 vendor/ 加进 sys.path。
# vendor 里是 curl_cffi 0.17，它在类体用了 `int | None`，Python 3.9 直接 TypeError；
# 本机已装 0.13（支持 impersonate），贸然插路径会把能用的换成不能用的。

TICKET_DIR = BASE / "runs" / "tickets"
CHECK_URL = "https://abs3.td.gov.hk/?lang=sc"

# curl 自己管、或不该由我们发的头
DROP_HEADERS = {
    "host", "content-length", "connection", "accept-encoding", "transfer-encoding",
    "cookie", "content-type", "te", "trailer", "proxy-connection", "keep-alive",
    "if-none-match", "if-modified-since", "range",
}
# 文档导航专有、XHR 不该带的头
NAV_ONLY = {"upgrade-insecure-requests", "sec-fetch-user", "priority"}


# ============================================================================
# 票
# ============================================================================
def ticket_path(ip_id: str = "", path: str = "") -> Path:
    if path:
        p = Path(path)
        return p if p.is_absolute() else (BASE / p)
    TICKET_DIR.mkdir(parents=True, exist_ok=True)
    return TICKET_DIR / f"{ip_id or 'direct'}.json"


def load_ticket(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = BASE / p
    if not p.exists():
        raise SystemExit(f"票不存在：{p}\n先铸票：python mint_align.py --ip-id w1")
    d = json.loads(p.read_text(encoding="utf-8"))
    if not d.get("cookies"):
        raise SystemExit(f"票 {p.name} 里没有 cookies")
    if not d.get("headers"):
        raise SystemExit(f"票 {p.name} 里没有 headers —— 请用 mint_align.py 重铸"
                         "（手写的头就是上次 400 的根源）")
    d["_path"] = str(p)
    return d


def save_ticket(t: Dict[str, Any]) -> None:
    p = Path(t.get("_path") or ticket_path(t.get("ip_id", "")))
    d = {k: v for k, v in t.items() if not k.startswith("_")}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    t["_path"] = str(p)


def age_seconds(t: Dict[str, Any]) -> float:
    try:
        return time.time() - time.mktime(time.strptime(t["created_at"],
                                                       "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 1e9


# HIK0 票的实测寿命约 30 分钟（换 IP 立即作废）。留 5 分钟余量做提醒。
TTL_WARN_S = 25 * 60
TTL_DEAD_S = 40 * 60


def ttl_note(t: Dict[str, Any], log=print) -> None:
    age = age_seconds(t)
    if age >= TTL_DEAD_S:
        log(f"   票龄 {age/60:.0f} 分钟 —— 大概率已过期（TTL≈30min），先 --verify 再谈复用")
    elif age >= TTL_WARN_S:
        log(f"   票龄 {age/60:.0f} 分钟 —— 已接近 TTL 上限，先 --verify")


# ============================================================================
# 头部重放
# ============================================================================
def clean_headers(raw: Dict[str, str], synth_secfetch: bool = False) -> Dict[str, str]:
    """把浏览器采到的头，裁成 curl 可以合法重放的一组（保持大小写与顺序）。

    synth_secfetch：Playwright 的 `request.headers` 不暴露 sec-fetch-* 三兄弟，
    所以「原样重放」时它们自然缺席。补它们属于**推断**而不是观测，
    默认关闭；只有当 400 排查到「缺 sec-fetch 被判非法」时才打开做 A/B。
    """
    out: Dict[str, str] = {}
    for k, v in (raw or {}).items():
        if not k or k.startswith(":") or k.lower() in DROP_HEADERS:
            continue
        out[k] = v
    if synth_secfetch and not any(k.lower().startswith("sec-fetch-") for k in out):
        out["Sec-Fetch-Dest"] = "document"
        out["Sec-Fetch-Mode"] = "navigate"
        out["Sec-Fetch-Site"] = "none"
        out["Sec-Fetch-User"] = "?1"
    return out


def xhr_headers(nav: Dict[str, str]) -> Dict[str, str]:
    """由「文档导航头」确定性地推出「同源 XHR 头」。

    不额外多打一次请求去采 XHR 头（那会多用掉一次出口配额），
    而是照 Chrome 自己的规则改：dest=empty / mode=cors / site=same-origin，
    去掉 Upgrade-Insecure-Requests 与 Priority。字段名与值大小写一律沿用原样。
    """
    out: Dict[str, str] = {}
    for k, v in nav.items():
        lk = k.lower()
        if lk in NAV_ONLY:
            continue
        if lk == "sec-fetch-dest":
            out[k] = "empty"
        elif lk == "sec-fetch-mode":
            out[k] = "cors"
        elif lk == "sec-fetch-site":
            out[k] = "same-origin"
        elif lk == "accept":
            out[k] = "application/json, text/plain, */*"
        else:
            out[k] = v
    return out


def find_headers_case(d: Dict[str, str], name: str) -> Tuple[str, str]:
    for k, v in d.items():
        if k.lower() == name.lower():
            return k, v
    return "", ""


# ============================================================================
# TLS 画像
# ============================================================================
def available_profiles() -> List[int]:
    try:
        import typing
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        return sorted(int(str(x)[6:]) for x in typing.get_args(BrowserTypeLiteral)
                      if re.fullmatch(r"chrome\d+", str(x)))
    except Exception:
        return [124, 131, 133, 136]


def profile_candidates(mint_version: str, tried: Optional[List[str]] = None) -> List[str]:
    """按「离铸票内核最近」排序给出候选 TLS 画像。

    铸票内核是 156，而 curl_cffi 最高只到 136 —— 所以「对齐」只能是
    「在同一大版本族内最接近的画像」，这份候选表就是这个事实的编码。
    """
    have = available_profiles()
    try:
        v = int(mint_version)
    except Exception:
        v = max(have) if have else 136
    near = sorted([x for x in have if x <= v], reverse=True) or sorted(have, reverse=True)
    cand = [f"chrome{x}" for x in near[:2]]
    cand.append("chrome")            # DEFAULT_CHROME
    out: List[str] = []
    for c in cand:
        if c not in out and (not tried or c not in tried):
            out.append(c)
    return out


# ============================================================================
# 对齐会话
# ============================================================================
class AlignedSession:
    """一张票 → 一个 curl_cffi.Session，请求头逐字复现。

    只做三件事：装头、装 cookie、选画像。不掺任何「优化」——
    任何自作聪明的改写都会让三元组失去对齐的意义。
    """

    def __init__(self, ticket: Dict[str, Any], impersonate: str = "",
                 proxy: str = "", timeout: int = 25, verify: bool = False,
                 synth_secfetch: bool = False):
        from curl_cffi import requests as cr

        self.ticket = ticket
        self.impersonate = impersonate or ticket.get("impersonate") or \
            profile_candidates(mint_version(ticket))[0]
        self.proxy = proxy or ticket.get("proxy") or ""
        self.timeout = timeout
        self.synth = synth_secfetch or bool(ticket.get("synth_secfetch"))
        self.nav = clean_headers(ticket.get("headers") or {}, self.synth)
        self.xhr = xhr_headers(self.nav)

        kw: Dict[str, Any] = {"impersonate": self.impersonate,
                              "verify": verify, "timeout": timeout}
        if self.proxy:
            kw["proxies"] = {"http": self.proxy, "https": self.proxy}
        else:
            kw["trust_env"] = False
        self.s = cr.Session(**kw)
        self.s.headers.clear()
        self.s.headers.update(self.nav)
        self._install_cookies(ticket.get("cookies") or [])

    # -- cookie ----------------------------------------------------------
    def _install_cookies(self, cookies: List[dict]) -> None:
        for c in cookies:
            if not c.get("name"):
                continue
            dom = c.get("domain") or ""
            try:
                self.s.cookies.set(c["name"], c.get("value", ""),
                                   domain=dom or None, path=c.get("path") or "/")
            except Exception:
                try:
                    self.s.cookies.set(c["name"], c.get("value", ""))
                except Exception:
                    pass

    def cookies(self) -> Dict[str, str]:
        try:
            return {c.name: c.value for c in self.s.cookies.jar}
        except Exception:
            return {}

    # -- 请求 ------------------------------------------------------------
    def request(self, method: str, url: str, *, kind: Optional[str] = None,
                referer_ok: bool = True, **kw):
        """kind=None 时按 URL 自动判：`/spa-api/` 走 XHR 头，其余走导航头。

        安全阀：Referer 只有与目标同源才发。
        实测（412 结论第五节）：给 abs3 发 `Referer: https://abs1.td.gov.hk/` → 400 被判非法。
        """
        if kind is None:
            kind = "xhr" if ("/spa-api/" in url or "/api/" in url) else "document"
        hdrs = dict(self.nav if kind == "document" else self.xhr)
        if not referer_ok:
            hdrs.pop("Referer", None)
            hdrs.pop("referer", None)
        extra = kw.pop("headers", None) or {}
        for k, v in extra.items():
            if k.lower() == "referer":
                if _same_origin(v, url):
                    hdrs["Referer"] = v
                continue
            hdrs[k] = v
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("verify", False)
        kw["headers"] = hdrs
        kw["allow_redirects"] = kw.get("allow_redirects", False)
        return self.s.request(method, url, **kw)

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:
            pass

    # -- 自检 ------------------------------------------------------------
    def verify(self) -> Dict[str, Any]:
        """1 次请求：这张票 + 这套画像，能不能进 abs3。"""
        t0 = time.time()
        try:
            r = self.get(CHECK_URL, allow_redirects=False)
        except Exception as e:
            return {"ok": False, "code": 0, "err": f"{type(e).__name__}: {str(e)[:100]}",
                    "ms": round((time.time() - t0) * 1000)}
        body = r.content or b""
        text = body[:4000].decode("utf-8", "replace")
        res = {
            "ok": r.status_code == 200,
            "code": r.status_code,
            "len": len(body),
            "ms": round((time.time() - t0) * 1000),
            "server": r.headers.get("Server", ""),
            "is_waf_page": ("$_ts" in text or "nsd=" in text),
            "location": (r.headers.get("Location") or "")[:160],
        }
        if r.status_code == 200:
            res["verdict"] = "对齐成功：票 + UA + 这套 TLS 画像被接受"
        elif r.status_code == 412:
            res["verdict"] = ("挑战模式：这张票不是「过挑战之后」的会话，"
                              "或 HIK0 已失效（TTL≈30min）")
        elif r.status_code == 400:
            res["verdict"] = "被判非法：画像/UA/出口 与铸票时不符，或出口已进黑名单"
        elif r.status_code in (301, 302, 303):
            res["verdict"] = "被弹走：多半是 Queue-it 未放行（不是 WAF 层的问题）"
        else:
            res["verdict"] = f"HTTP {r.status_code}"
        return res


def _same_origin(a: str, b: str) -> bool:
    try:
        from urllib.parse import urlparse
        pa, pb = urlparse(a), urlparse(b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False


def mint_version(ticket: Dict[str, Any]) -> str:
    ua = ticket.get("ua") or ""
    m = re.search(r"Chrome/(\d+)", ua)
    return m.group(1) if m else ""


# ============================================================================
# 画像发现：把「猜」变成一次有预算的探测
# ============================================================================
def discover(ticket: Dict[str, Any], *, budget: int = 3, interval: float = 3.0,
             log=print, save: bool = True, synth_secfetch: bool = False) -> Dict[str, Any]:
    """逐画像验证，命中即停。最多 budget 次请求，结果写回票。"""
    tried: List[str] = list((ticket.get("probe") or {}).get("tried_names") or [])
    if ticket.get("impersonate") and ticket.get("probe", {}).get("ok"):
        log(f"票已绑定画像 {ticket['impersonate']}（探测过，跳过）")
        return {"impersonate": ticket["impersonate"], "hit": True, "cached": True,
                "tried": tried}

    cand = profile_candidates(mint_version(ticket), tried)[:budget]
    if not cand:
        return {"hit": False, "reason": "候选画像已用尽（这张票之前全试过了），换出口重铸"}

    rows: List[Dict[str, Any]] = []
    for i, imp in enumerate(cand):
        if i:
            time.sleep(interval)
        s = AlignedSession(ticket, impersonate=imp, timeout=25,
                           synth_secfetch=synth_secfetch)
        try:
            r = s.verify()
        finally:
            s.close()
        rows.append({"impersonate": imp, **r})
        tag = "✅" if r.get("ok") else "❌"
        log(f"  {tag} {imp:12} HTTP {r.get('code')}  {r.get('len', 0):6}B "
            f"{r.get('ms', 0):5}ms  {r.get('verdict', '')}")
        tried.append(imp)
        if r.get("ok"):
            ticket["impersonate"] = imp
            ticket["probe"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "ok": True, "tried_names": tried, "rows": rows}
            if save:
                save_ticket(ticket)
            return {"hit": True, "impersonate": imp, "rows": rows, "tried": tried}
        # 412 / 302 说明不是画像问题，继续换画像只会白烧配额
        if r.get("code") in (412, 301, 302, 303) or r.get("code") == 0:
            ticket["probe"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "ok": False, "tried_names": tried, "rows": rows}
            if save:
                save_ticket(ticket)
            log(f"  停手：{r.get('verdict')} —— 换画像解决不了，别再烧配额")
            return {"hit": False, "reason": r.get("verdict"), "rows": rows,
                    "tried": tried}

    ticket["probe"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "ok": False, "tried_names": tried, "rows": rows}
    if save:
        save_ticket(ticket)
    log("  三个画像全 400 → 确认为「出口 IP + UA + TLS」三元绑定，单票纯 HTTP 走不通")
    log("  下一步：python sniper.py --align ... 换成 R2（无窗口内核）取票，或换出口重铸")
    return {"hit": False, "reason": "all-400", "rows": rows, "tried": tried}


# ============================================================================
# 供 sniper 使用：一次装配
# ============================================================================
def session_for(ticket_or_path, *, log=print, probe: bool = True,
                budget: int = 3, synth_secfetch: bool = False) -> AlignedSession:
    t = (load_ticket(ticket_or_path) if isinstance(ticket_or_path, str)
         else ticket_or_path)
    if probe and not (t.get("probe") or {}).get("ok"):
        info = discover(t, budget=budget, log=log, synth_secfetch=synth_secfetch)
        if not info.get("hit"):
            raise SystemExit(f"这张票在纯 HTTP 下不可用：{info.get('reason')}")
    s = AlignedSession(t, synth_secfetch=synth_secfetch)
    ttl_note(t, log)
    _, ua_val = find_headers_case(s.nav, "user-agent")
    log(f"对齐会话就绪：画像 {s.impersonate}  UA {ua_val[:60]}"
        f"  cookie {len(t.get('cookies') or [])} 条")
    return s


# ============================================================================
# CLI
# ============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="R1 票+指纹对齐客户端")
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--probe", action="store_true", help="≤3 次请求，发现可用 TLS 画像")
    ap.add_argument("--verify", action="store_true", help="1 次请求复验")
    ap.add_argument("--dump", action="store_true", help="只打印将重放的头（不发请求）")
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--synth-secfetch", action="store_true",
                    help="补齐 sec-fetch-*（推断项，默认不发；只在排查 400 时开）")
    a = ap.parse_args()

    t = load_ticket(a.ticket)
    print(f"票      : {t['_path']}")
    print(f"铸票 UA : {t.get('ua', '')[:90]}")
    print(f"出口    : {t.get('proxy') or '直连'}   egress={t.get('egress_ip') or '?'}")
    print(f"年龄    : {age_seconds(t)/60:.1f} 分钟")
    print(f"引擎    : {(t.get('engine') or {}).get('name')} "
          f"{(t.get('engine') or {}).get('version')} headless={(t.get('engine') or {}).get('headless')}")
    print(f"cookie  : {len(t.get('cookies') or [])} 条  "
          f"HIK0={'有' if any('HIK0' in c.get('name','') for c in t['cookies']) else '无'}")
    print(f"绑定画像: {t.get('impersonate') or '未探测'}")
    ttl_note(t)

    if a.dump:
        nav = clean_headers(t.get("headers") or {})
        xhr = xhr_headers(nav)
        print(f"\n--- 导航头（{len(nav)} 项，原样重放）---")
        for k, v in nav.items():
            print(f"  {k}: {v[:100]}")
        print(f"\n--- 同源 XHR 头（{len(xhr)} 项，派生）---")
        for k, v in xhr.items():
            if nav.get(k) != v:
                print(f"  {k}: {v[:100]}   (原 {nav.get(k, '—')[:60]})")
        print(f"\n候选画像顺序: {profile_candidates(mint_version(t))}")
        return 0

    if a.probe:
        print("\n--- 画像发现 ---")
        info = discover(t, budget=a.budget, interval=a.interval)
        print(f"\n结果: {'命中 ' + info['impersonate'] if info.get('hit') else info.get('reason')}")
        return 0 if info.get("hit") else 41

    if a.verify:
        s = AlignedSession(t)
        try:
            r = s.verify()
        finally:
            s.close()
        print(f"\n画像 {s.impersonate} → HTTP {r.get('code')} {r.get('len', 0)}B "
              f"{r.get('ms', 0)}ms")
        print(f"判读: {r.get('verdict')}")
        return 0 if r.get("ok") else 41

    print("\n没指定动作：用 --dump / --verify / --probe")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
