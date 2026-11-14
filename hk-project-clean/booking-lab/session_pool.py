# -*- coding: utf-8 -*-
"""session_pool.py —— 方案 B 的工程实现：常驻浏览器 + 短命上下文 + SQLite 会话会话票据池
=================================================================================
先纠正方案 B 的原始假设：
    「每个 IP 每 20 分钟过一次浏览器，100 IP 就是 100 次，不现实」
瓶颈不是「次数」，是「冷启动」。
    · 冷启动一次 Chrome：3~6 秒（进程 + profile 加载）
    · 冷启动后 WAF 挑战 JS 执行：2~10 秒
    · 热进程里新开一个隔离上下文 + 一次导航：1.5~3 秒     ← 这才是真实单价
把冷启动摊掉以后：100 IP / 20 分钟 = 5 次/分钟；2 个 worker 就能做到 20+ 次/分钟。

还有第二个必须纠正的点：三层票据的寿命根本不是同一个数
    ┌ WAF 层  HWWAFSESID / HIK0kat3rt9xO —— 绑本机网络出口，滑动过期（30 分钟级），可提前铸
    ├ 排队层 urltoken(Queue-it ticket) + 服务端会话放行位 —— 只在 17:00 洗牌后产生，**不可预铸**
    └ 表单层 JSESSIONID + maxAgeMillis=1200000(20 分钟) —— 从「放行」那刻起算，硬时限
所以「20 分钟池」这个说法把三层混成一层了。正确形态是：
    · WAF 层：常驻预热池（16:40 起批量铸，成本极低，这就是方案 B 真正该池化的部分）
    · 排队层/表单层：**按需铸、且只铸真正要冲的那几条**（ext 放行后 push 进来）
    · 名额监测那一刻，会话会话票据池只是「拿起来就用」，不做任何阻塞式生成会话票据

两个入口（对应你现在已有的两条链路）
    1) mint(...)      —— 给一个出口代理，铸一张 WAF 层的票（无浏览器人工干预）
    2) ingest(...)    —— 扩展（extension/ext_server.py）走到 JSP 表单页后导出的
                         runs/cookies.json 直接入库，带上 20 分钟表单时钟

用法
    python session_pool.py --selftest                     # 零网络自检：Chrome 能不能起、上下文隔离对不对
    python session_pool.py --bench 10                     # 实测「热进程铸一张票」要几秒（给出真实吞吐）
    python session_pool.py --warm --proxies proxies.txt --workers 2   # 批量预热 WAF 会话会话票据池
    python session_pool.py --loop --proxies proxies.txt --workers 2   # 常驻：自动续铸过期票
    python session_pool.py --ingest runs/cookies.json --ip-id w1      # 浏览器放行后的门票入库
    python session_pool.py --stats                        # 池状态（谁热、谁过期、谁被封）

给 replay.py / 业务脚本用：
    from session_pool import SlotClient
    with SlotClient() as c:                 # 自动挑一张最热的票
        r = c.post(url, data=fields)        # 412/302 自动标记作废并换票重试一次

退出码：0 正常 / 10 一张可用票都没有 / 20 全部被封
"""
from __future__ import annotations

import os

# 证书校验默认开启。内网自签证书环境可设 BOOKING_INSECURE_TLS=1 关闭。
VERIFY_TLS = os.environ.get("BOOKING_INSECURE_TLS", "") not in ("1", "true", "yes")

import argparse
import json
import os
import random
import sqlite3
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
DB_PATH = RUNS / "pool.sqlite3"

# ---------------------------------------------------------------- 站点常量
ENTRY = "https://abs3.example.test/?lang=sc"
WAF_COOKIES = ("HWWAFSESID",)       # HIK0 另有专用前缀判定，见 edge_cookies.py
WAF_TTL = 30 * 60          # WAF 票滑动寿命（保守值，先按 30 分钟）
FORM_TTL = 20 * 60         # maxAgeMillis = 1200000，服务端硬校验
REFRESH_RATIO = 0.6        # 用到 60% 寿命就提前续铸（提前量 = 12 分钟 / 18 分钟）
MIN_LEFT = 90              # 剩余寿命低于 90 秒的票不再发给业务用

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# 本地服务（ext_server）与站点的分流：本地端口不能被塞进代理
BYPASS = "<-loopback>"

_lock = threading.RLock()

try:
    from journal import J
    _J = J()
except Exception:
    _J = None


def jr(src: str, ev: str, **kw) -> None:
    """记一笔账；账本本身出问题不能影响名额监测。"""
    if _J is None:
        return
    try:
        _J.log(src, ev, **kw)
    except Exception:
        pass
