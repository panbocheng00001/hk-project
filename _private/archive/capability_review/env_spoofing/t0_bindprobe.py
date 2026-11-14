# -*- coding: utf-8 -*-
"""t0_bindprobe.py —— T0 决定性实验：WAF 票（HIK0）到底绑什么？

===========================================================================
一句话
---------------------------------------------------------------------------
这是「全链纯脚本、零浏览器」唯一还没被干净验证过的开关。
跑通它，纯 HTTP 路线成立；跑砸它，就只能上无窗口内核或 Node 解挑战。
===========================================================================

为什么必须重做（现有结论被污染）
---------------------------------------------------------------------------
`waf_replay.py` 的结论是「票与 TLS/浏览器指纹绑定」——但那次实验里：

    · 铸票的浏览器是 Chromium 156（UA-CH 报 156.0.8078.80）
    · 回放时手写的 UA 是 Chrome/131
    · 于是「换 TLS profile」与「换 UA」两个变量**同时变了**

全 400 完全可能只是 UA 不匹配，和 JA3 无关。
一次只动一个变量，才能知道该对齐哪个。

实验矩阵（顺序执行，前一步的结果决定后一步的意义）
---------------------------------------------------------------------------
  0 分诊    裸 GET（无 cookie）           → 这个出口现在是什么状态
  1 基线    票 + 铸票 UA + 匹配的 TLS     → 期望 200；若不是 200，后面全无意义
  2 换 TLS  票 + 铸票 UA + 别的 TLS       → ★核心：200 就说明 TLS 不绑
  3 换 UA   票 + 换掉 UA + 对应 TLS        → 对照组：200 说明 UA 也不绑
  4 换 UA   票 + 换掉 UA + 匹配该 UA 的 TLS → 区分「UA 绑」还是「UA+TLS 成对绑」
  5 减头    票 + 铸票 UA + 只留最小头集     → 看 sec-ch-ua / sec-fetch-* 是否参与
  6 降协议  票 + 铸票 UA + HTTP/1.1        → 看 h2 指纹是否参与

判读
---------------------------------------------------------------------------
  2=200                 → 纯 HTTP 可行（只对齐 UA + cookie），走 R1
  2=400 且 4=200        → 绑「UA」，把浏览器 UA 改成 curl_cffi 支持过的版本再铸票即可
  2=400 且 4=400 且 1=200 → 绑「UA+TLS 成对」，要造 chrome 主版本的 utls 画像
  1≠200                 → 先别谈绑定，出口已废或票已过期

红线
---------------------------------------------------------------------------
  · 变体之间 ≥ 3s，由 throttle.Throttle 统一闸门
  · 出现 412 立刻退避、连续 3 次熔断（别把出口打死，throttle 已实现）
  · 同一出口本脚本总请求 ≤ 12 次，一次实验只跑一次
  · 结论写进 runs/bindprobe/<时间>.json，别靠记忆

用法
---------------------------------------------------------------------------
  # 只看计划，不发请求
  python t0_bindprobe.py --plan

  # 用已有票跑（票由 runs\\rev\\grab_page.py 或 token_pool.py --warm 产出）
  python t0_bindprobe.py --ticket runs/rev/browser_cookies.json

  # 指定出口（票必须是在同一个出口铸的，否则 IP 维度先把你判死）
  python t0_bindprobe.py --ticket runs/rev/browser_cookies.json --proxy http://user:pass@host:port

  # 票里没记 UA 时手工指定（务必和铸票浏览器一字不差，含小版本号）
  python t0_bindprobe.py --ticket x.json --ua "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/156.0.0.0 Safari/537.36"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
for _v in (BASE / "vendor",):
    if _v.exists() and str(_v) not in sys.path:
        sys.path.insert(0, str(_v))

URL = "https://abs3.td.gov.hk/?lang=sc"
OUT_DIR = BASE / "runs" / "bindprobe"

# 出口分诊：412=挑战中（健康），200=不挑战，400=出口已废，0=丢包/被断
DIAG = {
    200: "OK（站点当前不挑战，票可直接用）",
    412: "CHALLENGE（挑战模式，票必须来自已过挑战的会话）",
    400: "DEAD（出口已被判死，先别做绑定实验）",
    0: "DROP（TCP 层就断了）",
}


# ---------------------------------------------------------------- 票
def load_ticket(path: str):
    """兼容两种产物：grab_page.py 的裸 cookie 列表 / token_pool 的 slot dict。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, list):
        return {"cookies": d, "ua": "", "proxy": ""}
    if not isinstance(d, dict):
        raise SystemExit(f"看不懂的票格式：{type(d).__name__}")
    ck = d.get("cookies") or (d.get("slot") or {}).get("cookies") or []
    if isinstance(ck, str):
        ck = json.loads(ck)
    return {
        "cookies": ck,
        "ua": d.get("ua") or d.get("user_agent") or (d.get("slot") or {}).get("ua") or "",
        "proxy": d.get("proxy") or (d.get("slot") or {}).get("proxy") or "",
        "raw": d,
    }


