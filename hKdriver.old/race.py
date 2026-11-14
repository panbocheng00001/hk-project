# -*- coding: utf-8 -*-
"""race.py —— 17:00 抢号总控（一键就位，边跑边记账）
=================================================================================
把之前分散的手工步骤串成一条：

    ① 分时起 4 个窗口（每个间隔 STAGGER 秒，避免同秒撞 412）
    ② 后台每 REFRESH 秒刷一次票（只刷该刷的），票池保持有票
    ③ 盯 runs/session_*.json：扩展一放行就自动 --ingest 进票池
    ④ 全程写 journal，事后一条命令出报告

它不做的事（刻意不做）：
    · 不点任何页面元素（填表交给扩展自己的状态机，避免两边抢同一个 DOM）
    · 不在浏览器里套代理（住宅代理 0.8–1.5s 延迟会拖慢排队轮询）

用法：
    python race.py                      # 默认 w1..w4，间隔 30s
    python race.py --insts w1,w2        # 只起两条
    python race.py --stagger 25 --until 17:30
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 当天时间表
# 这些不是注释，是默认行为：不传参数时 race.py 就按这张表走。
PLAN = [
    ("16:00", "站点体检：curl abs3（412/超时都算正常；只有 502/维护页要停手）"),
    ("16:15", "代理体检：python probe_proxy.py --file proxies.txt（只留住宅、出口一致）"),
    ("16:20", "预热票池：token_pool --warm --headless（不开可见窗口，不抢焦点）"),
    ("16:22", "起 w1；之后每 30–35s 起 w2 / w3 / w4"),
    ("16:35", "确认 4 条都进了 queue（看 runs\\watch.log）"),
    ("16:50", "再刷一次票（headless），把票的有效期覆盖到 17:20 之后"),
    ("16:57", "人坐屏幕前，钉钉/声音打开"),
    ("17:00", "洗牌放行 → 扩展自动走；卡在 SelectCountry 就手点「中国内地」"),
    ("17:00-17:20", "放行即自动 ingest；用 replay.py --pool --ip-id wN-form 跑满窗口"),
    ("17:20-18:00", "等取消回流，窗口别关，保持挂机"),
]

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
PAGE = BASE / "launch_ext.ps1"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
# DETACHED_PROCESS: 让子进程脱离本进程的 job 对象。
# 教训（17:04 实测）：直接 Stop-Process 掉总控，Windows 会把 job 里的子孙一起收走 ——
# 4 个浏览器窗口瞬间全没了。加上这个标志，总控挂了窗口还活着。
DETACHED = 0x00000008 if sys.platform == "win32" else 0

try:
    from journal import J
    from token_pool import Store, _store
except Exception:            # 记账/票池不可用也要能跑
    J = None
    Store = None
    _store = None


def log(msg: str, *a) -> None:
    line = (msg % a) if a else msg
    print(f"[{datetime.now():%H:%M:%S}] {line}", flush=True)
    if J is not None:
        try:
            J().log("race", "event", msg=line[:180])
        except Exception:
            pass


def read_status() -> list[dict]:
    p = RUNS / "ext_status.jsonl"
    if not p.exists():
        return []
    rows = []
    for ln in p.open(encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            for part in ln.replace("}{", "}\x00{").split("\x00"):
                try:
                    rows.append(json.loads(part))
                except Exception:
                    pass
    return rows


def last_stage_per_inst(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        inst = r.get("inst") or ""
        st = r.get("stage") or ""
        if inst and st and st not in ("beat",):
            out[inst] = (st, r.get("t", ""))
    return out


class Launcher(threading.Thread):
    def __init__(self, insts: list[str], stagger: float):
        super().__init__(daemon=True)
        self.insts, self.stagger = insts, stagger
        self.procs: dict[str, subprocess.Popen] = {}

    def launch_one(self, inst: str) -> None:
        prof = BASE / f"chrome_ext_{inst}"
        # --load-extension 会把扩展复制进 profile；不删就跑旧脚本
        shutil.rmtree(prof, ignore_errors=True)
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(PAGE),
               "-Instance", inst]
        self.procs[inst] = subprocess.Popen(cmd, cwd=str(BASE),
                                            creationflags=CREATE_NO_WINDOW | DETACHED,
                                            start_new_session=True,
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        log("已启动 %s（profile 已重置）", inst)

    def run(self) -> None:
        for i, inst in enumerate(self.insts):
            self.launch_one(inst)
            if i + 1 < len(self.insts):
                time.sleep(self.stagger)


class Refresher(threading.Thread):
    """只刷该刷的票；不阻塞主循环。"""

    def __init__(self, proxies: str, interval: float = 120.0, workers: int = 4,
                 headless: bool = True):
        super().__init__(daemon=True)
        self.proxies, self.interval, self.workers = proxies, interval, workers
        self.headless = headless
        self.stopped = False

    def run(self) -> None:
        while not self.stopped:
            try:
                if _store is not None and Store is not None:
                    due = len(_store().due())
                    if due:
                        log("票池有 %d 张待铸，开始刷新…", due)
                        cmd = [sys.executable, "-X", "utf8", "token_pool.py",
                               "--warm", "--proxies", self.proxies,
                               "--workers", str(self.workers)]
                        if self.headless:
                            cmd.append("--headless")     # 默认不弹窗，避免抢焦点/被误认成队列窗口
                        subprocess.run(cmd,
                                       cwd=str(BASE), stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                        log("刷新结束")
            except Exception as exc:
                log("刷新异常（忽略）：%s", exc)
            time.sleep(self.interval)

    def stop(self) -> None:
        self.stopped = True


def ingest_sessions(since: float, done: set[str]) -> list[str]:
    """扩展每放行一次就写一个 session_*.json，这里把它收进票池。"""
    if _store is None:
        return []
    got = []
    st = _store()
    for p in sorted(RUNS.glob("session_*.json")):
        if p.name in done or p.stat().st_mtime < since:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            done.add(p.name)
            continue
        stage = d.get("stage") or ""
        ck = d.get("cookies") or []
        if not ck:
            done.add(p.name)
            continue
        inst = d.get("inst") or "x"
        ip_id = f"{inst}-{stage or 's'}"
        form = stage in ("form", "confirm", "otp", "calendar")
        if st.get(ip_id) is None:
            st.upsert(ip_id, "")
        st.save_ticket(ip_id, ck, d.get("ua") or "", form=form,
                       egress_ip="")
        done.add(p.name)
        got.append(f"{ip_id}({len(ck)} cookie)")
        if J is not None:
            try:
                J().log("race", "ingest", ip_id=ip_id, stage=stage, n=len(ck),
                        file=p.name, form=form)
            except Exception:
                pass
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insts", default="w1,w2,w3,w4")
    ap.add_argument("--stagger", type=float, default=30.0)
    ap.add_argument("--until", default="18:00", help="跑到几点（HH:MM，默认 18:00 等回流）")
    ap.add_argument("--plan", action="store_true", help="只打印当天时间表")
    ap.add_argument("--dry-run", action="store_true", help="只体检+打印计划，不起浏览器不刷票")
    ap.add_argument("--proxies", default="proxies.txt")
    ap.add_argument("--refresh", type=float, default=120.0)
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--refresh-headful", action="store_true",
                    help="刷新票时开可见浏览器（headless 过不了挑战时用，但会抢焦点）")
    ap.add_argument("--no-browser", action="store_true", help="只盯 ingest 与票池")
    a = ap.parse_args()

    if a.plan:
        print("当天时间表：")
        for t, what in PLAN:
            print(f"  {t:<12} {what}")
        return 0

    insts = [x.strip() for x in a.insts.split(",") if x.strip()]
    now = datetime.now()
    hh, mm = (int(x) for x in a.until.split(":"))
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)

    log("=== 17:00 总控启动：%s，窗口 %s，间隔 %.0fs，截止 %s ===",
        ",".join(insts), "浏览器" if not a.no_browser else "仅票池",
        a.stagger, deadline.strftime("%H:%M"))

    if a.dry_run:
        print("当天时间表：")
        for t, what in PLAN:
            print(f"  {t:<12} {what}")
        ok_srv = False
        try:
            from curl_cffi import requests as _cr
            ok_srv = _cr.get("http://127.0.0.1:8765/ping", timeout=5).json().get("ok", False)
        except Exception:
            ok_srv = False
        print(f"\n本地服务 8765：{'在线' if ok_srv else '离线（先 python ext_server.py）'}")
        pl = Path(a.proxies)
        n = len([ln for ln in pl.read_text(encoding="utf-8").splitlines() if ln.strip()]) if pl.exists() else 0
        print(f"代理清单 {a.proxies}：{n} 条")
        if _store is not None:
            try:
                st = _store().all()
                warm = sum(1 for x in st if x["state"] in ("warm", "admitted"))
                print(f"票池：{warm}/{len(st)} 张可用")
            except Exception:
                pass
        print("\n（dry-run：没有启动浏览器、没有刷票）")
        return 0

    start_mtime = time.time()
    done: set[str] = set()
    # 历史 session 文件不重复收
    for p in RUNS.glob("session_*.json"):
        done.add(p.name)

    if not a.no_browser:
        Launcher(insts, a.stagger).start()
    if not a.no_refresh:
        Refresher(a.proxies, a.refresh, headless=not a.refresh_headful).start()

    last_summary = 0.0
    while datetime.now() < deadline:
        rows = read_status()
        stages = last_stage_per_inst(rows)
        got = ingest_sessions(start_mtime, done)
        for g in got:
            log("★ 自动入库：%s", g)
        if time.time() - last_summary > 20:
            last_summary = time.time()
            brief = " ".join(f"{i}:{stages.get(i, ('-',))[0]}" for i in insts)
            pool = ""
            if _store is not None:
                try:
                    allslots = _store().all()
                    warm = sum(1 for s in allslots if s["state"] in ("warm", "admitted"))
                    pool = f" | 票池 {warm}/{len(allslots)} 张可用"
                except Exception:
                    pass
            log("状态 %s%s", brief, pool)
        time.sleep(4)

    log("到点结束。票池：")
    if _store is not None:
        try:
            print(_store().stats())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
