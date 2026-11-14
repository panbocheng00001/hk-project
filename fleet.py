#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fleet.py —— 10 个代理 IP 的作战编排：谁排队、谁探号、谁跑业务
===========================================================================
设计原则（来自今天的实测）
--------------------------
1. **排队是随机的**（17:00 洗牌），多一条 ticket 就多一分概率 → N 个 IP 全部排队。
2. **放行票不绑出口 IP**（实测 3 个不同出口用同一套 cookie 都 200）→ 谁放行，谁就能立刻
   进业务；其余 IP 可以拿同一套 cookie 帮忙探号。
3. **会话有 20 分钟硬窗口** → 放行那一刻起算，业务必须一口气跑完，中间不能停。
4. **判号只要 0.9 秒**（代理），所以"探号"这件事可以并行、高频做。

角色分配（默认 10 个 IP）
------------------------
  IP-01 … IP-08  排 队 组：各自 enqueue + 轮询 status，等自己那条被放行
  IP-09          探 号 组：不排队，等 winner 出现后立刻用 winner 的 cookie 高频查名额概览
  IP-10          兜 底 组：保留；winner 掉线/被封时顶上（换票重排或接手业务）

  第一个 released 的 worker → **winner**：
      enter（进业务页）→ step1（判号）→ 有号 → 抓第 2 步页面 → 落盘并打印交接
  其余排队 worker → 全部转 WATCHER：用 winner cookie 反复查 booking_status，
      把「哪个办事处 / 哪天 / 上午下午有空」第一时间报出来

用法
----
    # 正式：16:29 拿到链接后直接喂进去，16:30 整点自动开排
    python fleet.py --link "http://v2.api.juliangip.com/company/dynamic/getips?..." \
                    --start-at 16:30:00 --deadline 17:40 --workers 10

    # 先演练（用本机直连 + 现成代理，不等 16:30）
    python fleet.py --dry-run --proxies-file runs/rev/proxy_good.json --workers 3

    # 只看分配方案不打网络
    python fleet.py --plan --workers 10
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sniper import Sniper, Log, QUEUE_ENTRY  # noqa: E402

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs" / "fleet"
RUNS.mkdir(parents=True, exist_ok=True)

API_TMPL = ("http://v2.api.juliangip.com/company/dynamic/getips?auto_white=1"
            "&num={num}&pt=1&result_type=text&split=1"
            "&trade_no=2977409725466488&sign=d2210aad5b296a631c2a4181b520da8c")

_lock = threading.Lock()
_note_lock = threading.Lock()