def ua_version(ua: str) -> str:
    m = re.search(r"Chrome/(\d+)", ua or "")
    return m.group(1) if m else ""


def pick_imp(mint_version: str) -> str:
    """挑一个离铸票主版本最近的 curl_cffi 画像，作为「匹配 TLS」的基线。"""
    try:
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        import typing
        have = sorted(int(x) for x in typing.get_args(BrowserTypeLiteral)
                      if re.fullmatch(r"chrome\d+", str(x)))
    except Exception:
        have = [124, 131, 133, 136]
    try:
        v = int(mint_version)
    except Exception:
        v = 0
    if not v:
        return "chrome"
    near = [x for x in have if x <= v]
    return f"chrome{max(near)}" if near else "chrome"


def ua_platform(ua: str) -> str:
    if "Windows" in (ua or ""):
        return '"Windows"'
    if "Macintosh" in (ua or ""):
        return '"macOS"'
    return '"Linux"'


def sec_ch_ua(ua: str) -> str:
    v = ua_version(ua) or "131"
    return f'"Chromium";v="{v}", "Not_A Brand";v="24"'


def header_set(ua: str, minimal: bool = False) -> dict:
    if minimal:
        return {"User-Agent": ua}
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": sec_ch_ua(ua),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": ua_platform(ua),
    }


# ---------------------------------------------------------------- 请求
class Probe:
    def __init__(self, proxy: str = "", timeout: int = 25, min_interval: float = 3.0):
        from curl_cffi import requests as cr  # noqa: N813
        from throttle import Throttle, ThrottleTripped  # noqa: F401

        self.cr = cr
        self.timeout = timeout
        self.th = Throttle(min_interval=min_interval, trip_after=3)
        self.proxy = proxy
        self.calls = 0

    def _session(self, impersonate: str, headers: dict, http2: bool = True):
        kw: dict[str, Any] = {"impersonate": impersonate, "verify": False,
                              "timeout": self.timeout, "allow_redirects": False,
                              "headers": headers}
        if not http2:
            # curl_cffi 用 curl 选项关 h2；不可用时忽略（不阻断实验）
            kw["http_version"] = "v1.1"
        if self.proxy:
            kw["proxies"] = {"http": self.proxy, "https": self.proxy}
        else:
            kw["trust_env"] = False
        return self.cr.Session(**kw)

    def once(self, tag: str, cookies: list, impersonate: str, headers: dict,
             http2: bool = True) -> dict:
        self.th.wait()
        self.calls += 1
        t0 = time.time()
        s = self._session(impersonate, headers, http2)
        for c in cookies or []:
            if not c.get("name"):
                continue
            try:
                s.cookies.set(c["name"], c.get("value", ""),
                              domain=c.get("domain") or "abs3.td.gov.hk",
                              path=c.get("path") or "/")
            except Exception:
                pass
        rec = {"tag": tag, "impersonate": impersonate, "http2": http2,
               "ua": headers.get("User-Agent", ""), "n_cookie": len(cookies or [])}
        try:
            r = s.get(URL)
            body = r.content or b""
            rec.update({"code": r.status_code, "len": len(body),
                        "ms": round((time.time() - t0) * 1000),
                        "head": body[:120].decode("utf-8", "replace"),
                        "server": r.headers.get("Server", ""),
                        "set_cookie": [x.split(";")[0][:40] for x in
                                       (r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else [])]})
            if r.status_code == 412:
                try:
                    self.th.on_412()
                except Exception as e:  # 熔断
                    rec["tripped"] = str(e)
            else:
                self.th.on_ok()
        except Exception as e:
            rec.update({"code": 0, "err": f"{type(e).__name__}: {str(e)[:120]}",
                        "ms": round((time.time() - t0) * 1000)})
        print(f"  {tag:44} {rec.get('code'):>4} len={rec.get('len', 0):6} "
              f"{rec.get('ms', 0):5}ms  {rec.get('err', '')}", flush=True)
        try:
            s.close()
        except Exception:
            pass
        return rec


