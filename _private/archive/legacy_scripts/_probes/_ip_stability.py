# -*- coding: utf-8 -*-
"""出口稳定性实测：N 次 abs3 + 每 k 次查一次出口 IP。

判读：
  status 全 200            → 出口未被 WAF 判死
  出口 IP 恒定            → 粘性会话，可绑 Queue-it / WAF 票
  出现 400                 → 立刻停（该出口已被拉黑，再打只会更死）
"""
from __future__ import annotations

import json
import statistics
import sys
import time

from curl_cffi import CurlHttpVersion, requests as cr

PROXY = "http://jlipiLP7OB:boOKSVho@14.215.163.195:2018"
TARGET = "https://abs3.td.gov.hk/?lang=sc"
IPINFO = "https://ipinfo.io/json"
N = 20
STEP = 2.0


def new_session() -> "cr.Session":
    s = cr.Session(verify=False, trust_env=False)
    return s


def main() -> int:
    s = new_session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"),
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    rows = []
    egress = []
    ok = bad = rst = 0
    t_start = time.time()
    for i in range(1, N + 1):
        t0 = time.time()
        rec = {"n": i}
        try:
            r = s.get(TARGET, proxy=PROXY, timeout=20, allow_redirects=False,
                      http_version=CurlHttpVersion.V1_1)
            body = r.text or ""
            rec.update(status=r.status_code, len=len(body),
                       ms=int((time.time() - t0) * 1000),
                       hwwaf="HWWAFSESID" in " ".join(s.cookies.keys()),
                       chal=("$_ts" in body[:6000] or "nsid" in body[:200]))
            if r.status_code == 200:
                ok += 1
            else:
                bad += 1
        except Exception as e:
            rec.update(err=f"{type(e).__name__}: {str(e)[:90]}",
                       ms=int((time.time() - t0) * 1000))
            if "56" in str(e) or "reset" in str(e).lower():
                rst += 1
            bad += 1
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if rec.get("status") == 400:
            print("!! 400：出口已被判死，停止测试", flush=True)
            break
        if i % 5 == 0 or i == 1:
            try:
                a = s.get(IPINFO, proxy=PROXY, timeout=15, verify=False,
                          http_version=CurlHttpVersion.V1_1)
                d = a.json()
                egress.append({"n": i, "ip": d.get("ip"), "org": d.get("org"),
                               "city": d.get("city")})
                print("   出口 " + json.dumps(egress[-1], ensure_ascii=False), flush=True)
            except Exception as e:
                print(f"   出口查询失败 {type(e).__name__}", flush=True)
        time.sleep(STEP)

    ms = [r["ms"] for r in rows if r.get("ms")]
    ips = {e["ip"] for e in egress}
    summary = {
        "elapsed_s": round(time.time() - t_start, 1),
        "requests": len(rows), "ok_200": ok, "bad": bad, "recv_reset": rst,
        "ms_median": int(statistics.median(ms)) if ms else None,
        "ms_p90": sorted(ms)[int(len(ms) * 0.9) - 1] if ms else None,
        "egress_ips": sorted(x for x in ips if x),
        "egress_stable": len(ips) == 1,
        "egress": egress,
    }
    print("\n==== 汇总 ====")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    open("runs/stability_20261114.json", "w", encoding="utf-8").write(
        json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, indent=1))
    return 0 if ok and not bad else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
