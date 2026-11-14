#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sign_forensics.py —— 拦截「带签名/加密参数」的请求，并追溯生成它的 JS 函数
===========================================================================
干三件事：

1. **抓请求**：走 CDP 的 `Network.requestWillBeSent`，拿完整 URL / headers / POST body，
   以及最关键的 `initiator.stack.callFrames` —— 里面是「哪个 js 文件的第几行调用发起了这个请求」。
2. **认签名**：自动判定哪些参数像签名
   · 参数名是随机串（不在常见词表里，如 `sBCIo0XL`）
   · 参数值长且是 base64url/hex（≥40 字符）
   · header 里的 `authorization` / `x-sign` / `x-token` / `x-timestamp`
   · body 里的 `sign` / `token` / `jwt` / `nonce` / `timestamp`
3. **落证据**：命中项连同调用栈一起写 `runs/sign/<ts>/suspects.json`，
   并把调用栈里出现的 JS 文件下载到 `runs/sign/<ts>/js/` —— 后续直接在那些文件里找加密函数。

用法
----
    python sign_forensics.py --minutes 10          # 挂 10 分钟自动收
    python sign_forensics.py --minutes 40 --goto "https://abs3.td.gov.hk/?lang=sc"
    python sign_forensics.py --minutes 40 --stop-on-hit 5   # 抓到 5 条可疑就停

注意：`--goto` 会主动导航一次（可能引发 WAF 412）。不想触发的就手动在窗口里点，
本工具只监听。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent  # 本脚本放在项目根
ROOT = HERE
OUT_ROOT = ROOT / "runs" / "sign"
CDP = "http://127.0.0.1:9222"

# 常见「语义参数名」——不在这张表里的短参数名，很可能是 WAF/风控塞进来的动态参数
COMMON_QP = {
    "lang", "cmd", "id", "page", "type", "code", "func", "auth", "country",
    "action", "actiontype", "authmethod", "cid", "c", "e", "t", "ver", "man",
    "cver", "kupver", "sitekey", "v", "ts", "_", "r", "callback", "format",
    "service", "servicetype", "storedappointmentoperation",
    "storedappointmentexpirydate", "enablepick", "sid", "session", "token",
    "q", "query", "sort", "order", "limit", "offset", "pageNo", "ref", "from",
}
SIG_HEADERS = ("authorization", "x-sign", "x-signature", "x-token", "x-timestamp",
               "x-nonce", "sign", "signature", "x-api-key", "x-csrf-token")
SIG_BODY_KEYS = ("sign", "signature", "token", "jwt", "nonce", "timestamp",
                 "captchatoken", "helperjwt", "urltoken", "accesstoken")
B64 = re.compile(r"^[A-Za-z0-9_\-+/=]{40,}$")
HEX = re.compile(r"^[0-9a-fA-F]{32,}$")


def looks_like_sig(name: str, value: str) -> str:
    """返回命中理由；空串表示不像。"""
    n = name.lower()
    if n in SIG_HEADERS or any(k in n for k in ("sign", "signature", "nonce")):
        return "name"
    if B64.match(value) or HEX.match(value):
        if n not in COMMON_QP:
            return "random-name+long-value" if n not in COMMON_QP and len(n) <= 12 else "long-value"
        return "long-value"
    return ""


