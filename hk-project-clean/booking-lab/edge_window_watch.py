# -*- coding: utf-8 -*-
"""edge_window_watch.py —— 盯「WAF 放行窗口」：一旦 abs3 从 412 变 200，立刻叫你

背景（今天实测）：
  · 14:17 abs3 → 200（纯 HTTP 0.9s/次判号）
  · 16:30 之后 abs3 → 412（全站挑战），所有纯 HTTP 变形都被 400/412 挡回
  · 结论：412 是"站点策略窗口"，不是 IP 属性；窗口一开，纯 HTTP 全速

所以真正该做的是**等窗口**，不是硬撞。本脚本低频探测，窗口一出现就落盘 + 可选执行命令。

用法：
  python edge_window_watch.py --proxies runs/pool_runner/proxies.txt --interval 120
  python edge_window_watch.py --proxies ... --on-open "python http_flow.py --watch --minutes 30"

注意：
  · 默认 120s 一次，单出口最多 3 次就换（避免把 IP 打进限流名单）
  · 命中 200 后默认继续盯 5 轮确认不是偶发
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from pathlib import Path

URL = "https://abs3.example.test/?lang=sc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def probe(proxy: str, timeout: int = 15) -> tuple[str, int]:
    cmd = ["curl.exe", "-k", "-sS", "--max-time", str(timeout), "-A", UA,
           "-o", "NUL", "-w", "%{http_code}", URL]
    if proxy:
        cmd[1:1] = ["-x", proxy]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 8)
        code = (r.stdout or "").strip()[-3:]
        if r.returncode != 0 and not code:
            err = (r.stderr or "").lower()
            if "timed out" in err or "timeout" in err:
                return "DROP", 0
            if "resolve" in err:
                return "DNS", 0
            if "certificate" in err:
                return "TLS", 0
            return "ERR", 0
        return "HTTP", int(code) if code.isdigit() else 0
    except Exception:
        return "ERR", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxies", default="")
    ap.add_argument("--interval", type=float, default=120)
    ap.add_argument("--max-per-exit", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=0, help="0=无限")
    ap.add_argument("--on-open", default="", help="窗口出现时执行的命令")
    ap.add_argument("--out", default="runs/pool_runner/window_watch.jsonl")
    a = ap.parse_args()

    proxies = [""]
    if a.proxies and Path(a.proxies).exists():
        proxies = [l.strip() for l in Path(a.proxies).read_text(encoding="utf-8").splitlines() if l.strip()]

    used: dict[str, int] = {p: 0 for p in proxies}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    opened = 0

    print(f"盯窗口: {URL}  出口 {len(proxies)} 个  间隔 {a.interval}s")
    while True:
        n += 1
        # 选一个用得最少的出口
        cand = sorted(proxies, key=lambda p: used[p])
        px = cand[0]
        used[px] += 1
        if used[px] > a.max_per_exit and len(proxies) > 1:
            proxies_avail = [p for p in proxies if used[p] < a.max_per_exit]
            if proxies_avail:
                px = sorted(proxies_avail, key=lambda p: used[p])[0]
                used[px] += 1

        kind, code = probe(px)
        ts = time.strftime("%H:%M:%S")
        line = {"t": ts, "proxy": px, "kind": kind, "code": code}
        print(f"{ts}  #{n:03d}  {px or '直连':28}  {kind} {code or ''}")
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

        if kind == "HTTP" and code == 200:
            opened += 1
            print(f"  ★★★ 窗口打开！（第 {opened} 次确认）")
            if opened >= 3:
                print("\n=== 窗口已稳定，执行 on-open ===")
                if a.on_open:
                    subprocess.run(a.on_open, shell=True)
                return 0
        else:
            opened = 0

        if a.rounds and n >= a.rounds:
            print("轮次用完，退出")
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