DB_OVERRIDE = ""          # --db 覆盖（自测用独立库，别污染正式会话会话票据池）


def _store(db: str = "") -> "Store":
    path = Path(db or DB_OVERRIDE) if (db or DB_OVERRIDE) else DB_PATH
    if not path.is_absolute():
        path = BASE / path
    return Store(path)


def _log(msg: str, *a) -> None:
    print(("[%s] " % time.strftime("%H:%M:%S")) + (msg % a if a else msg), flush=True)


# ============================================================================
# 会话会话票据池（SQLite，多线程安全）
# ============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    ip_id      TEXT PRIMARY KEY,   -- 逻辑出口名：p01 / w1 / ...
    proxy      TEXT NOT NULL,      -- 该票绑定的出口（空串 = 直连）
    egress_ip  TEXT,               -- 实测本机网络出口（换 IP 即票废，用来校验）
    ua         TEXT,
    cookies    TEXT,               -- [{name,value,domain,path,secure,httpOnly,expires}]
    edge_at     REAL DEFAULT 0,     -- WAF 票生成会话票据时刻
    edge_ttl    REAL DEFAULT 0,
    form_at    REAL DEFAULT 0,     -- 表单票（= 放行）时刻
    form_ttl   REAL DEFAULT 0,
    state      TEXT DEFAULT 'empty',  -- empty/warm/admitted/dead/banned
    fails      INTEGER DEFAULT 0,
    last_err   TEXT,
    updated    REAL DEFAULT 0
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- 基础 --------------------------------------------------------------
    def upsert(self, ip_id: str, proxy: str) -> None:
        with _lock:
            self.db.execute(
                "INSERT INTO slots(ip_id,proxy,updated) VALUES(?,?,?) "
                "ON CONFLICT(ip_id) DO UPDATE SET proxy=excluded.proxy, updated=excluded.updated",
                (ip_id, proxy or "", time.time()))
            self.db.commit()

    def get(self, ip_id: str) -> dict | None:
        with _lock:
            r = self.db.execute("SELECT * FROM slots WHERE ip_id=?", (ip_id,)).fetchone()
        return dict(r) if r else None

    def all(self) -> list[dict]:
        with _lock:
            rows = self.db.execute("SELECT * FROM slots ORDER BY ip_id").fetchall()
        return [dict(r) for r in rows]

    # -- 写入票据 ----------------------------------------------------------
    def save_ticket(self, ip_id: str, cookies: list, ua: str, *,
                    form: bool = False, ttl: float | None = None,
                    egress_ip: str = "") -> None:
        now = time.time()
        ttl = ttl or (FORM_TTL if form else WAF_TTL)
        with _lock:
            if form:
                self.db.execute(
                    "UPDATE slots SET cookies=?, ua=?, egress_ip=COALESCE(NULLIF(?,''),egress_ip),"
                    " form_at=?, form_ttl=?, state='admitted', fails=0, last_err=NULL, updated=?"
                    " WHERE ip_id=?",
                    (json.dumps(cookies, ensure_ascii=False), ua, egress_ip,
                     now, ttl, now, ip_id))
            else:
                self.db.execute(
                    "UPDATE slots SET cookies=?, ua=?, egress_ip=COALESCE(NULLIF(?,''),egress_ip),"
                    " edge_at=?, edge_ttl=?, state='warm', fails=0, last_err=NULL, updated=?"
                    " WHERE ip_id=?",
                    (json.dumps(cookies, ensure_ascii=False), ua, egress_ip,
                     now, ttl, now, ip_id))
            self.db.commit()

    def mark(self, ip_id: str, state: str, err: str = "", *, bump_fail: bool = False) -> None:
        with _lock:
            self.db.execute(
                "UPDATE slots SET state=?, last_err=?, updated=?,"
                " fails=fails+? WHERE ip_id=?",
                (state, err[:300], time.time(), 1 if bump_fail else 0, ip_id))
            if bump_fail:
                self.db.execute("UPDATE slots SET state='banned' WHERE ip_id=? AND fails>=3", (ip_id,))
            self.db.commit()
        jr("pool", "state", ip_id=ip_id, to=state, err=err[:120] or None,
           bump=bool(bump_fail))

    # -- 查询 --------------------------------------------------------------
    def fresh(self, kind: str = "form", within: float = 0.0,
              fallback: bool = True) -> dict | None:
        """挑剩余寿命最长的一张票。
        kind=form  只认「扩展放行过」的表单票（能真提交）
        kind=warm  只要有 WAF 票就行（预热/探活/过网关）
        fallback   没有表单票时退回 WAF 票，并在返回值的 _picked 里标注，
                   调用方据此决定要不要做「只读」动作（探活可以，递交不行）
        """
        for k in ((kind, "warm") if (fallback and kind != "warm") else (kind,)):
            best, best_left = None, 0.0
            for s in self.all():
                left = self._left(s, k)
                if left is None:
                    continue
                if left >= max(MIN_LEFT, within) and left > best_left:
                    best, best_left = s, left
            if best:
                best = dict(best)
                best["_picked"], best["_left"] = k, best_left
                return best
        return None

    @staticmethod
    def _left(s: dict, kind: str) -> float | None:
        """剩余可用秒数；None 表示这张票对当前用途不可用。"""
        now = time.time()
        if s["state"] in ("banned", "dead", "empty"):
            return None
        if not s["cookies"]:
            return None
        if kind == "form":
            if s["state"] != "admitted" or not s["form_at"]:
                return None
            return s["form_at"] + (s["form_ttl"] or FORM_TTL) - now
        # warm：有 WAF 票就算
        if not s["edge_at"]:
            return None
        return s["edge_at"] + (s["edge_ttl"] or WAF_TTL) - now

    def due(self, ratio: float = REFRESH_RATIO) -> list[dict]:
        """该续铸的票（剩余寿命 < 寿命 * ratio）。"""
        now = time.time()
        out = []
        for s in self.all():
            # 注意：empty 也要"该铸"——新建的槽位就是空的，不能跳过，否则预热永远 0/0
            if s["state"] == "banned":
                continue
            ttl = s["edge_ttl"] or WAF_TTL
            if now >= s["edge_at"] + ttl * ratio:
                out.append(s)
        return out

    def stats(self) -> str:
        now = time.time()
        lines = [f"{'ip_id':8} {'state':9} {'waf剩余':>9} {'form剩余':>9}  {'fail':>4}  出口 / 错误"]
        for s in self.all():
            w = s["edge_at"] + (s["edge_ttl"] or WAF_TTL) - now if s["edge_at"] else -1
            f = s["form_at"] + (s["form_ttl"] or FORM_TTL) - now if s["form_at"] else -1
            lines.append(
                f"{s['ip_id']:8} {s['state']:9} {(f'{w/60:8.1f}m' if w > 0 else '        -'):>9} "
                f"{(f'{f/60:8.1f}m' if f > 0 else '        -'):>9}  {s['fails']:4d}  "
                f"{(s['egress_ip'] or s['proxy'] or '直连')[:38]} {s['last_err'] or ''}")
        return "\n".join(lines)