# ---------------------------------------------------------------- 计划
def build_plan(ua: str, base_imp: str, alt_imp: str, alt_ua: str, alt_ua_imp: str) -> List[dict]:
    """返回变体清单。字段：tag/cookies/impersonate/headers/http2

    cookies 字段只做「带不带」的开关，真实 cookie 由 main 注入。
    """
    tick = [{"name": "<ticket>"}]
    return [
        dict(tag="0 分诊：裸 GET（无票）", cookies=[], impersonate="chrome",
             headers=header_set(ua, minimal=True), http2=True),
        dict(tag=f"1 基线：票 + 铸票 UA({ua_version(ua) or '?'}) + 最近 TLS({base_imp})",
             cookies=tick, impersonate=base_imp, headers=header_set(ua), http2=True),
        dict(tag=f"2 ★换 TLS：票 + 铸票 UA + imp={alt_imp}",
             cookies=tick, impersonate=alt_imp, headers=header_set(ua), http2=True),
        dict(tag=f"3 对照组：票 + 换 UA({ua_version(alt_ua)}) + imp={alt_ua_imp}",
             cookies=tick, impersonate=alt_ua_imp, headers=header_set(alt_ua), http2=True),
        dict(tag=f"4 只换 UA({ua_version(alt_ua)}) 不换 TLS({base_imp})",
             cookies=tick, impersonate=base_imp, headers=header_set(alt_ua), http2=True),
        dict(tag=f"5 减头：票 + 铸票 UA + 最小头集 + {base_imp}", cookies=tick,
             impersonate=base_imp, headers=header_set(ua, minimal=True), http2=True),
        dict(tag=f"6 降协议：票 + 铸票 UA + {base_imp} + HTTP/1.1", cookies=tick,
             impersonate=base_imp, headers=header_set(ua), http2=False),
    ]