class Collector:
    def __init__(self, out: Path):
        self.out = out
        self.reqs: list[dict] = []
        self.suspects: list[dict] = []
        self.js_urls: set[str] = set()
        self.fh = open(out / "requests.jsonl", "a", encoding="utf-8")

    def on_request(self, ev: dict) -> None:
        req = ev.get("request") or {}
        url = req.get("url") or ""
        method = req.get("method") or ""
        headers = {k.lower(): v for k, v in (req.get("headers") or {}).items()}
        post = req.get("postData") or ""
        init = ev.get("initiator") or {}
        stack = (init.get("stack") or {})
        frames = stack.get("callFrames") or []

        reasons: list[str] = []
        hits: list[dict] = []

        # URL query
        if "?" in url:
            qs = url.split("?", 1)[1].split("#")[0]
            for kv in qs.split("&"):
                if "=" not in kv:
                    continue
                k, v = kv.split("=", 1)
                why = looks_like_sig(k, v)
                if why:
                    reasons.append(f"query:{k}")
                    hits.append({"where": "query", "name": k, "value": v[:160], "why": why})
        # headers
        for k, v in headers.items():
            why = looks_like_sig(k, str(v))
            if why and k not in ("user-agent", "accept", "accept-language",
                                 "accept-encoding", "content-type", "referer",
                                 "origin", "cookie", "connection", "host",
                                 "sec-fetch-dest", "sec-fetch-mode",
                                 "sec-fetch-site", "sec-fetch-user",
                                 "upgrade-insecure-requests", "content-length"):
                reasons.append(f"header:{k}")
                hits.append({"where": "header", "name": k, "value": str(v)[:160], "why": why})
        # body
        if post:
            low = post.lower()
            for key in SIG_BODY_KEYS:
                if key in low:
                    m = re.search(rf"{key}[^=&]*=([^&]{{8,}})", post, re.I)
                    reasons.append(f"body:{key}")
                    hits.append({"where": "body", "name": key,
                                 "value": (m.group(1)[:160] if m else "[present]"),
                                 "why": "key"})

        rec = {
            "t": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "method": method, "url": url[:400],
            "resource": ev.get("type"),
            "post_len": len(post),
            "initiator": frames[0]["url"] if frames else (init.get("url") or ""),
            "stack": [{"fn": f.get("functionName"), "url": f.get("url"),
                       "line": f.get("lineNumber"), "col": f.get("columnNumber")}
                      for f in frames[:8]],
            "hits": hits,
        }
        self.reqs.append(rec)
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()

        for f in frames:
            u = f.get("url") or ""
            if u.endswith(".js") or ".js?" in u:
                self.js_urls.add(u)

        if hits:
            self.suspects.append(rec)
            top = frames[0] if frames else {}
            where = (f"{top.get('url','?').split('/')[-1]}:{top.get('line',-1)}"
                     if top else "无调用栈")
            print(f"  ★ {method:5} {url.split('?')[0][-60]:60} "
                  f"[{','.join(reasons)}] ← {where}", flush=True)

    def close(self) -> None:
        self.fh.close()


def download_js(urls: set[str], dest: Path, limit: int = 40) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    n = 0
    for u in list(urls)[:limit]:
        try:
            with op.open(u, timeout=20) as r:
                data = r.read()
            name = hashlib.sha1(u.encode()).hexdigest()[:12] + "_" + \
                   re.sub(r"[^A-Za-z0-9._-]", "_", u.split("/")[-1])[:60]
            (dest / name).write_bytes(data)
            n += 1
        except Exception:
            pass
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--cdp", default=CDP)
    ap.add_argument("--goto", default="", help="可选：先导航一次以触发资源加载")
    ap.add_argument("--stop-on-hit", type=int, default=0, help="抓到 N 条可疑就停")
    a = ap.parse_args()

    ts = time.strftime("%Y%m%d-%H%M%S")
    out = OUT_ROOT / ts
    out.mkdir(parents=True, exist_ok=True)
    col = Collector(out)
    print(f"输出目录 {out}")

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(a.cdp)
        ctx = b.contexts[0]
        sessions = []

        def attach(pg):
            try:
                s = ctx.new_cdp_session(pg)
                s.send("Network.enable")
                s.on("Network.requestWillBeSent", lambda ev: col.on_request(ev))
                sessions.append(s)
                print(f"  已挂 CDP 会话：{pg.url[:80]}")
            except Exception as e:
                print("  attach 失败:", type(e).__name__, str(e)[:80])

        for pg in ctx.pages:
            attach(pg)
        ctx.on("page", attach)

        if a.goto:
            try:
                pg = ctx.pages[0]
                print(f"导航到 {a.goto}")
                pg.goto(a.goto, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:
                print("goto 异常:", type(e).__name__, str(e)[:100])

        deadline = time.time() + a.minutes * 60
        while time.time() < deadline:
            time.sleep(2)
            if a.stop_on_hit and len(col.suspects) >= a.stop_on_hit:
                print(f"已抓到 {len(col.suspects)} 条可疑，收工")
                break
        try:
            b.close()
        except Exception:
            pass

    col.close()
    (out / "suspects.json").write_text(
        json.dumps(col.suspects, ensure_ascii=False, indent=1), encoding="utf-8")
    n_js = download_js(col.js_urls, out / "js")
    summary = {
        "at": ts, "minutes": a.minutes,
        "requests": len(col.reqs), "suspects": len(col.suspects),
        "js_files": sorted(col.js_urls)[:50], "js_downloaded": n_js,
        "hit_reasons": sorted({h["where"] + ":" + h["name"]
                               for r in col.suspects for h in r["hits"]})[:60],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "js_files"},
                     ensure_ascii=False))
    print(f"JS 已下载 {n_js} 个 → {out / 'js'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