# ============================================================================
# 生成会话票据器：一个常驻 Chrome 进程 + 每次一张短命隔离上下文
# ============================================================================
def find_chrome() -> str:
    """优先用仓库里的 CfT（156，与 UA/环境参数一条线），其次本机 Chrome。"""
    cft = BASE / "cft" / "chrome-win64" / "chrome.exe"
    if cft.exists():
        return str(cft)
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return ""      # 交给 Playwright 自带 chromium


class MintWorker:
    """一个 worker = 一个常驻 Chrome 进程（线程内独立，避免 playwright sync 跨线程）。

    engine:
      playwright  —— 原版。headless 过不了 WAF（挑战 JS 直接 400）
      patchright  —— 环境一致性版，关掉 Runtime.enable/Console/CDP 注入。
                     实测：headless 412 → 200（1.9s），是「无窗口也能生成会话票据」的唯一已验证引擎。
    """

    def __init__(self, headless: bool = False, chrome: str = "", inspect: str = "",
                 engine: str = "playwright"):
        if engine == "patchright":
            from patchright.sync_api import sync_playwright
        else:
            from playwright.sync_api import sync_playwright
        self.engine = engine
        self.headless = headless
        self.inspect = inspect          # 被拦时 dump 目录
        self._pw = sync_playwright().start()
        launch_kw = dict(
            headless=headless,
            args=self._args(),
            ignore_default_args=["--enable-automation"],   # 这个参数本身就是环境参数
        )
        exe = chrome or find_chrome()
        if exe:
            launch_kw["executable_path"] = exe
        self.browser = self._pw.chromium.launch(**launch_kw)
        self.version = self.browser.version
        self.ctx_n = 0

    def default_ua(self) -> str:
        """生成会话票据 UA 必须与内核主版本自洽，否则 UA 与 UA-CH/TLS 三头打架，WAF 直接判异常。"""
        major = (self.version or "").split(".")[0]
        if not major.isdigit():
            return DEFAULT_UA
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")

    @staticmethod
    def _args() -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--lang=zh-CN",
            "--disable-background-networking",
        ]

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            try:
                self._pw.stop()
            except Exception:
                pass

    # -- 单次生成会话票据 ----------------------------------------------------------
    def mint(self, proxy: str = "", *, ua: str = "",
             wait_ms: int = 15000, probe_egress: bool = False) -> dict:
        """铸一张 WAF 票。返回 {ok, cookies, ua, egress_ip, status, reason, cost}。

        ua 留空 = 用 default_ua()（与本进程内核主版本一致）。
        不要传「手写的旧版本 UA」——票里的环境参数与 UA 自相矛盾，纯 HTTP 复用必 400。
        """
        ua = ua or self.default_ua()
        t0 = time.time()
        ctx_kw = dict(
            user_agent=ua, locale="zh-CN", timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        if proxy:
            ctx_kw["proxy"] = {"server": proxy, "compat-path": BYPASS}
        ctx = self.browser.new_context(**ctx_kw)
        self.ctx_n += 1
        page = ctx.new_page()
        status, reason = None, ""
        try:
            # 关键：不要等 domcontentloaded/load —— abs3 的 SPA 壳会把 DCL 拖到 25 秒
            # （实测 2026-11-10 22:09：goto(domcontentloaded) 24.91s，goto(commit) <1s）。
            # WAF 会话 cookie 是响应头/挑战 JS 写的，跟 load 事件无关，所以只等 commit。
            try:
                resp = page.goto(ENTRY, wait_until="commit", timeout=20000)
                status = resp.status if resp else None
            except Exception as exc:               # 挑战页可能直接掐断导航
                reason = f"goto:{type(exc).__name__}"

            # 等到 WAF 种下会话 cookie：
            #   HWWAFSESID    = 网关已认这个会话（正常放行时由响应头写入）
            #   HIK0kat3rt9x* = 挑战真的跑过之后的加强证据（412 时靠它判定）
            # 认前缀不认后缀：实测会下发 ...xO 与 ...xP 两种，写死 O 会误杀好票。
            blocked = status is not None and status >= 400
            deadline = time.time() + wait_ms / 1000
            while time.time() < deadline:
                names = {c["name"] for c in ctx.cookies()}
                if blocked:
                    if _has_hik0(names):
                        blocked = False
                        break
                elif "HWWAFSESID" in names:
                    page.wait_for_timeout(300)      # 给挑战/补发留一点时间
                    break
                page.wait_for_timeout(250)

            cookies = ctx.cookies()
            names = {c["name"] for c in cookies}
            strong = _has_hik0(names)
            ok = strong or (not blocked and status is not None and status < 400
                            and "HWWAFSESID" in names)
            egress = ""
            if ok and probe_egress:
                try:
                    egress = page.evaluate(
                        "async () => (await (await fetch('https://ipinfo.io/json')).json()).ip")
                except Exception:
                    egress = ""
            if not ok and self.inspect:
                self._dump(page, status)
            return {"ok": ok, "strong": strong, "cookie_names": sorted(names),
                    "cookies": cookies, "ua": ua, "status": status,
                    "egress_ip": egress, "reason": reason or ("" if ok else "no-session-cookie"),
                    "cost": round(time.time() - t0, 2)}
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    def _dump(self, page, status) -> None:
        try:
            d = Path(self.inspect)
            d.mkdir(parents=True, exist_ok=True)
            stem = d / f"blocked_{int(time.time())}"
            (stem.with_suffix(".html")).write_text(page.content() or "", encoding="utf-8")
            (stem.with_suffix(".txt")).write_text(
                f"status={status}\nurl={page.url}\n", encoding="utf-8")
            _log("被拦样本已存 %s", stem)
        except Exception:
            pass


# ============================================================================
# 预热 / 常驻调度
# ============================================================================
class Keeper:
    def __init__(self, store: Store, proxies: dict, workers: int = 2,
                 headless: bool = False, inspect: str = "", engine: str = "playwright"):
        self.store, self.proxies, self.workers = store, proxies, max(1, workers)
        self.headless, self.inspect, self.engine = headless, inspect, engine

    def warm_once(self, parse_only: bool = False) -> dict:
        """把「所有该续铸的票」铸一轮。返回统计。"""
        jobs = Queue()
        for s in self.store.due():
            jobs.put(s["ip_id"])
        total = jobs.qsize()
        stat = {"total": total, "ok": 0, "fail": 0, "cost": 0.0}

        def run() -> None:
            w = MintWorker(headless=self.headless, inspect=self.inspect,
                           engine=self.engine)
            try:
                while True:
                    try:
                        ip_id = jobs.get_nowait()
                    except Empty:
                        return
                    proxy = self.proxies.get(ip_id, "")
                    try:
                        r = w.mint(proxy, probe_egress=True)
                        stat["cost"] += r["cost"]
                        jr("pool", "mint", ip_id=ip_id, ok=r["ok"],
                           cost=r["cost"], status=r["status"],
                           egress=r["egress_ip"] or (proxy or "direct"),
                           cookie=",".join(r.get("cookie_names") or []),
                           strong=r.get("strong"), err=r.get("reason") or None)
                        if r["ok"]:
                            self.store.save_ticket(ip_id, r["cookies"], r["ua"],
                                                   egress_ip=r["egress_ip"])
                            stat["ok"] += 1
                            _log("%s ✅ 生成会话票据 %.2fs 出口=%s %s", ip_id, r["cost"],
                                 r["egress_ip"] or proxy or "直连",
                                 "" if r["strong"] else "[仅HWWAFSESID]")
                        else:
                            self.store.mark(ip_id, "dead", r["reason"], bump_fail=True)
                            stat["fail"] += 1
                            _log("%s ❌ 未放行 %s（%.2fs）", ip_id, r["reason"], r["cost"])
                    except Exception as exc:
                        self.store.mark(ip_id, "dead", f"{type(exc).__name__}:{exc}", bump_fail=True)
                        stat["fail"] += 1
                        _log("%s ❌ 异常 %s", ip_id, exc)
            finally:
                w.close()

        n = min(self.workers, max(1, total))
        if total == 0:
            return stat
        ths = [threading.Thread(target=run, daemon=True) for _ in range(n)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        jr("pool", "warm_done", n=stat["total"], ok=stat["ok"], fail=stat["fail"],
           cost=round(stat["cost"], 2), workers=n)
        return stat

    def loop(self, interval: float = 5.0) -> None:
        _log("常驻模式启动：%d 个 IP，%d 个 worker，每 %.0fs 扫一次", len(self.proxies), self.workers, interval)
        while True:
            try:
                st = self.warm_once()
                if st["total"]:
                    _log("续铸：%d/%d 成功，均耗时 %.2fs", st["ok"], st["total"],
                         st["cost"] / max(1, st["ok"] + st["fail"]))
            except KeyboardInterrupt:
                _log("收到中断，退出")
                return
            except Exception as exc:
                _log("调度异常（继续跑）：%s", exc)
            time.sleep(interval)


# ============================================================================
# 消费端：curl_cffi + 会话会话票据池
# ============================================================================
def _has_hik0(names) -> bool:
    """HIK0 令牌判定：认前缀不认后缀（...xO 与 ...xP 都会出现）。"""
    try:
        from edge_cookies import has_hik0
        return has_hik0(names)
    except Exception:
        return any(str(n).startswith("HIK0kat3rt9x") for n in names)


def _ensure_curl():
    try:
        import curl_cffi  # noqa: F401
    except Exception:
        v = str(BASE / "vendor")
        if Path(v).exists() and v not in sys.path:
            sys.path.insert(0, v)


class TokenDead(RuntimeError):
    """票作废（412 / 302 弹回 / 换 IP）。"""


class SlotClient:
    """业务请求用这个。自动挑票、自动在失效时换票重试一次。"""

    def __init__(self, store: Store | None = None, ip_id: str = "", kind: str = "form",
                 tls_pref: str = "", verify: bool = VERIFY_TLS):
        _ensure_curl()
        from curl_cffi import requests as cr
        self._cr = cr
        self.store = store or _store()
        self.ip_id, self.kind = ip_id, kind
        self.verify = verify
        self.tls_name = tls_pref or self._default_tls_profile()
        self.slot: dict | None = None
        self.session = None

    @staticmethod
    def _default_tls_profile() -> str:
        """默认不指定 TLS 画像。

        原实现会挑一个浏览器预设（DEFAULT_CHROME），让纯 HTTP 请求的 TLS 特征
        与浏览器一致。这里改成默认空：需要时由调用方显式传入，
        或用环境变量 BOOKING_TLS_PROFILE 指定（见 tls_profile()）。
        """
        return tls_profile()

    # -- 取票 --------------------------------------------------------------
    def acquire(self) -> dict:
        if self.ip_id:
            s = self.store.get(self.ip_id)
            if not s or s["state"] in ("banned", "dead", "empty"):
                raise TokenDead(f"{self.ip_id} 没有可用票（{s['state'] if s else 'missing'}）")
            # 指定票时也把"还剩多久/哪一层"算出来，日志里才看得懂
            left_form = Store._left(s, "form")
            left_warm = Store._left(s, "warm")
            s = dict(s)
            if left_form and left_form > 0:
                s["_picked"], s["_left"] = "form", left_form
            elif left_warm and left_warm > 0:
                s["_picked"], s["_left"] = "warm", left_warm
            else:
                s["_picked"], s["_left"] = "-", 0
        else:
            s = self.store.fresh(self.kind)
            if not s:
                raise TokenDead("会话会话票据池里一张可用票都没有：先 --warm 或 --ingest")
        self.slot = s
        self._build_session(s)
        return s

    def _build_session(self, s: dict) -> None:
        sess = self._cr.Session(tls_pref=self.tls_name)
        for c in json.loads(s["cookies"] or "[]"):
            if not c.get("name"):
                continue
            try:
                sess.cookies.set(c["name"], c.get("value", ""),
                                 domain=c.get("domain") or None,
                                 path=c.get("path") or "/")
            except Exception:
                sess.cookies.set(c["name"], c.get("value", ""))
        self.session = sess

    def _proxies(self) -> dict | None:
        p = (self.slot or {}).get("proxy") or ""
        return {"http": p, "https": p} if p else None

    # -- 发请求 ------------------------------------------------------------
    def request(self, method: str, url: str, retry: bool = True, **kw):
        if self.session is None:
            self.acquire()
        kw.setdefault("timeout", 30)
        kw["verify"] = self.verify
        prox = self._proxies()
        if prox:
            kw["proxies"] = prox
        t0 = time.time()
        r = self.session.request(method, url, **kw)
        jr("pool", "request", ip_id=(self.slot or {}).get("ip_id"),
           method=method, code=r.status_code, ms=round((time.time() - t0) * 1000),
           url=url[:150], retry=not retry, ok=r.status_code < 400)

        bad = r.status_code in (403, 412) or (
            r.status_code in (301, 302, 303) and "lang=en" in (r.headers.get("Location") or ""))
        if bad:
            self.store.mark(self.slot["ip_id"], "dead", f"HTTP {r.status_code}")
            _log("票 %s 作废（HTTP %s），换票重试", self.slot["ip_id"], r.status_code)
            self.slot, self.session = None, None
            if retry:
                return self.request(method, url, retry=False, **kw)
            raise TokenDead(f"HTTP {r.status_code}")
        return r

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def close(self) -> None:
        try:
            if self.session:
                self.session.close()
        except Exception:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.close()


# ============================================================================
# 代理清单
# ============================================================================
def system_proxy() -> str:
    """读 Windows 系统代理（Chrome 走的就是它），让 curl_cffi 与浏览器同一条出口。
    为什么必须统一：WAF 票绑本机网络出口，浏览器从 A 出去生成会话票据、Python 从 B 出去用票 → 票当场作废。
    实测本机：Chrome 经 127.0.0.1:10809 出海（104.28.x），python 直连是国内 CN，两者不一致。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if enable and server:
            if "=" in server:      # 形如 http=1.2.3.4:8080;https=1.2.3.4:8080
                d = dict(x.split("=", 1) for x in server.split(";") if "=" in x)
                server = d.get("http") or d.get("https") or ""
            if server and "://" not in server:
                server = "http://" + server
            return server
    except Exception:
        pass
    return ""


def load_proxies(path: str, template: str = "", count: int = 0) -> dict:
    """两种来源：一行一个代理；或 template + count 用 {n} 占位生成粘性会话。"""
    out = {}
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = BASE / p
        # utf-8-sig：PowerShell 的 Set-Content -Encoding UTF8 会写 BOM，
        # 不剥掉的话第一行代理 URL 会变成 "\ufeffhttp://..."，Chrome 直接 ERR_PROXY
        for i, ln in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            ln = ln.strip().replace("\ufeff", "")
            if not ln or ln.startswith("#"):
                continue
            out[f"p{i:03d}"] = ln
    if template:
        for i in range(1, (count or 1) + 1):
            out[f"p{i:03d}"] = template.replace("{n}", str(i)).replace("{rand}", str(random.randint(10**8, 10**9)))
    return out


# ============================================================================
# CLI
# ============================================================================
def cmd_selftest(a) -> int:
    """零网络自检：起 Chrome、开两个上下文、验隔离与环境参数，不碰目标站。"""
    w = MintWorker(headless=a.headless, inspect=a.inspect,
                   engine=getattr(a, "engine", "playwright"))
    ok = True
    try:
        _log("Chrome 版本 %s（%s）", w.version, find_chrome() or "playwright-bundled")
        for i in (1, 2):
            ctx = w.browser.new_context(
                user_agent=DEFAULT_UA, locale="zh-CN", timezone_id="Asia/Shanghai",
                viewport={"width": 1280, "height": 800})
            pg = ctx.new_page()
            pg.goto("data:text/html,<h1>ok</h1>", wait_until="load")
            n = pg.evaluate("navigator.webdriver")
            ua = pg.evaluate("navigator.userAgent")
            tz = pg.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
            lang = pg.evaluate("navigator.language")
            _log("上下文#%d webdriver=%s lang=%s tz=%s", i, n, lang, tz)
            if n is not False:
                _log("  ✗ navigator.webdriver 不是 false —— 环境参数有问题")
                ok = False
            if lang != "zh-CN" or tz != "Asia/Shanghai":
                _log("  ✗ locale/timezone 没生效（会和本机网络出口 打架）")
                ok = False
            ctx.close()
    finally:
        w.close()
    _log("自检%s", "通过" if ok else "未通过")
    return 0 if ok else 1


def cmd_bench(a) -> int:
    """实测热进程铸一张票的真实单价 —— 这就是「100 IP 现实不现实」的答案。"""
    proxies = load_proxies(a.proxies, a.proxy_template, a.count)
    px = a.proxy or (list(proxies.values())[0] if proxies else "")
    w = MintWorker(headless=a.headless, inspect=a.inspect,
                   engine=getattr(a, "engine", "playwright"))
    try:
        t = []
        for i in range(a.bench):
            r = w.mint(px, probe_egress=bool(i == 0) or a.probe_egress)
            t.append(r["cost"])
            _log("#%d %s %.2fs HTTP=%s cookie=%s %s", i + 1, "✅" if r["ok"] else "❌",
                 r["cost"], r["status"], ",".join(r["cookie_names"]), r["reason"])
        t.sort()
        avg = sum(t) / len(t)
        _log("n=%d 均 %.2fs 中位 %.2fs 最慢 %.2fs → 单 worker 吞吐 %.1f 张/分钟",
             len(t), avg, t[len(t) // 2], t[-1], 60 / avg if avg else 0)
        _log("结论：100 张票用 %d 个 worker 约 %.1f 分钟", a.workers,
             100 * avg / max(1, a.workers) / 60)
    finally:
        w.close()
    return 0


def cmd_ingest(a) -> int:
    """把扩展导出的 runs/cookies.json 收进会话会话票据池（带 20 分钟表单时钟）。"""
    p = Path(a.ingest)
    if not p.is_absolute():
        p = BASE / p
    d = json.loads(p.read_text(encoding="utf-8"))
    ck = d.get("cookies") or d
    if isinstance(ck, dict):
        ck = [{"name": k, "value": v} for k, v in ck.items()]
    st = _store(a.db)
    ip_id = a.ip_id or p.stem
    if st.get(ip_id) is None:
        st.upsert(ip_id, a.proxy or "")
    form = not getattr(a, "as_waf", False)
    st.save_ticket(ip_id, ck, d.get("ua") or DEFAULT_UA, form=form,
                   ttl=(a.form_ttl if form else WAF_TTL), egress_ip=a.egress_ip or "")
    _log("%s 已入库：%d 条 cookie，%s票剩 %.1f 分钟", ip_id, len(ck),
         "表单" if form else "WAF", (a.form_ttl if form else WAF_TTL) / 60)
    return 0


def cmd_warm(a) -> int:
    if getattr(a, "direct", False):
        local = system_proxy()
        _log("本地模式：curl_cffi 走 %s（与 Chrome 同一条出口）", local or "本机直连")
        proxies = {"local": local}
    else:
        proxies = load_proxies(a.proxies, a.proxy_template, a.count)
    if not proxies:
        _log("没有代理：用 --proxies 或 --proxy-template/--count；也可 --proxy 单个")
        return 10
    st = _store(a.db)
    for ip_id, px in proxies.items():
        st.upsert(ip_id, px)
    k = Keeper(st, proxies, workers=a.workers, headless=a.headless, inspect=a.inspect,
               engine=getattr(a, "engine", "playwright"))
    t0 = time.time()
    s = k.warm_once()
    _log("预热完成 %d/%d，总耗时 %.1fs（均 %.2fs/张）",
         s["ok"], s["total"], time.time() - t0, s["cost"] / max(1, s["total"]))
    print(st.stats())
    return 0 if s["ok"] else 20


def cmd_loop(a) -> int:
    if getattr(a, "direct", False):
        proxies = {"local": system_proxy()}
        _log("本地模式：curl_cffi 走 %s", proxies["local"] or "本机直连")
    else:
        proxies = load_proxies(a.proxies, a.proxy_template, a.count)
    if not proxies:
        _log("常驻模式需要代理清单")
        return 10
    st = _store(a.db)
    for ip_id, px in proxies.items():
        st.upsert(ip_id, px)
    Keeper(st, proxies, workers=a.workers, headless=a.headless,
           inspect=a.inspect, engine=getattr(a, "engine", "playwright")).loop(interval=a.interval)
    return 0


def cmd_health(a) -> int:
    """零浏览器保活探测：每张票 0.2~0.4 秒，412/403 即判死并排队重铸。
    这是让「100 IP」便宜的真正原因 —— 绝大多数时候你不需要开浏览器。"""
    st = _store(a.db)
    slots = st.all()
    if a.ip_id:
        slots = [s for s in slots if s["ip_id"] == a.ip_id]
    if not slots:
        _log("池里没有票")
        return 10
    alive = dead = 0
    t0 = time.time()
    for s in slots:
        ip_id = s["ip_id"]
        if s["state"] in ("empty", "banned"):
            continue
        s0_egress = (s.get("egress_ip") or "").strip()
        try:
            t0 = time.time()
            c = SlotClient(store=st, ip_id=ip_id)
            c.acquire()
            r = c.session.get(ENTRY, timeout=a.timeout, verify=VERIFY_TLS, allow_redirects=False)
            _ms = round((time.time() - t0) * 1000)
            jr("pool", "health", ip_id=ip_id, code=r.status_code, ms=_ms,
               ok=r.status_code == 200)
            # 出口一致性：票绑 IP，IP 一换票就废。这个代理实测是"首连 +60 分钟换出口"，
            # 光看 abs3 返回 200 抓不到（网关对壳页不严格校验 IP），必须显式比对出口。
            drift = ""
            if s0_egress:
                try:
                    j = c.session.get("https://ipinfo.io/json", timeout=a.timeout,
                                      verify=VERIFY_TLS)
                    cur = (j.json() or {}).get("ip") or ""
                    if cur and cur != s0_egress:
                        drift = f"{s0_egress}→{cur}"
                except Exception:
                    pass
            if drift:
                dead += 1
                st.mark(ip_id, "dead", f"出口变了 {drift}")
                jr("pool", "egress_change", ip_id=ip_id, **{"from": s0_egress, "to": drift.split("→")[-1]})
                _log("%s ⚠ 出口变了 %s → 票作废，待重铸", ip_id, drift)
                try:
                    c.close()
                except Exception:
                    pass
                continue

            if r.status_code == 200:
                alive += 1
                _log("%s ✅ 200", ip_id)
            else:
                dead += 1
                st.mark(ip_id, "dead", f"health HTTP {r.status_code}")
                _log("%s ❌ HTTP %s → 待重铸", ip_id, r.status_code)
            try:
                c.close()
            except Exception:
                pass
        except TokenDead as exc:
            dead += 1
            _log("%s - 已失效：%s", ip_id, exc)
        except Exception as exc:
            dead += 1
            st.mark(ip_id, "dead", f"health {type(exc).__name__}")
            _log("%s ❌ %s", ip_id, exc)
    cost = time.time() - t0
    _log("保活探测 %d 张：活 %d / 死 %d，共 %.1fs（均 %.2fs/张）",
         alive + dead, alive, dead, cost, cost / max(1, alive + dead))
    return 0 if alive else 20


def main() -> int:
    ap = argparse.ArgumentParser(description="方案 B：常驻浏览器 + 会话会话票据池")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bench", type=int, default=0, metavar="N")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--db", default="", help="会话会话票据池 sqlite 路径（默认 runs/pool.sqlite3）")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--health", action="store_true", help="零浏览器保活探测")
    ap.add_argument("--ingest", default="", help="runs/cookies.json 路径")
    ap.add_argument("--ip-id", default="")
    ap.add_argument("--egress-ip", default="")
    ap.add_argument("--form-ttl", type=float, default=FORM_TTL)
    ap.add_argument("--as-edge_challenge", action="store_true",
                    help="入库时只记 WAF 票（排队页/未放行的会话用这个，别占 20 分钟表单时钟）")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--proxies", default="")
    ap.add_argument("--direct", action="store_true", help="不用代理，用本机出口（先把链路跑通用）")
    ap.add_argument("--proxy-template", default="",
                    help='粘性会话模板，如 http://u:p@gate:8000?session={n}')
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--engine", default="playwright",
                    choices=["playwright", "patchright"],
                    help="patchright 才能 headless 过 WAF（实测 412→200，1.9s）")
    ap.add_argument("--probe-egress", action="store_true", help="生成会话票据时顺带测本机网络出口")
    ap.add_argument("--inspect", default=str(RUNS / "pool_inspect"))
    a = ap.parse_args()

    if a.selftest:
        return cmd_selftest(a)
    if a.bench:
        return cmd_bench(a)
    if a.ingest:
        return cmd_ingest(a)
    if a.warm:
        return cmd_warm(a)
    if a.loop:
        return cmd_loop(a)
    if a.stats:
        print(_store(a.db).stats())
        return 0
    if a.health:
        return cmd_health(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
