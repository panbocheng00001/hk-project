# -*- coding: utf-8 -*-
"""go1630.py —— 一条命令跑完「取新出口 → 分诊 → 排队 → 放行 → 判号」

它是 sniper.py 的"上门司机"：只负责换出口与分支决策，业务逻辑全在 sniper 里。

    python go1630.py --minutes 45                # 默认：取 1 个巨量IP 出口，跑到判号
    python go1630.py --minutes 45 --retries 3    # 出口被打就再取，最多 3 个
    python go1630.py --dry                       # 只取出口 + 分诊，不排队

分支：
    200/302 → sniper --all（纯 HTTP 全链）
    412     → 挑战窗口：默认交给 r2_flow.py（无窗口内核，票不搬家）
    400     → 这个出口已死，换一个（不重试同一个）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs" / "go1630"
API = ("http://v2.api.juliangip.com/company/postpay/getips?auto_white=1&city=%E6%B7%B1%E5%9C%B3"
       "&filter=1&isp=3&num=1&pt=1&result_type=text&split=1"
       "&trade_no=6280170248860427&sign=0080489861adf7c21cf67e611789576f")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")
TARGET = "https://abs3.td.gov.hk/?lang=sc"


def now() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str, tag: str = "·") -> None:
    print(f"{now()} {tag} {msg}", flush=True)


def fetch_proxy(timeout: float = 20.0) -> str:
    from curl_cffi import requests as creq
    r = creq.get(API, timeout=timeout)
    txt = (r.text or "").strip()
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})", txt)
    return m.group(1) if m else ""


def triage(proxy: str, timeout: float = 25.0, impersonate: str = "none",
           http1: bool = True) -> tuple[int, int, str]:
    """单次分诊：返回 (status, bytes, body_head)。一个出口只打这一次。

    注意：认证型转发代理（host:port:user:pass 那种）走不了 BoringSSL 的 h2/ALPS，
    chrome 画像会稳定 curl(28) 超时；分诊一律用普通 TLS + HTTP/1.1。
    """
    from curl_cffi import CurlHttpVersion, requests as creq
    # 与 sniper.py 同口径：verify=False（这些出口是转发型，证书链不完整）
    kw = {}
    if impersonate and impersonate.lower() not in ("none", "off", ""):
        kw["impersonate"] = impersonate
    s = creq.Session(verify=False, trust_env=False, **kw)
    if "://" not in proxy:
        proxy = "http://" + proxy
    s.proxies = {"http": proxy, "https": proxy}
    req = {}
    if http1:
        req["http_version"] = CurlHttpVersion.V1_1
    t0 = time.time()
    r = s.get(TARGET, timeout=timeout, allow_redirects=False, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}, **req)
    body = r.text or ""
    log(f"分诊 {proxy} → {r.status_code} {len(body)}B {int((time.time()-t0)*1000)}ms")
    return r.status_code, len(body), body[:400]


def run(cmd: list[str]) -> int:
    log("执行：" + " ".join(cmd), ">")
    return subprocess.call(cmd, cwd=str(BASE))


def main() -> int:
    ap = argparse.ArgumentParser(description="取新出口 + 分诊 + 全链")
    ap.add_argument("--minutes", type=float, default=45.0, help="排队观察窗口")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--retries", type=int, default=3, help="最多换几个出口")
    ap.add_argument("--worker", default="", help="config.json 申请人 id（可选）")
    ap.add_argument("--dry", action="store_true", help="只取出口+分诊")
    ap.add_argument("--engine", default="r2", choices=["r2", "mint"],
                    help="412 时用哪条：r2=无窗口内核 / mint=纯脚本取票")
    ap.add_argument("--proxy", default="",
                    help="直接用这条出口（http://u:p@h:p 或 h:p），跳过巨量IP 取号")
    ap.add_argument("--impersonate", default="none",
                    help="curl_cffi 画像；认证型转发代理必须 none")
    ap.add_argument("--http1", action="store_true", default=True,
                    help="强制 HTTP/1.1（默认开）")
    a = ap.parse_args()
    http1 = bool(a.http1)

    RUNS.mkdir(parents=True, exist_ok=True)
    py = sys.executable or "python"

    for attempt in range(1, (1 if a.proxy else max(1, a.retries)) + 1):
        proxy = a.proxy
        if proxy:
            log(f"#{attempt} 指定出口 {proxy}", "√")
        else:
            for _ in range(5):
                try:
                    proxy = fetch_proxy()
                except Exception as e:
                    log(f"取 IP 失败：{type(e).__name__}: {str(e)[:100]}", "!")
                    proxy = ""
                if proxy:
                    break
                time.sleep(1.5)
            if not proxy:
                log("连续取不到可用 IP，停", "!!")
                return 7
            log(f"#{attempt} 新出口 {proxy}", "√")

        try:
            status, size, head = triage(proxy, impersonate=a.impersonate, http1=http1)
        except Exception as e:
            log(f"分诊异常：{type(e).__name__}: {str(e)[:100]} → 换出口", "!")
            continue

        if status == 400:
            log("400：出口被判非法，直接换（不重试）", "!!")
            continue
        if status not in (200, 302, 412):
            log(f"非预期 {status}，换出口", "!")
            continue

        if a.dry:
            log("--dry：到此为止", "=")
            return 0

        if status == 412:
            log("412：站点进入挑战窗口", "~")
            purl = proxy if "://" in proxy else f"http://{proxy}"
            if a.engine == "mint":
                rc = run([py, "mint_pure.py", "--proxy", purl, "--ip-id", f"g{attempt}"])
                if rc != 0:
                    log(f"纯脚本取票未成功（rc={rc}），换出口/换 L2", "!")
                    if a.engine == "mint":
                        continue
                else:
                    rc = run([py, "sniper.py", "--align", f"runs/tickets/g{attempt}.json",
                              "--all", "--minutes", str(a.minutes),
                              "--interval", str(a.interval),
                              "--impersonate", a.impersonate] + (["--http1"] if http1 else [])
                             + (["--worker", a.worker] if a.worker else []))
                    return rc
            rc = run([py, "r2_flow.py", "--proxy", purl,
                      "--minutes", str(a.minutes), "--ip-id", f"g{attempt}"])
            return rc

        # 200 / 302：纯 HTTP 全链
        purl = proxy if "://" in proxy else f"http://{proxy}"
        cmd = [py, "sniper.py", "--all", "--proxy", purl,
               "--minutes", str(a.minutes), "--interval", str(a.interval),
               "--impersonate", a.impersonate] + (["--http1"] if http1 else [])
        if a.worker:
            cmd += ["--worker", a.worker]
        rc = run(cmd)
        (RUNS / "last.json").write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": proxy,
            "status": status, "exit": rc,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if rc == 0:
            log("★ 有名额（判号返回 0）—— 立刻进第 2 步", "√")
            return 0
        if rc == 10:
            log("无名额（正常）；如需继续盯，重跑本脚本", "~")
            return 10
        if rc == 42:
            log("排队未放行 / 会话不认，换出口再来", "~")
            continue
        return rc

    log("所有候选出口都没跑通", "!!")
    return 42


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