def verdict(rows: List[dict]) -> str:
    by = {r["tag"][:1]: r for r in rows}
    diag = (by.get("0") or {}).get("code")
    base = (by.get("1") or {}).get("code")
    tls = (by.get("2") or {}).get("code")
    both = (by.get("3") or {}).get("code")
    ua_only = (by.get("4") or {}).get("code")

    if diag in (400, 0):
        return (f"结论：出口已废（分诊={diag} / {DIAG.get(diag)}）。\n"
                "       换干净出口重铸票，再做绑定实验。现在任何 400 都不具备判读价值。")
    if base != 200:
        return (f"结论：基线就不是 200（基线={base}）。\n"
                "       说明「票 + 铸票 UA + 匹配 TLS」这套组合本身不成立：\n"
                "       · 412 → 票不是过挑战后铸的，或 HIK0 缺失\n"
                "       · 400 → 出口/UA 与铸票不一致，或票已过期（TTL≈30min）\n"
                "       · 302 → 被弹回排队页，Queue-it 票不在\n"
                "       先修基线，别急着解释矩阵。")
    if tls == 200:
        return ("结论：★TLS 指纹不绑票 —— 纯 HTTP 路线成立（R1）。\n"
                "       只要 cookie + UA 对齐，换任何 curl_cffi profile 都能 200。\n"
                "       下一步：把 mint→复用 的 UA 对齐写死，全链零浏览器。")
    if both == 200 and ua_only != 200:
        return ("结论：绑「UA+TLS 成对」，但成对即可 —— 需要一个和铸票 Chrome 主版本一致的画像。\n"
                "       做法：铸票时把浏览器 UA 降到 curl_cffi 支持的版本（131/136），\n"
                "       之后 UA 与 imp 一起对齐即可 200（R1 变体）。")
    if ua_only == 200:
        return ("结论：只绑 UA，不绑 TLS —— 纯 HTTP 路线成立（R1）。\n"
                "       把铸票 UA 原样带上，TLS 用什么都行。")
    return ("结论：确认为「出口 IP + UA + TLS」三元绑定 —— 纯 HTTP 复用单票不可行。\n"
            "       走 R2（无窗口内核，脚本自跑）或 R3（Node 里解挑战 JS 自铸票）。")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", default="", help="票文件（grab_page 或 token_pool 产物）")
    ap.add_argument("--proxy", default="", help="出口；留空=直连（票必须同出口铸）")
    ap.add_argument("--ua", default="", help="覆盖票里的 UA（必须与铸票浏览器一致）")
    ap.add_argument("--alt-imp", default="chrome131", help="变体 2 的 TLS 画像")
    ap.add_argument("--alt-ua", default="", help="变体 3/4 的替换 UA")
    ap.add_argument("--alt-ua-imp", default="chrome131", help="变体 3 配合替换 UA 的 TLS 画像")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--plan", action="store_true", help="只打印计划，不发请求")
    a = ap.parse_args()

    alt_ua = a.alt_ua or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    ticket: dict = {"cookies": [], "ua": "", "proxy": ""}
    if a.ticket:
        ticket = load_ticket(a.ticket)
    ua = a.ua or ticket.get("ua") or ""
    proxy = a.proxy or ticket.get("proxy") or ""

    if not ua:
        print("!! 票里没有 UA。需要铸票浏览器一字不差的 UA，否则变体 1 会失败。")
        print("   先跑：python -c \"from token_pool import Store;p=Store();print([s.get('ua') for s in p.all()][:3])\"")
        print("   或手工：--ua \"Mozilla/5.0 ... Chrome/156.0.0.0 Safari/537.36\"")
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        print(f"   → 临时使用默认 UA（{ua_version(ua)}），判读时请打折\n")

    base_imp = pick_imp(ua_version(ua))
    alt_imp = a.alt_imp
    alt_ua_imp = a.alt_ua_imp
    # 变体退化保护：如果两个 TLS 画像一样，实验等于白跑
    if alt_imp == base_imp:
        alt_imp = "chrome136" if base_imp != "chrome136" else "chrome124"
        print(f"!! --alt-imp 与基线相同，自动改成 {alt_imp}（否则变体 2 无意义）")
    # 替换 UA 也要真的不同，否则变体 3/4 测的是同一件事
    if ua_version(alt_ua) == ua_version(ua):
        alt_ua = re.sub(r"Chrome/\d+\.\d+\.\d+\.\d+", "Chrome/124.0.0.0", alt_ua)
        print(f"!! --alt-ua 与铸票 UA 同版本，自动改成 {ua_version(alt_ua)}")
    if ua_version(alt_ua) == ua_version(ua):
        alt_ua_imp = "chrome124" if alt_ua_imp != "chrome124" else "chrome131"

    plan = build_plan(ua, base_imp, alt_imp, alt_ua, alt_ua_imp)

    print(f"目标   : {URL}")
    print(f"出口   : {proxy or '直连'}")
    print(f"铸票 UA: {ua[:95]}")
    print(f"票 cookie 数: {len(ticket.get('cookies') or [])}")
    hik = [c["name"] for c in (ticket.get("cookies") or []) if "HIK0" in (c.get("name") or "")]
    print(f"HIK0   : {hik or '缺失！票不是过挑战后的会话'}")
    print()

    if a.plan:
        for i, v in enumerate(plan):
            print(f"[{i}] {v['tag']}")
            print(f"      impersonate={v['impersonate']}  http2={v['http2']}  "
                  f"头 {len(v['headers'])} 项  "
                  f"cookie {'带' if v['cookies'] else '不带'}"
                  f"（{len(ticket.get('cookies') or [])} 条）")
        print("\n（--plan 不发请求。去掉该参数即开始实验。）")
        print("（Windows 控制台若中文乱码：先执行 chcp 65001）")
        return 0

    if not ticket.get("cookies"):
        print("没有票，只跑分诊。")
        plan = plan[:1]

    p = Probe(proxy=proxy, min_interval=a.interval)
    rows: List[dict] = []
    for v in plan:
        rows.append(p.once(v["tag"], ticket.get("cookies") if v["cookies"] else [],
                           v["impersonate"], v["headers"], v["http2"]))
        if rows[-1].get("tripped"):
            print(f"  !! 熔断：{rows[-1]['tripped']}")
            break

    print("\n" + "=" * 74)
    print(verdict(rows) if len(rows) > 1 else f"分诊：{DIAG.get(rows[0].get('code'), '?')}")
    print("=" * 74)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-bindprobe.json"
    out.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": URL, "proxy": proxy, "mint_ua": ua,
        "hik0": hik, "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out.relative_to(BASE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