def note(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with _note_lock:
        with open(RUNS / "fleet.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def fetch_proxies(link: str, num: int) -> list[str]:
    """一次提完（sign 是按 num 签的，别改 num）。"""
    url = link or API_TMPL.format(num=num)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(url, timeout=25) as r:
            txt = r.read().decode("utf-8", "replace").strip()
    except Exception as e:
        note(f"! 提取失败：{type(e).__name__} {e}")
        return []
    if txt.startswith("{"):
        note(f"! API 返回错误：{txt[:160]}")
        return []
    ips = [x.strip() for x in txt.replace("\r", "").split("\n") if ":" in x]
    note(f"提取到 {len(ips)} 个代理")
    return [f"http://{x}" for x in ips]


def dump_cookies(sp: Sniper) -> list[dict]:
    out: list[dict] = []
    try:
        for c in sp.s.cookies.jar:
            out.append({"name": c.name, "value": c.value,
                        "domain": c.domain, "path": c.path or "/"})
    except Exception:
        try:
            for k, v in sp.s.cookies.get_dict().items():
                out.append({"name": k, "value": v, "domain": "", "path": "/"})
        except Exception:
            pass
    return out


def load_cookies(sp: Sniper, cookies: list[dict]) -> int:
    n = 0
    for c in cookies:
        try:
            sp.s.cookies.set(name=c["name"], value=c["value"],
                             domain=c.get("domain") or "", path=c.get("path") or "/")
            n += 1
        except Exception:
            pass
    return n


@dataclass
class Shared:
    customer: str = ""
    event: str = ""
    target: str = ""
    ticket_template: str = ""
    winner: str = ""                      # worker 名
    winner_proxy: str = ""
    winner_cookies: list[dict] = field(default_factory=list)
    winner_queue_id: str = ""
    released_at: float = 0.0
    stop_poll: bool = False
    business_done: bool = False
    captcha_blocked: bool = False
    results: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"customer": self.customer, "event": self.event,
                "target": self.target, "winner": self.winner,
                "winner_proxy": self.winner_proxy,
                "winner_queue_id": self.winner_queue_id,
                "released_at": self.released_at,
                "business_done": self.business_done,
                "captcha_blocked": self.captcha_blocked,
                "cookies_n": len(self.winner_cookies),
                "results": self.results}


class Worker(threading.Thread):
    def __init__(self, idx: int, role: str, proxy: str, shared: Shared,
                 start_at: float, deadline: float, interval: float,
                 app: dict, dry: bool = False, auto_rescue: bool = True,
                 rescue_timeout: float = 240.0):
        super().__init__(daemon=True)
        self.name = f"IP-{idx:02d}"
        self.idx = idx
        self.role = role
        self.proxy = proxy
        self.shared = shared
        self.start_at = start_at
        self.deadline = deadline
        self.interval = interval
        self.app = app
        self.dry = dry
        self.auto_rescue = auto_rescue
        self.rescue_timeout = rescue_timeout
        self.sp: Sniper | None = None
        self.ticket = ""
        self.state = "init"

    # ---------------------------------------------------------------- 排队
    def run(self) -> None:
        try:
            if self.role == "reserve":
                self._reserve()
            elif self.role == "probe":
                self._probe_only()
            elif self.role == "verify":
                self._verify_only()
            else:
                self._queue()
        except Exception as e:
            note(f"[{self.name}] 异常退出：{type(e).__name__}: {str(e)[:140]}")

    def _sniper(self) -> Sniper:
        if self.sp is None:
            self.sp = Sniper(proxy=self.proxy, log=Log(f"fleet-{self.name}", file=False),
                             auto_rescue=self.auto_rescue,
                             rescue_timeout=self.rescue_timeout)
        return self.sp

    def _queue(self) -> None:
        sp = self._sniper()
        # 等到开闸时刻（16:30）
        while time.time() < self.start_at:
            time.sleep(min(2.0, max(0.2, self.start_at - time.time())))
        # 抖动 0~2.5s：10 个 worker 同时 TLS 握手会被 reset（实测并发同源会失败）
        time.sleep(random.uniform(0, 2.5))
        code = 0
        for attempt in range(1, 4):
            code = sp.waf_land()
            if code == 0:
                break
            note(f"[{self.name}] WAF 层失败 {code}（第 {attempt}/3 次），等 2s 重试")
            time.sleep(2)
        if code:
            note(f"[{self.name}] WAF 层连续失败，退出排队")
            return
        self.state = "enqueued"
        self.ticket = self.shared.ticket_template
        sp.ctx.customer = self.shared.customer
        sp.ctx.event = self.shared.event
        sp.ctx.target = self.shared.target
        c2 = sp.enqueue()
        if c2:
            note(f"[{self.name}] enqueue 失败 {c2}")
            return
        note(f"[{self.name}] 排队中 queueId={sp.ctx.queue_id}")
        self.state = "polling"

        n = 0
        while time.time() < self.deadline and not self.shared.stop_poll:
            n += 1
            code, st = sp.status(n)
            if code:
                time.sleep(min(self.interval * 3, 20))
                continue
            with _lock:
                if self.shared.released_at:
                    return
            if st.released:
                with _lock:
                    if not self.shared.released_at:
                        self.shared.winner = self.name
                        self.shared.winner_proxy = self.proxy
                        self.shared.winner_queue_id = sp.ctx.queue_id
                        self.shared.winner_cookies = dump_cookies(sp)
                        self.shared.released_at = time.time()
                        self.shared.stop_poll = True
                note(f"[{self.name}] ★ 放行！redirectUrl={st.redirect_url} "
                     f"page={st.page_class}（第 {n} 次）")
                self._business(sp)
                return
            if n % 20 == 0:
                note(f"[{self.name}] #{n:03d} state={st.queue_state} "
                     f"forecast={st.forecast} eta={st.expected_utc}")
            time.sleep(self._adaptive_interval())

    def _adaptive_interval(self) -> float:
        """越接近洗牌时刻越密：17:00 那一分钟不能错过。"""
        target = self.start_at + 30 * 60          # 16:30 开排 → 17:00 洗牌
        remain = target - time.time()
        if remain < 60:
            return max(1.0, self.interval / 3)
        if remain < 300:
            return max(1.5, self.interval / 2)
        return self.interval + random.uniform(0, self.interval * 0.3)

    # ---------------------------------------------------------------- 业务
    def _business(self, sp: Sniper) -> None:
        note(f"[{self.name}] 进入业务流：enter → step1 → booking")
        c = sp.enter()
        if c:
            note(f"[{self.name}] enter 失败 {c}")
            self.shared.results[self.name] = {"enter": c}
            return
        code, body = sp.step1(app=self.app)
        self.shared.results[self.name] = {"step1": code}
        note(f"[{self.name}] step1 → {code}")
        if code == 44:
            # 撞到验证码：立刻停手，不重试（越试越死），并留下给人看的说明
            self.shared.captcha_blocked = True
            self.shared.results[self.name] = {"step1": 44, "captcha": True}
            (RUNS / "ALERT.md").write_text(
                f"# 需要人工过验证码 {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"- worker：`{self.name}`（proxy `{self.proxy}`）\n"
                f"- 阶段：step1（判号）\n"
                f"- 证据：`runs/sniper/captcha-step1-post-*.html`\n\n"
                f"## 三条路，按顺序试\n"
                f"1. 把 `runs/fleet/winner_cookies.json` 灌进浏览器 → 人过验证 → 再导回来\n"
                f"2. MTCaptcha 打码平台（sitekey + pageurl 换 token）\n"
                f"3. 第 1 步 token 实测非强制：先空着提交一次看看能不能过\n",
                encoding="utf-8")
            note(f"[{self.name}] !! 验证码拦截，已停手；见 runs/fleet/ALERT.md")
            return
        (RUNS / f"step1_{self.name}.html").write_text(body or "", encoding="utf-8")
        st = sp.booking_status()
        if st.get("error") == 44:
            self.shared.captcha_blocked = True
            note(f"[{self.name}] !! 名额概览页也撞到验证码（{st.get('captcha')}）")
        self.shared.results[self.name] = {"step1": code,
                                          "booking": {k: v for k, v in st.items()
                                                      if k != "free"},
                                          "free": st.get("free") or []}
        note(f"[{self.name}] 名额概览：有号 {len(st.get('free') or [])} 处")
        if code == 0:
            note(f"[{self.name}] ★★ 有名额！第 2 步页面已落盘，立刻抓字段")
            (RUNS / f"step2_{self.name}.html").write_text(body, encoding="utf-8")
            note(f"[{self.name}] 交接：python replay.py --send --skip-final "
                 f"（cookie 见 winner_cookies.json）")
        self.shared.business_done = True
        (RUNS / "winner_cookies.json").write_text(
            json.dumps(self.shared.winner_cookies, ensure_ascii=False, indent=1),
            encoding="utf-8")

    # ---------------------------------------------------------------- 探号
    def _probe_only(self) -> None:
        """不排队，等 winner 出现后用它的 cookie 高频查名额概览。"""
        while not self.shared.released_at and time.time() < self.deadline:
            time.sleep(1.5)
        if not self.shared.released_at:
            return
        sp = self._sniper()
        n = load_cookies(sp, self.shared.winner_cookies)
        note(f"[{self.name}] 探号启动（灌 winner cookie {n} 条）")
        rounds = 0
        while not self.shared.business_done and time.time() < self.deadline:
            rounds += 1
            st = sp.booking_status()
            free = st.get("free") or []
            if free:
                note(f"[{self.name}] ★ 探到 {len(free)} 个空档：{free[:3]}")
                (RUNS / f"probe_free_{rounds}.json").write_text(
                    json.dumps(free, ensure_ascii=False, indent=1), encoding="utf-8")
            if rounds % 5 == 0:
                note(f"[{self.name}] 探号 #{rounds} 无空档（页面时间 {st.get('updated','-')}）")
            time.sleep(4)

    def _verify_only(self) -> None:
        """验证机：按轮跑 verify_flow 的 S1~S7，每轮落一份报告。

        它不参与抢号（不会变成 winner），只回答一个问题：**脚本现在还行不行**。
        winner 出现后它会自动带上 winner 的 cookie，把 S5~S7 也验进去。
        """
        import verify_flow
        rounds = 0
        while time.time() < self.deadline and not self.shared.business_done:
            rounds += 1
            ck = ""
            if self.shared.released_at and self.shared.winner_cookies:
                p = RUNS / "winner_cookies.json"
                p.write_text(json.dumps(self.shared.winner_cookies,
                                        ensure_ascii=False, indent=1), encoding="utf-8")
                ck = str(p)
            try:
                code, rows = verify_flow.run_once(self.proxy, ck, self.app)
            except Exception as e:
                note(f"[{self.name}] 验证异常：{type(e).__name__}: {str(e)[:120]}")
                time.sleep(30)
                continue
            out = {"round": rounds, "at": time.strftime("%H:%M:%S"),
                   "proxy": self.proxy, "cookies": bool(ck),
                   "exit": code, "rows": rows}
            (RUNS / f"verify_{self.name}_{rounds:02d}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            bad = [r for r in rows if r["status"] == "FAIL"]
            note(f"[{self.name}] 验证第 {rounds} 轮 exit={code}"
                 + (f" ← {bad[0]['step']} 失败：{bad[0]['detail'][:80]}" if bad else " 全通"))
            time.sleep(60)

    def _reserve(self) -> None:
        while not self.shared.business_done and time.time() < self.deadline:
            time.sleep(3)


def plan(workers: int) -> list[tuple[int, str]]:
    """角色分配：末尾留三台——探号 / 验证机 / 兜底。

    验证机（verify）跑的是 verify_flow.run_once()：S1~S7 逐步骤断言，
    每轮落一份报告。它的作用是「脚本自己有没有退化」，不参与抢号。
    """
    rows = []
    for i in range(1, workers + 1):
        if i <= workers - 3:
            rows.append((i, "queue"))
        elif i == workers - 2:
            rows.append((i, "probe"))
        elif i == workers - 1:
            rows.append((i, "verify"))
        else:
            rows.append((i, "reserve"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="10 IP 作战编排：排队 / 探号 / 业务")
    ap.add_argument("--link", default="", help="提代理链接（原样粘贴，别改 num）")
    ap.add_argument("--proxies-file", default="", help="已有代理清单（JSON 数组或每行一个）")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--start-at", default="16:30:00", help="开排时刻 HH:MM:SS")
    ap.add_argument("--deadline", default="17:40", help="整体截止 HH:MM（跨天不管）")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--worker-id", default="w1")
    ap.add_argument("--plan", action="store_true", help="只打印分配方案")
    ap.add_argument("--dry-run", action="store_true", help="不等开排时刻，立刻跑")
    ap.add_argument("--no-rescue", action="store_true",
                    help="撞到验证码时不自动开浏览器（默认会自动开，等你拖滑块）")
    ap.add_argument("--rescue-timeout", type=float, default=240.0)
    a = ap.parse_args()

    rows = plan(a.workers)
    if a.plan:
        print(f"共 {a.workers} 个 IP，角色分配：")
        for i, role in rows:
            desc = {"queue": "排队等放行（第一名转业务）",
                    "probe": "探号组：放行后用 winner cookie 高频查名额",
                    "verify": "验证机：按轮跑 S1~S7 自检，不抢号，专验脚本",
                    "reserve": "兜底组：保留，winner 掉线时顶上"}[role]
            print(f"  IP-{i:02d}  {role:8} {desc}")
        return 0

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    app = next((x for x in cfg["applicants"] if x.get("id") == a.worker_id),
               cfg["applicants"][0])

    # 代理
    if a.proxies_file:
        p = Path(a.proxies_file)
        raw = p.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw)
            prox = data if isinstance(data, list) else [raw.strip()]
        except Exception:
            prox = [x.strip() for x in raw.splitlines() if x.strip()]
        prox = [x if (not x or x.startswith("http")) else f"http://{x}" for x in prox]
    else:
        prox = fetch_proxies(a.link, a.workers)
    if not prox:
        note("没有可用代理，退出")
        return 20
    while len(prox) < a.workers:
        prox.append("")            # 不够就用直连补位
    prox = prox[:a.workers]

    # 时间
    now = datetime.now()
    def at(hms: str) -> float:
        h, m, s = (hms.split(":") + ["0", "0"])[:3]
        return now.replace(hour=int(h), minute=int(m), second=int(s),
                           microsecond=0).timestamp()
    start_at = time.time() if a.dry_run else at(a.start_at)
    if a.dry_run:
        deadline = time.time() + 90
    else:
        dl = a.deadline if a.deadline.count(":") == 2 else a.deadline + ":00"
        deadline = at(dl)

    shared = Shared()
    # 主线程先做一次 discover，拿到 c/e/t 广播给所有 worker
    boot = Sniper(proxy=prox[0], log=Log("fleet-boot", file=False))
    code = boot.waf_land()
    if code == 0:
        code = boot.discover()
    if code:
        note(f"discover 失败 {code}，退出")
        return code
    shared.customer = boot.ctx.customer
    shared.event = boot.ctx.event
    shared.target = boot.ctx.target
    note(f"event={shared.event} customer={shared.customer}")
    note(f"开排时刻 {datetime.fromtimestamp(start_at).strftime('%H:%M:%S')}  "
         f"截止 {datetime.fromtimestamp(deadline).strftime('%H:%M:%S')}")


    threads = []
    for i, role in rows:
        w = Worker(i, role, prox[i - 1], shared, start_at, deadline,
                   a.interval, app, dry=a.dry_run)
        threads.append(w)
    note("角色分配：" + ", ".join(f"IP-{i:02d}={r}" for i, r in rows))
    for t in threads:
        t.start()

    # 主循环：盯 winner，落 state
    last_dump = 0.0
    while any(t.is_alive() for t in threads):
        time.sleep(1)
        if shared.released_at and time.time() - last_dump > 3:
            last_dump = time.time()
            (RUNS / "state.json").write_text(
                json.dumps(shared.as_json(), ensure_ascii=False, indent=1),
                encoding="utf-8")
        if shared.business_done:
            break
    (RUNS / "state.json").write_text(
        json.dumps(shared.as_json(), ensure_ascii=False, indent=1), encoding="utf-8")

    note("=" * 60)
    if shared.winner:
        note(f"winner = {shared.winner}  proxy={shared.winner_proxy}")
        note(f"cookie 已存 runs/fleet/winner_cookies.json（{len(shared.winner_cookies)} 条）")
        note(f"结果：{json.dumps(shared.results, ensure_ascii=False)[:400]}")
    else:
        note("没有任何 IP 被放行（event 未开闸或队列不动）")
    return 0 if shared.business_done else (42 if not shared.winner else 0)


if __name__ == "__main__":
    sys.exit(main())
