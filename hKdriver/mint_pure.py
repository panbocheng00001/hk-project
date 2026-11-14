# -*- coding: utf-8 -*-
"""mint_pure.py —— 纯脚本取票（零浏览器进程）的接线层

它把「HTTP 指纹」和「JS 挑战」拆成两半，各自用对的工具：

    curl_cffi(chrome136 画像)  ← 负责 TCP/TLS/HTTP2/UA 指纹，全程同一个会话
            │  ① GET abs3/?lang=sc
            ▼
    412 + $_ts{nsd,cd} + /RGOj2…/xxx.js
            │  ② 同一个会话把挑战 JS 取下来（Referer 同源）
            ▼
    node wafnode/solve.js      ← 负责跑挑战 VM，产出 document.cookie
            │  ③ stdin/stdout JSON 契约
            ▼
    同一个 curl_cffi 会话装 cookie → 再 GET 一次
            │  ④ 200/302 且 jar 里有 HIK0* = 票到手
            ▼
    交给 sniper.py --align <票>

为什么必须同一会话：2026-11-12 16:32 实测「弱票 200 / 硬票(HIK0) 全画像 400」，
说明票与铸票时的出口 IP + TLS/UA 三元组绑定。换客户端重放 = 白铸。

用法
    python mint_pure.py --dry                  # 只跑 ①②，看是不是 412 挑战窗口
    python mint_pure.py --ip-id w1             # 全跑，票写到 runs/tickets/w1.json
    python mint_pure.py --proxy http://u:p@h:p --ip-id r1

退出码
    0   票到手（有 HIK0）
    2   站点没挑战（200/302）——裸 HTTP 就能打，不一定需要票
    41  挑战没解出来（solve.js 没产出 cookie）
    42  装 cookie 后仍不放行（票不认 / 出口已死）
    7   出口连不上
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
TICKETS = BASE / "runs" / "tickets"
WORK = BASE / "runs" / "wafnode"
SRC = "https://abs3.td.gov.hk/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


def now() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str, tag: str = "·") -> None:
    print(f"{now()} {tag} {msg}", flush=True)


def parse412(html: str) -> dict:
    """从 412 页里取出种子与挑战脚本地址。"""
    out = {"nsd": None, "cd": None, "js_path": None}
    m = re.search(r"\$_ts\.nsd\s*=\s*(\d+)", html)
    if m:
        out["nsd"] = int(m.group(1))
    m = re.search(r'\$_ts\.cd\s*=\s*"([^"]+)"', html)
    if m:
        out["cd"] = m.group(1)
    m = re.search(r'src="([^"]+\.js)"', html)
    if m:
        out["js_path"] = m.group(1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="纯脚本取票：curl_cffi + node 挑战求解")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--ip-id", default="w1")
    ap.add_argument("--impersonate", default="chrome136",
                    help="curl_cffi 画像；必须与后续所有请求保持一致")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--dry", action="store_true", help="只跑到取挑战 JS")
    ap.add_argument("--keep", action="store_true", help="保留中间产物（默认也保留）")
    a = ap.parse_args()

    try:
        from curl_cffi import requests as creq
    except Exception as e:  # 依赖缺失时给出可执行的补救
        log(f"缺 curl_cffi：{e}", "!")
        return 7

    WORK.mkdir(parents=True, exist_ok=True)
    TICKETS.mkdir(parents=True, exist_ok=True)

    s = creq.Session(impersonate=a.impersonate, verify=False, trust_env=False)
    if a.proxy:
        s.proxies = {"http": a.proxy, "https": a.proxy}

    # ---------- ① 首包：分诊 ----------
    t0 = time.time()
    try:
        r = s.get(SRC, timeout=a.timeout, allow_redirects=False,
                  headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    except Exception as e:
        log(f"① 连不上：{type(e).__name__}: {str(e)[:120]}", "!!")
        return 7
    ms = int((time.time() - t0) * 1000)
    body = r.text or ""
    log(f"① GET abs3 → {r.status_code}  {len(body)}B  {ms}ms  画像={a.impersonate}")
    jar0 = {c.name: c.value for c in s.cookies.jar}
    log(f"   cookie: {sorted(jar0)}")

    if r.status_code in (200, 302):
        log("① 站点当前不挑战——裸 HTTP 就能打，票不是必需品", "√")
        (WORK / f"{a.ip_id}-200.html").write_text(body, encoding="utf-8")
        if a.dry:
            return 2

    if r.status_code == 400:
        log("① 400：这个出口已被判非法（别再试第二次）", "!!")
        return 42

    if r.status_code != 412:
        log(f"① 非预期状态 {r.status_code}，落盘后停", "!")
        (WORK / f"{a.ip_id}-{r.status_code}.html").write_text(body, encoding="utf-8")
        return 42

    # ---------- ② 解种子 + 取挑战 JS ----------
    seed = parse412(body)
    (WORK / f"{a.ip_id}-412.html").write_text(body, encoding="utf-8")
    log(f"② 种子 nsd={seed['nsd']} cd={len(seed['cd'] or '')}B  js={seed['js_path']}")
    if not (seed["nsd"] and seed["cd"] and seed["js_path"]):
        log("② 412 页结构变了（没找到 nsd/cd/脚本地址）", "!!")
        return 41

    js_url = "https://abs3.td.gov.hk" + seed["js_path"] if seed["js_path"].startswith("/") else seed["js_path"]
    try:
        rj = s.get(js_url, timeout=a.timeout,
                   headers={"Referer": SRC, "Accept": "*/*"})
    except Exception as e:
        log(f"② 取挑战 JS 失败：{type(e).__name__}", "!!")
        return 41
    log(f"② 挑战 JS → {rj.status_code}  {len(rj.content)}B")
    if rj.status_code != 200:
        log("② 挑战 JS 不是 200：出口/会话已经被降权，换出口重来", "!!")
        return 41
    js_file = WORK / f"{a.ip_id}-chall.js"
    js_file.write_bytes(rj.content)

    if a.dry:
        log("--dry：到这里为止（种子与挑战脚本已落盘）", "=")
        return 0

    # ---------- ③ node 跑挑战 VM ----------
    seed_file = WORK / f"{a.ip_id}-seed.json"
    seed_file.write_text(json.dumps({
        "nsd": seed["nsd"], "cd": seed["cd"], "js": str(js_file),
        "href": SRC, "ua": UA,
    }, ensure_ascii=False), encoding="utf-8")

    t1 = time.time()
    try:
        pr = subprocess.run(["node", str(BASE / "wafnode" / "solve.js"), "--seed", str(seed_file)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    except Exception as e:
        log(f"③ solve.js 调用失败：{e}", "!!")
        return 41
    cost = int((time.time() - t1) * 1000)
    try:
        res = json.loads((pr.stdout or "").strip().splitlines()[-1])
    except Exception:
        log(f"③ solve.js 没吐出 JSON：{pr.stdout[:200]!r} / {pr.stderr[:200]!r}", "!!")
        return 41
    log(f"③ 挑战求解 {cost}ms  ok={res.get('ok')}  写入={len(res.get('cookieSets') or [])}条")
    if res.get("missing"):
        log(f"   缺宿主能力：{json.dumps(res['missing'][:6], ensure_ascii=False)}", "~")
    if res.get("err"):
        log(f"   VM 报错：{res['err'][:160]}", "~")
    if not res.get("cookies"):
        log("③ 没解出 cookie —— 这一轮不算票", "!!")
        return 41

    # ---------- ④ 装 cookie 复验 ----------
    for name, value in res["cookies"].items():
        try:
            s.cookies.set(name, value, domain="abs3.td.gov.hk", path="/")
        except Exception:
            s.cookies.set(name, value)
    log(f"④ 装 cookie：{sorted(res['cookies'])}")

    try:
        r2 = s.get(SRC, timeout=a.timeout, allow_redirects=False,
                   headers={"Referer": SRC})
    except Exception as e:
        log(f"④ 复验连不上：{type(e).__name__}", "!!")
        return 42
    jar = {c.name: c.value for c in s.cookies.jar}
    hik = [k for k in jar if k.startswith("HIK0kat3rt9x")]
    log(f"④ 复验 → {r2.status_code}  {len(r2.text or '')}B  HIK0={hik}  "
        f"Queue={'QueueITAccepted' in ' '.join(jar)}")

    if r2.status_code == 412:
        log("④ 还是 412：挑战没真正通过（cookie 不对/时序不对）", "!!")
        return 41
    if r2.status_code == 400:
        log("④ 400：出口被打死", "!!")
        return 42
    if not hik:
        log("④ 没拿到 HIK0 —— 可能站点只是弱校验，直接带现有 cookie 试业务层", "~")

    ticket = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ip_id": a.ip_id,
        "proxy": a.proxy, "impersonate": a.impersonate,
        "status": r2.status_code, "hik0_names": hik,
        "cookies": jar, "cookie_sets": res.get("cookieSets") or [],
        "nav": res.get("nav") or [], "solve_ms": cost,
    }
    out = TICKETS / f"{a.ip_id}.json"
    out.write_text(json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"④ 票已落盘 {out.relative_to(BASE)}  cookies={len(jar)}", "√")
    log("下一步：python sniper.py --align " + str(out.relative_to(BASE)), "=")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
