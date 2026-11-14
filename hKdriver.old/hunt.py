# -*- coding: utf-8 -*-
"""hunt.py —— 提 IP + 快速测，找一个能返回 412（或 200）的出口"""
import subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

LINK = ("http://v2.api.juliangip.com/company/postpay/getips?auto_white=1&city=深圳&filter=1"
        "&isp=3&num=1&pt=1&result_type=text&split=1&trade_no=6280170248860427"
        "&sign=0080489861adf7c21cf67e611789576f")
TARGET = "https://abs3.td.gov.hk/?lang=sc"


def enc(u):
    sp = urllib.parse.urlsplit(u)
    q = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
    return urllib.parse.urlunsplit((sp.scheme, sp.netloc, sp.path,
                                    urllib.parse.urlencode(q), sp.fragment))


def fetch():
    try:
        with urllib.request.urlopen(enc(LINK), timeout=20) as r:
            t = r.read().decode("utf-8", "ignore").strip()
    except Exception as e:
        return None, f"{type(e).__name__}:{str(e)[:40]}"
    for line in t.splitlines():
        line = line.strip()
        if line.count(":") == 1 and line.replace(":", "").replace(".", "").isdigit():
            return line, ""
    return None, t[:60]


def probe(ip, timeout=8):
    try:
        r = subprocess.run(["curl.exe", "-k", "-x", f"http://{ip}", "-sS",
                            "--max-time", str(timeout), "-o", "NUL",
                            "-w", "%{http_code}", TARGET],
                           capture_output=True, text=True, timeout=timeout + 6)
        return (r.stdout or "").strip()[-3:], (r.stderr or "")[:60]
    except Exception as e:
        return "ERR", str(e)[:60]


hits = []
seen = set()
for i in range(1, 15):
    ip, err = fetch()
    if not ip:
        print(f"[{i:02d}] 提不到: {err}")
        time.sleep(1.5)
        continue
    if ip in seen:
        print(f"[{i:02d}] {ip} 重复")
        time.sleep(1.0)
        continue
    seen.add(ip)
    code, e = probe(ip)
    print(f"[{i:02d}] {ip:24} → {code} {e}")
    if code in ("412", "200", "302"):
        hits.append((ip, code))
        if code in ("200", "302"):
            break
    time.sleep(0.8)

Path("runs/fleet/hits.txt").parent.mkdir(parents=True, exist_ok=True)
Path("runs/fleet/hits.txt").write_text("\n".join(f"{i}:{c}" for i, c in hits) + "\n", encoding="ascii")
print(f"\n可用 {len(hits)} 个 → runs/fleet/hits.txt")
for i, c in hits:
    print(f"  {i}  {c}")
