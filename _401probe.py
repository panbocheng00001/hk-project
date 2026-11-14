# -*- coding: utf-8 -*-
"""_401probe.py —— 401 发牌 → 收下 → 重放，看能不能 200（2 次请求）

2026-11-12 16:40 实测直连 abs3：
    HTTP/1.1 401 Unauthorized
    Set-Cookie: HWWAFSESID / HWWAFSESTIME / HIK0kat3rt9xO(109字符)
    len=6（空 body），耗时 10125ms（被限速了 10 秒）

如果「收牌 + 重放」就 200，那挑战窗口内也有一条纯 HTTP 通路，
不需要浏览器，也不需要铸票。

只在同一个 Session 里做，两次请求，不打第三次。
"""
import sys
import time

sys.path.insert(0, ".")

from curl_cffi import requests as cr
from preflight import HDR_ALIGN

URL = "https://abs3.td.gov.hk/?lang=sc"
PROXY = sys.argv[1] if len(sys.argv) > 1 else ""

kw = {"impersonate": "chrome136", "timeout": 30, "verify": False,
      "allow_redirects": False, "headers": dict(HDR_ALIGN)}
if PROXY:
    kw["proxies"] = {"http": PROXY, "https": PROXY}
else:
    kw["trust_env"] = False

s = cr.Session(**kw)
print(f"出口: {PROXY or '直连'}")

t0 = time.time()
r1 = s.get(URL)
names = list(s.cookies.keys())
print(f"① 第一次  {r1.status_code}  len={len(r1.content or b'')}  "
      f"{time.time()-t0:.1f}s")
print(f"   cookies: {names}")
hik = {k: len(v) for k, v in s.cookies.items() if k.startswith("HIK0kat3rt9x")}
print(f"   HIK0   : {hik or '（无）'}")

if not hik:
    print("\n没有拿到 HIK0 牌 → 这条通路不成立（或当前不是 401 状态）")
    raise SystemExit(1)

time.sleep(1.0)
t1 = time.time()
r2 = s.get(URL)
print(f"\n② 收牌重放 {r2.status_code}  len={len(r2.content or b'')}  "
      f"{time.time()-t1:.1f}s")
body = (r2.content or b"")[:300].decode("utf-8", "replace").replace("\n", " ")
print(f"   body: {body[:200]}")

if r2.status_code in (200, 302):
    print("\n★ 收牌重放即通 —— 挑战窗口内存在纯 HTTP 通路！")
elif r2.status_code == 401:
    print("\n401 依旧 → 牌只是 seed，需要第二段握手（或算动态参数）")
elif r2.status_code == 412:
    print("\n412 → 转入真挑战，需要执行 JS")
else:
    print(f"\n{r2.status_code} → 未归类")
