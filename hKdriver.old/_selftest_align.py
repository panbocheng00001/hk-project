# -*- coding: utf-8 -*-
"""_selftest_align.py —— R1 对齐链路的**离线**端到端验证（不发一个外网请求）

验证四件事：
  1. 票里的头是否被逐字重放（含大小写、含 sec-ch-ua）
  2. cookie 是否按 domain/path 正确注入
  3. URL 自动判 XHR：`/spa-api/` 走 XHR 头（Accept=application/json），文档走导航头
  4. Referer 同源安全阀：跨源 Referer 一律不发
  5. 注入的会话能被 Sniper 直接接管（节流闸门 / 分类逻辑不炸）

做法：本地起一个记录型 HTTP 服务器当靶子，票里的 domain 改成 127.0.0.1。
"""
from __future__ import annotations
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

SEEN = []


class Recorder(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        SEEN.append({"method": self.command, "path": self.path,
                     "headers": {k: v for k, v in self.headers.items()},
                     "body": body[:200].decode("utf-8", "replace")})
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), Recorder)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{port}"
    print(f"记录型靶机: {host}")

    # 一张「合成票」：头是浏览器真会发的那几项，用来验重放保真度
    ticket = {
        "ip_id": "selftest",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "proxy": "",
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"),
        "headers": {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "zh-HK",
            "sec-ch-ua": '"Not:A-Brand";v="8", "Chromium";v="156"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "upgrade-insecure-requests": "1",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"),
            "cookie": "should_be_dropped=1",          # 必须被丢掉，由 jar 负责
            "host": "should_be_dropped",              # 必须被丢掉
        },
        "cookies": [
            {"name": "HWWAFSESID", "value": "abc123", "domain": "127.0.0.1", "path": "/"},
            {"name": "HIK0kat3rt9xO", "value": "tok456", "domain": "127.0.0.1", "path": "/"},
        ],
        "impersonate": "",
        "probe": {},
    }

    from align_client import AlignedSession, clean_headers, xhr_headers, profile_candidates
    from sniper import Sniper, Log

    print(f"候选画像: {profile_candidates('156')}")
    nav = clean_headers(ticket["headers"])
    assert "host" not in {k.lower() for k in nav}, "host 没被丢掉"
    assert "cookie" not in {k.lower() for k in nav}, "cookie 头没被丢掉"
    print(f"头部裁剪: {len(ticket['headers'])} → {len(nav)} 项（host/cookie 已剔除）")

    ok = True
    s = AlignedSession(ticket, impersonate="chrome136")
    try:
        # 1) 文档请求
        r = s.get(host + "/doc")
        seen = SEEN[-1]
        h = {k.lower(): v for k, v in seen["headers"].items()}
        print(f"\n[文档] {seen['method']} {seen['path']} → {r.status_code}")
        for k in ("user-agent", "sec-ch-ua", "sec-ch-ua-platform", "accept-language"):
            print(f"   {k:22} = {h.get(k, '—')[:70]}")
        print(f"   cookie               = {h.get('cookie', '—')}")
        checks = [
            ("UA 逐字一致", h.get("user-agent") == ticket["headers"]["user-agent"]),
            ("sec-ch-ua 逐字一致", h.get("sec-ch-ua") == ticket["headers"]["sec-ch-ua"]),
            ("accept 逐字一致", h.get("accept") == ticket["headers"]["accept"]),
            ("cookie 来自 jar", "HWWAFSESID=abc123" in (h.get("cookie") or "")
             and "HIK0kat3rt9xO=tok456" in (h.get("cookie") or "")),
            ("伪 cookie 头被丢", "should_be_dropped=1" not in (h.get("cookie") or "")),
            ("host 由 curl 决定", "should_be_dropped" not in h.get("host", "")),
        ]

        # 2) XHR 自动识别
        r2 = s.post(host + "/spa-api/queue/tdabsditest/e1/enqueue", json={})
        seen2 = SEEN[-1]
        h2 = {k.lower(): v for k, v in seen2["headers"].items()}
        print(f"\n[XHR ] {seen2['method']} {seen2['path']} → {r2.status_code}")
        print(f"   accept = {h2.get('accept')}")
        checks.append(("XHR accept 已切换", h2.get("accept") == "application/json, text/plain, */*"))
        checks.append(("XHR 仍带原 UA", h2.get("user-agent") == ticket["headers"]["user-agent"]))

        # 3) Referer 同源安全阀
        s.get(host + "/doc2", headers={"Referer": "https://abs1.td.gov.hk/x"})
        h3 = {k.lower(): v for k, v in SEEN[-1]["headers"].items()}
        print(f"\n[Referer] 跨源 Referer 是否被拦：{'是（未发送）' if 'referer' not in h3 else '否——有 bug'}")
        checks.append(("跨源 Referer 被拦", "referer" not in h3))
        s.get(host + "/doc3", headers={"Referer": host + "/doc"})
        h4 = {k.lower(): v for k, v in SEEN[-1]["headers"].items()}
        checks.append(("同源 Referer 照发", h4.get("referer") == host + "/doc"))

        # 4) Sniper 接管
        sp = Sniper(session=s, log=Log("selftest", file=False), min_interval=0.05)
        r4 = sp._get(host + "/via-sniper", allow_redirects=False)
        checks.append(("Sniper 接管不炸", r4.status_code == 200))
        checks.append(("节流闸门就位", hasattr(sp, "th") and sp.th is not None))
        print(f"\n[Sniper] {r4.status_code}  闸门={'ok' if getattr(sp,'th',None) else 'missing'}")
    finally:
        s.close()

    print("\n--- 校验 ---")
    for name, cond in checks:
        print(f"  {'✅' if cond else '❌'} {name}")
        ok = ok and cond
    srv.shutdown()
    print("\n" + ("离线自检全部通过：R1 重放链路保真" if ok else "有失败项，别上真票"))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
