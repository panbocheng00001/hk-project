# -*- coding: utf-8 -*-
"""proxies_fetch.py —— 从 API 批量取代理并逐个验活（替掉手点）

为什么要验：巨量IP 这类动态池子，「取到」不等于「能用」。
实测（2026-11-11 22:45）：同一批 API 连取两条，第一条 HTTP 504（上游坏），
第二条 200。不验活就铸票，等于拿一条死出口去烧一张票。

判定口径（严格按项目红线）
  · HTTP 探活只用一次 GET www.baidu.com，不做重试 —— 探活本身也是在花出口配额
  · 出口 IP 与代理地址常常不同（转发型）：票绑的是**出口 IP**，所以必须记下来
  · 稳定性要额外测：同一代理连取 3 次出口 IP，漂了就标 unstable（票会当场作废）

用法
---------------------------------------------------------------------------
  python proxies_fetch.py --count 1              # 取 1 条并验活
  python proxies_fetch.py --count 5 --stability  # 取 5 条，各测 3 次出口稳定性
  python proxies_fetch.py --check-only           # 只验 proxies.txt 里现有的

API 地址放在 proxies_api.txt（含 trade_no / sign，属于凭据，别提交）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

BASE = Path(__file__).resolve().parent
API_FILE = BASE / "proxies_api.txt"
OUT_TXT = BASE / "proxies.txt"
OUT_JSON = BASE / "runs" / "proxies.json"
TICKET_DIR = BASE / "runs" / "tickets"
DEFAULT_API = (
    "http://v2.api.juliangip.com/company/dynamic/getips?auto_white=1&num={n}"
    "&pt=1&result_type=text&split=1"
    "&trade_no=2977409725466488&sign=d2210aad5b296a631c2a4181b520da8c"
)
CURL = "curl.exe"


def curl(args: List[str], timeout: int = 25) -> tuple[int, str]:
    try:
        # 必须显式 utf-8：默认按 GBK 解码，遇到中文/UTF-8 字节会抛
        # UnicodeDecodeError（实测 2026-11-12 16:37），并且会让异常文本混进结果。
        p = subprocess.run([CURL, "-s", "-m", str(timeout)] + args,
                           capture_output=True, text=True, timeout=timeout + 5,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def next_ticket_index() -> int:
    """下一个可用票号：现有 p<N>.json 的最大值 +1，避免覆盖。"""
    if not TICKET_DIR.exists():
        return 1
    used = []
    for f in TICKET_DIR.glob("p*.json"):
        digits = f.stem[1:]
        if digits.isdigit():
            used.append(int(digits))
    return (max(used) + 1) if used else 1


def probe(proxy: str) -> Dict[str, Any]:
    """一次探活：能不能出网 + 出口 IP 是多少。"""
    t0 = time.time()
    rc, body = curl(["-o", "NUL", "-w", "%{http_code}", "-x", proxy,
                     "http://www.baidu.com"], timeout=20)
    ms = int((time.time() - t0) * 1000)
    ok = body.strip() == "200"
    exit_ip, isp = "", ""
    if ok:
        # ip-api 的 line 模式字段顺序不保证，先试 query,isp，失败退到只查 IP
        for fields in ("query,isp", "query"):
            rc2, txt = curl(["-x", proxy,
                             f"http://ip-api.com/line/?fields={fields}"], timeout=15)
            parts = [s.strip() for s in (txt or "").splitlines() if s.strip()]
            for s in parts:
                if s.count(".") == 3 and s.replace(".", "").isdigit():
                    exit_ip = s
                elif (s and not isp and s.lower() not in ("fail", "query")
                      and not any(k in s.lower() for k in
                                  ("timeout", "error", "gateway", "too many", "rate limit"))):
                    isp = s
            if exit_ip:
                break
    return {"proxy": proxy, "ok": ok, "code": body.strip(), "ms": ms,
            "exit_ip": exit_ip, "isp": isp}


def stability(proxy: str, n: int = 3) -> List[str]:
    ips = []
    for i in range(n):
        if i:
            time.sleep(1.5)
        rc, txt = curl(["-x", proxy, "http://ip-api.com/line/?fields=query"], timeout=15)
        parts = [s.strip() for s in (txt or "").splitlines() if s.strip()]
        for s in parts:
            if s.count(".") == 3 and s.replace(".", "").isdigit():
                ips.append(s)
                break
    return ips


def fetch(api: str, count: int) -> List[str]:
    url = api.replace("{n}", str(count)) if "{n}" in api else api
    rc, body = curl([url], timeout=30)
    out = []
    for line in (body or "").splitlines():
        s = line.strip().replace("<br>", "").replace("\r", "")
        if not s:
            continue
        if s.startswith("http://") or s.startswith("https://"):
            s = s.split("//", 1)[1]
        # 只认 ip:port，其余（IndexError、额度不足等错误文本）一律丢掉
        if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}", s):
            if s and len(s) < 120:
                print(f"  ! 跳过非 IP 行：{s}")
            continue
        out.append("http://" + s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="取代理并验活")
    ap.add_argument("--api", default="", help="取 IP 的 API（默认读 proxies_api.txt）")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--stability", action="store_true", help="每个代理测 3 次出口是否漂移")
    ap.add_argument("--triage", action="store_true",
                    help="额外对目标站分诊 1 次（多花 1 次 abs3 配额，但能直接筛掉被 WAF 打死的出口）")
    ap.add_argument("--check-only", action="store_true", help="只验 proxies.txt 现有条目")
    ap.add_argument("--append", action="store_true", help="追加而不是覆盖 proxies.txt")
    a = ap.parse_args()

    api = a.api
    if not api and API_FILE.exists():
        api = API_FILE.read_text(encoding="utf-8").strip()
    if not api and not a.check_only:
        (API_FILE).write_text(DEFAULT_API, encoding="utf-8")
        api = DEFAULT_API
        print(f"已把 API 写入 {API_FILE.name}（含 trade_no/sign，属凭据）")

    if a.check_only:
        cand = [ln.strip() for ln in OUT_TXT.read_text(encoding="utf-8").splitlines()
                if ln.strip()] if OUT_TXT.exists() else []
        if not cand:
            print("proxies.txt 是空的")
            return 10
    else:
        print(f"向 API 取 {a.count} 条 …")
        cand = fetch(api or DEFAULT_API, a.count)
        print(f"取到 {len(cand)} 条：{', '.join(c.split('//')[-1] for c in cand)}")
        if not cand:
            print("! 没取到 IP：检查 sign 是否过期、余额、或 auto_white 是否要换")
            return 11

    rows: List[Dict[str, Any]] = []
    for px in cand:
        r = probe(px)
        if a.stability and r["ok"]:
            ips = stability(px, 3)
            r["exit_ips"] = ips
            r["unstable"] = len(set(ips)) > 1
        if a.triage and r["ok"]:
            # 能上百度不代表能过目标 WAF：这条才是真正决定能不能用的判据
            try:
                from preflight import triage as _triage
                tr = _triage(proxy=px)
                r["triage"] = tr
                r["target_ok"] = tr.get("verdict") in ("OK", "CHALLENGE")
                time.sleep(1.0)
            except Exception as e:
                r["triage"] = {"verdict": "OTHER", "hint": f"{type(e).__name__}"}
                r["target_ok"] = False
        rows.append(r)
        tag = "✅" if r["ok"] else "❌"
        extra = ""
        if r.get("exit_ips") is not None:
            extra += f"  出口序列={r['exit_ips']}" + ("  ⚠漂移" if r.get("unstable") else "  稳定")
        if "triage" in r:
            tr = r["triage"]
            extra += f"  目标站={tr.get('verdict')}"
        print(f"  {tag} {px:34} HTTP={r['code']:>3} {r['ms']:5}ms  "
              f"exit={r['exit_ip'] or '—':16} {r['isp'][:20]}{extra}")

    good = [r for r in rows if r["ok"] and not r.get("unstable")
            and r.get("target_ok", True)]
    if good and not a.check_only:
        lines = [r["proxy"] for r in good]
        if a.append and OUT_TXT.exists():
            old = [ln.strip() for ln in OUT_TXT.read_text(encoding="utf-8").splitlines()
                   if ln.strip()]
            lines = list(dict.fromkeys(old + lines))
        OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n已写入 {OUT_TXT.name}：{len(lines)} 条（可用 {len(good)} 条）")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    prev = []
    if OUT_JSON.exists():
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8")).get("batches", [])
        except Exception:
            prev = []
    prev.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": len(rows), "rows": rows})
    OUT_JSON.write_text(json.dumps({"batches": prev[-20:]}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"→ {OUT_JSON.relative_to(BASE)}")

    print("\n下一步（票绑出口 IP，别换出口）：")
    if good:
        px0 = good[0]["proxy"]
        idx = next_ticket_index()
        print(f'  python mint_align.py --ip-id p{idx} --proxy "{px0}"')
        print(f"  python align_client.py --ticket runs/tickets/p{idx}.json --probe")
        print(f"  python sniper.py --align runs/tickets/p{idx}.json --all")
    return 0 if good else 12


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
