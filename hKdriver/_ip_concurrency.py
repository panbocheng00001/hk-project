# -*- coding: utf-8 -*-
"""同一出口的并发能力：8 连接 × 4 轮，看是否出现 400/重置/队头阻塞。"""
from __future__ import annotations

import json
import sys
import threading
import time

from curl_cffi import CurlHttpVersion, requests as cr

PROXY = "http://jlipiLP7OB:boOKSVho@14.215.163.195:2018"
TARGET = "https://abs3.td.gov.hk/?lang=sc"
THREADS = 8
ROUNDS = 4
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

lock = threading.Lock()
rows: list[dict] = []


def worker(tid: int) -> None:
    s = cr.Session(verify=False, trust_env=False)
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    for i in range(ROUNDS):
        t0 = time.time()
        rec = {"t": tid, "i": i}
        try:
            r = s.get(TARGET, proxy=PROXY, timeout=25, allow_redirects=False,
                      http_version=CurlHttpVersion.V1_1)
            rec.update(status=r.status_code, len=len(r.content),
                       ms=int((time.time() - t0) * 1000))
        except Exception as e:
            rec.update(err=f"{type(e).__name__}:{str(e)[:60]}",
                       ms=int((time.time() - t0) * 1000))
        with lock:
            rows.append(rec)


def main() -> int:
    t0 = time.time()
    ths = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    wall = time.time() - t0
    rows.sort(key=lambda r: (r["t"], r["i"]))
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    ok = sum(1 for r in rows if r.get("status") == 200)
    codes: dict[str, int] = {}
    for r in rows:
        k = str(r.get("status") or r.get("err", "")[:28])
        codes[k] = codes.get(k, 0) + 1
    ms = [r["ms"] for r in rows]
    out = {"threads": THREADS, "rounds": ROUNDS, "total": len(rows), "ok_200": ok,
           "wall_s": round(wall, 2), "codes": codes,
           "ms_max": max(ms), "ms_avg": int(sum(ms) / len(ms))}
    print("\n==== 并发汇总 ====")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    open("runs/concurrency_20261114.json", "w", encoding="utf-8").write(
        json.dumps({"rows": rows, "summary": out}, ensure_ascii=False, indent=1))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
