# -*- coding: utf-8 -*-
"""_getip.py —— 从巨量IP postpay 接口提 IP（处理中文 city 参数编码）

用法：
  python _getip.py "<url>" [次数]
输出：
  ip:port 逐行打印，同时写到 runs/fleet/ips.txt
"""
import sys, time, urllib.parse, urllib.request
from pathlib import Path

url = sys.argv[1] if len(sys.argv) > 1 else ""
n = int(sys.argv[2]) if len(sys.argv) > 2 else 1

# 把 URL 的 query 部分逐个参数重新编码（中文 city 必须转 %E6%B7%B1%E5%9C%B3）
sp = urllib.parse.urlsplit(url)
q = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
safe_query = urllib.parse.urlencode(q, encoding="utf-8")
full = urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path, safe_query, sp.fragment))
print(f"请求: {full}")

ips = []
for i in range(n):
    try:
        with urllib.request.urlopen(full, timeout=20) as r:
            txt = r.read().decode("utf-8", "ignore").strip()
    except Exception as e:
        print(f"  [{i+1}] 异常 {type(e).__name__}: {str(e)[:120]}")
        break
    print(f"  [{i+1}] {txt[:300]}")
    for line in txt.splitlines():
        line = line.strip()
        if line.count(":") == 1 and line.replace(":", "").replace(".", "").isdigit():
            ips.append(line)
    time.sleep(0.5)

if ips:
    Path("runs/fleet/ips.txt").parent.mkdir(parents=True, exist_ok=True)
    Path("runs/fleet/ips.txt").write_text("\n".join(ips) + "\n", encoding="ascii")
    print(f"\n写入 {len(ips)} 个 → runs/fleet/ips.txt")
else:
    print("\n没有解析到 IP")
