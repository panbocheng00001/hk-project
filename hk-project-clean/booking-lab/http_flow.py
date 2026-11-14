#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
http_flow.py — BOOKING 换驾照预约：纯 HTTP 全流程（不开浏览器）
===========================================================================
链路（每一环都是 2026-11-11 实测过的）：

    ① GET  abs3/?lang=sc                     → HWWAFSESID / HWWAFSESTIME
    ② GET  abs3/SelectCountry?...            → 302 Location 里带 Queue-it 的 c/e/t
    ③ POST abs4/spa-api/queue/{c}/{e}/enqueue→ queueId + Queue-it-* cookie
    ④ POST .../{queueId}/status              → QueueState / forecastStatus / ETA（放行看 redirectUrl）
    ⑤ GET  {t}（http→https）                 → 业务页（需放行 cookie）
    ⑥ GET  abs1/.../AdminServlet?cmd=cmdIntoSelectAuthDI
       POST .../AdminServlet_<lang>?cmd=cmdGetOfficeQuotaDIAction
                                             → 服务端直接回答「有没有名额」+ TrackId
    ⑦ GET  ...?cmd=cmdGetOfficeBookingStatusAction
                                             → 各办事处 × 日期 × 上午/下午 的剩余名额

用法
----
    # 体检：WAF → 302 → enqueue → status×3（约 6 秒）
    python http_flow.py --probe

    # 一条 IP 跑完整条（推荐先这么验）：进业务 → 判号 → 名额概览
    python http_flow.py --all --cookies runs\\rev\\browser_cookies.json

    # 只判号 / 只看名额概览
    python http_flow.py --cookies runs\\rev\\browser_cookies.json --step1
    python http_flow.py --cookies runs\\rev\\browser_cookies.json --status

    # 没有浏览器会话时自己排队等放行
    python http_flow.py --watch --minutes 40
    python http_flow.py --run --worker w1

    # 走代理（票与出口关联，别中途换）
    python http_flow.py --all --cookies runs\\rev\\browser_cookies.json --proxy http://ip:port

退出码
------
  0 有名额/成功   10 链路通但无名额   20 没探到（结构变化/解析失败）
  30 官方 5xx     40 出口被 WAF 限制   41 WAF 412 挑战
  42 会话没放行   43 eventId 失效     44 人机校验

实测踩过的坑（都写在代码里）
----------------------------
* 表单 action 是空串，真正的提交地址由页面 JS 设成 `AdminServlet_<lang>?cmd=cmdGetOfficeQuotaDIAction`
* 繁体页响应是 **Big5**，用 utf-8 解判号会全错
* Queue-it 的 `t=` 是 `http://abs3…`，而 abs3 只开 443 → 必须改写 https
* `forecastStatus=FirstInLine` **不是**放行；放行只认 `status.redirectUrl`
* `eventId` 会变，`--reuse-event` 只用于同场次重试
* curl_cffi：`proxies={}` 会让 libcurl 读野指针（报 `0.0.0.5:1080`）；直连要**不传** proxies 且 trust_env=False
"""

from __future__ import annotations

import os

# 证书校验默认开启。内网自签证书环境可设 BOOKING_INSECURE_TLS=1 关闭。
VERIFY_TLS = os.environ.get("BOOKING_INSECURE_TLS", "") not in ("1", "true", "yes")

# TLS 画像说明
# -------------
# curl_cffi 支持指定 TLS/HTTP2 特征预设（对应库参数）。
# 本项目**不默认启用**这一项：
#   · 默认用 curl_cffi 自身的 TLS 栈发请求；
#   · 需要时通过环境变量 BOOKING_TLS_PROFILE 显式指定（例如 chrome131）。
# 如果某个出口在默认 TLS 下拿不到正常响应，说明该出口与站点不兼容，
# 正确做法是换出口或回到浏览器路径，而不是把 TLS 画像改成别的浏览器。

def tls_profile(explicit: str = "") -> str:
    """解析本次要用的 TLS 画像。默认空（用 curl_cffi 默认栈）。

    显式指定优先级：参数 > 环境变量 BOOKING_TLS_PROFILE。
    返回空字符串表示"不指定"。
    """
    import os
    val = (explicit or os.environ.get("BOOKING_TLS_PROFILE", "") or "").strip()
    if val.lower() in ("none", "off", "0", "default"):
        return ""
    return val

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from throttle import Throttle, ThrottleTripped

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
RUNS.mkdir(exist_ok=True)
LOG_DIR = RUNS / "http_flow"
LOG_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------------ 站点常量
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

ABS3 = "https://abs3.example.test"
ABS4 = "https://abs4.example.test"
ABS1 = "https://abs1.example.test"

ENTRY_SEED = ABS3 + "/?lang=sc"
QUEUE_ENTRY = (ABS3 + "/SelectCountry?"
               "lang=sc&actionType=apply&authMethod=withOutIas")
STEP1_ENTRY = (ABS1 + "/tdab2/tdabs_di_external/AdminServlet"
               "?cmd=cmdIntoSelectAuthDI&func=apply&auth=withOutIas"
               "&country=mainland&lang=sc")
QUOTA_CMD = ("https://abs2.example.test/tdab2/tdabs_di_external/AdminServlet_schinese"
             "?cmd=cmdGetOfficeQuotaDIAction")
STATUS_CMD = ("https://abs1.example.test/tdab2/tdabs_di_external/AdminServlet_tchinese"
              "?cmd=cmdGetOfficeBookingStatusAction&serviceTypeCode=DI"
              "&storedAppointmentOperation=A"
              "&storedAppointmentExpiryDate={expiry}&enablePick=N")

WAF_MARKERS = ("$_ts", "$_ts.cd", "nsd=", "HIK0kat3rt9x")   # 挑战页正文标记（前缀匹配 O/P）
RESTRICTED_MARKERS = ("access has been restricted", "Restricted access",
                      "iCloud Private Relay", "Zscaler")

# 验证码检测：只看「可交互的元素」或「服务端明确拒绝」，不看 CSP 里的域名
# （第 1 步页面的 CSP 里本来就有 service.captcha.example.test，只按字符串匹配会天天误报）
CAPTCHA_SIGNS = [
    ("mtcaptcha", [r"<script[^>]+mtcaptcha", r'class="[^"]*mtcap',
                   r'id="mtcap', r"data-sitekey", r"mtcaptcha\.com/mtcv1"]),
    ("hcaptcha", [r"hcaptcha\.com/1/api\.js", r'class="h-captcha"']),
    ("recaptcha", [r"recaptcha/api\.js", r'class="g-recaptcha"']),
    ("turnstile", [r"challenges\.cloudflare\.com/turnstile"]),
    ("edge_slider", [r"拖动滑块", r"請拖動", r"请拖动", r"verify-drag_puzzle"]),
]
CAPTCHA_REJECT = [r"驗證碼(錯誤|不正確|已過期)", r"验证码(错误|不正确|已过期)",
                  r"captcha\s*(invalid|failed|expired|required)",
                  r"reCAPTCHA|MTCaptcha\s*(failed|required)"]

QS_INQUEUE = 2
QS_SERVED = 3


def now() -> str:
    return time.strftime("%H:%M:%S")


class Log:
    def __init__(self, tag: str = "http_flow", file: bool = True):
        self.tag = tag
        self.fh = None
        if file:
            p = LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.log"
            self.fh = open(p, "a", encoding="utf-8")

    def __call__(self, msg: str, level: str = "·") -> None:
        line = f"{now()} {level} {msg}"
        print(line, flush=True)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()

    def json(self, obj: dict) -> None:
        if self.fh:
            self.fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self.fh:
            self.fh.close()


@dataclass
class QueueCtx:
    customer: str = ""
    event: str = ""
    target: str = ""
    queue_id: str = ""
    location: str = ""
    discovered_at: float = 0.0


@dataclass
class Status:
    n: int = 0
    queue_state: int | None = None
    page_class: str | None = None
    forecast: str | None = None
    progress: float | None = None
    users_ahead: int | None = None
    users_in_queue: int | None = None
    expected_utc: str | None = None
    window_start_utc: str | None = None
    redirect_url: str | None = None
    raw_len: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def released(self) -> bool:
        """只认「明确放行」。QueueState: 0=Unavailable 1=Before 2=InQueue 3=After"""
        if self.redirect_url:
            return True
        if self.page_class and self.page_class != "queue":
            return True
        if self.queue_state == QS_SERVED:
            return True
        if (self.forecast or "").lower() in ("served", "afterevent", "after"):
            return True
        return False


class Sniper:
    def __init__(self, proxy: str = "", timeout: int = 25, log: Log | None = None,
                 auto_rescue: bool = False, rescue_timeout: float = 300.0,
                 session: Any = None, min_interval: float = 3.0,
                 tls_pref: str = "", http1: bool = False):
        """session 传入时（R1 对齐会话），不再新建/改写头部 —— 头部由票决定。

        min_interval 是节流闸门：同一出口两次请求的最小间隔。
        2026-11-11 的事实是「一个出口 2~3 次就进限流名单」，所以这个闸门不是可选项。
        """
        try:
            from curl_cffi import requests as cr
        except ImportError:
            sys.exit("需要 curl_cffi：pip install curl_cffi")
        self.cr = cr
        self.log = log or Log()
        self.timeout = timeout

        # 节流 / 熔断（_gate 与 _get/_post 都依赖它；之前漏赋值，第一次请求就会 AttributeError）
        self.th = Throttle(min_interval=min_interval,
                           log=lambda m, lv="·": self.log(m, lv))

        # 环境参数与协议版本：某些出口（认证型转发代理）不支持 BoringSSL 的 h2/ALPS，
        # chrome 画像会稳定超时；此时用 --tls-profile none --http1 走普通 TLS/1.1。
        self._hv = None
        if http1:
            try:
                from curl_cffi import CurlHttpVersion
                self._hv = CurlHttpVersion.V1_1
            except Exception:
                self._hv = None
        self.tls_name = "" if (tls_pref or "").lower() in ("none", "off", "0", "") else tls_pref

        if session is not None:
            # R1：外部注入的对齐会话，头部/环境参数/代理都已按票复现，不许覆盖
            self.s = session
            self._proxies = None
            self.ctx = QueueCtx()
            self.auto_rescue = auto_rescue
            self.rescue_timeout = rescue_timeout
            return

        kwargs: dict[str, Any] = dict(verify=VERIFY_TLS)
        if self.tls_name:
            kwargs["impersonate"] = self.tls_name
        # 代理：直连时**不要传** proxies 键（curl_cffi 0.13 传 {} 会让 libcurl 读野指针，
        # 报 `Failed to connect to 0.0.0.5 port 1080`）；传代理时 dict 由 self 强引用。
        self._proxies: dict[str, str] | None = (
            {"http": proxy, "https": proxy} if proxy else None)
        if self._proxies:
            kwargs["proxies"] = self._proxies
        else:
            kwargs["trust_env"] = False
        self.s = cr.Session(**kwargs)
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
        })
        self.ctx = QueueCtx()
        self.auto_rescue = auto_rescue
        self.rescue_timeout = rescue_timeout

    # ------------------------------------------------------------ 基础请求
    @staticmethod
    def explain_exc(e: Exception) -> str:
        """把 curl 错误号翻成人话。

        2026-11-11 的教训：当天 16:30 后全链路报「https 不支持 / 被拒绝」，
        实际是两种完全不同的失败被混成一个症状：
          · curl (35) WRONG_VERSION_NUMBER → 对方在 443 上回的不是 TLS
            （最常是代理不支持 CONNECT，或 http:// 地址被当 https 用）
          · curl (35) Connection was reset  → TLS 握手被 RST，本机网络出口 已进限流名单
        分不清这两者，就会把「换出口能解决」误判成「站点不支持 https」。
        """
        s = str(e)
        low = s.lower()
        if "curl: (35)" in s:
            if "wrong_version_number" in low or "wrong version number" in low:
                return ("TLS_WRONG_VERSION：443 上回的不是 TLS —— "
                        "查代理是否支持 CONNECT / 地址是不是被写成了 https")
            if "reset" in low:
                return "TLS_RESET：握手被 RST —— 本机网络出口 大概率已进限流名单，换出口"
            return "TLS_ERROR(35)：握手失败，先判出口再看代理"
        if "curl: (28)" in s or "timed out" in low:
            return "TIMEOUT(28)：TCP 层被丢包（ping 可能还通），换出口"
        if "curl: (56)" in s:
            return "RECV_RESET(56)：连接被重置，出口被打"
        if "curl: (7)" in s or "refused" in low:
            return "CONN_REFUSED(7)：连不上，端口关或代理死"
        if "curl: (52)" in s:
            return "EMPTY_REPLY(52)：对方空回复，常见于被网关直接掐"
        return f"{type(e).__name__}: {s[:120]}"

    def _retry(self, fn, times: int = 2):
        """代理链路首包常见 502/连接重置，补一次再判死。"""
        last = None
        for i in range(times):
            try:
                r = fn()
                if i + 1 < times and r.status_code in (502, 503, 504):
                    self.log(f"   首包 {r.status_code}，重试一次", "~")
                    time.sleep(0.6)
                    continue
                return r
            except Exception as e:
                last = e
                if i + 1 < times:
                    self.log(f"   连接异常：{self.explain_exc(e)}，重试一次", "~")
                    time.sleep(0.8)
        if last:
            raise last
        return r

    def _gate(self, r):
        """每个响应都过一遍节流记账：412 记一次并退避，正常就清零。"""
        if getattr(r, "status_code", 0) == 412:
            try:
                self.th.on_412()
            except ThrottleTripped as e:
                raise SystemExit(f"节流熔断：{e}")
        else:
            self.th.on_ok()
        return r

    def _get(self, url: str, **kw) -> Any:
        kw.setdefault("timeout", self.timeout)
        if self._hv is not None:
            kw.setdefault("http_version", self._hv)
        self.th.wait()
        return self._gate(self._retry(lambda: self.s.get(url, **kw)))

    def _post(self, url: str, **kw) -> Any:
        kw.setdefault("timeout", self.timeout)
        if self._hv is not None:
            kw.setdefault("http_version", self._hv)
        self.th.wait()
        return self._gate(self._retry(lambda: self.s.post(url, **kw)))

    def classify(self, r) -> int:
        text = (r.text or "")[:8000]
        if r.status_code == 412 or any(m in text for m in WAF_MARKERS):
            return 41
        if any(m.lower() in text.lower() for m in RESTRICTED_MARKERS):
            return 40
        if r.status_code in (502, 503, 504):
            return 30
        return 0

    @staticmethod
    def _decode(content: bytes) -> str:
        for enc in ("utf-8", "big5", "big5hkscs", "gb18030"):
            try:
                return content.decode(enc)
            except Exception:
                continue
        return content.decode("utf-8", "replace")

    # ------------------------------------------------------------ 验证码哨兵
    def detect_captcha(self, text: str) -> str:
        """返回验证码类型（空串=没有）。这是「会不会卡住」的答案所在。

        判据分两类，避免误报：
          · 页面里有**可交互的验证码元素**（script/iframe/sitekey/滑块文案）
          · 服务端**明确拒绝**（"验证码错误/已过期" / captcha required）
        注意：CSP 头里出现 `service.captcha.example.test` 不算 —— 第 1 步页面一直有那一行。
        """
        if not text:
            return ""
        low = text.lower()
        for kind, pats in CAPTCHA_SIGNS:
            for pat in pats:
                if re.search(pat, low, re.I):
                    return kind
        for pat in CAPTCHA_REJECT:
            if re.search(pat, low, re.I):
                return "rejected"
        return ""

    def _captcha_alarm(self, kind: str, body: str, tag: str) -> None:
        """撞到验证码：落证据 + 告警 + 通知，然后**停手**（不重试，越试越死）。"""
        p = LOG_DIR / f"captcha-{tag}-{time.strftime('%H%M%S')}.html"
        p.write_text(body or "", encoding="utf-8")
        self.log(f"!! 撞到验证码：{kind}（证据 {p.name}）", "!!")
        self.log("   纯 HTTP 产不出 captchaToken；按下面顺序动手：", "!!")
        self.log("   ① 同会话交给浏览器：runs/rev/grab_page.py 导出 cookie → 人验证交互", "!!")
        self.log("   ② 或接识别服务：MTCaptcha 用 sitekey+pageurl 换 token", "!!")
        self.log("   ③ 第 1 步的 token 实测非强制，可先空着提交试一次", "!!")
        try:
            import notifier
            notifier.captcha_need_human(kind)
        except Exception:
            pass
        try:
            (LOG_DIR / "ALERT.md").write_text(
                f"# 验证码告警 {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"- 类型：`{kind}`\n- 证据：`{p.name}`\n"
                f"- 处置：浏览器接手 / 识别服务 / 空 token 先试\n",
                encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ 自动救援
    def dump_cookies(self) -> list[dict]:
        out = []
        try:
            for c in self.s.cookies.jar:
                out.append({"name": c.name, "value": c.value,
                            "domain": c.domain, "path": c.path or "/"})
        except Exception:
            try:
                for k, v in self.s.cookies.get_dict().items():
                    out.append({"name": k, "value": v, "domain": "", "path": "/"})
            except Exception:
                pass
        return out

    def _reload_cookies(self, cookies: list[dict]) -> int:
        try:
            self.s.cookies.clear()
        except Exception:
            pass
        n = 0
        for c in cookies:
            try:
                self.s.cookies.set(name=c["name"], value=c["value"],
                                   domain=c.get("domain") or "",
                                   path=c.get("path") or "/")
                n += 1
            except Exception:
                pass
        return n

    def rescue(self, url: str) -> bool:
        """开浏览器把这一条会话救回来：你拖一下滑块，脚本自动拿回 cookie。

        这是「纯 HTTP 产不出 captchaToken」的唯一正解：把人手动那一步压到 30 秒。
        """
        if not (self.auto_rescue and url):
            return False
        inp = LOG_DIR / "rescue_in.json"
        outp = LOG_DIR / "rescue_out.json"
        inp.write_text(json.dumps(self.dump_cookies(), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        self.log(f"   → 启动救援浏览器（{url[:80]}），请在窗口里拖一下滑块，"
                 f"最多等 {int(self.rescue_timeout)}s", "R")
        cmd = [sys.executable, str(BASE / "captcha_handoff.py"), "--url", url,
               "--cookies", str(inp), "--timeout", str(self.rescue_timeout),
               "--out", str(outp)]
        try:
            rc = subprocess.call(cmd)
        except Exception as e:
            self.log(f"   救援进程起不来：{type(e).__name__}", "!")
            return False
        if rc != 0 or not outp.exists():
            self.log(f"   救援失败（退出码 {rc}）：超时没人操作或页面打不开", "!")
            return False
        try:
            cookies = json.loads(outp.read_text(encoding="utf-8"))
        except Exception:
            return False
        n = self._reload_cookies(cookies)
        self.log(f"   √ 救援成功，回灌 {n} 条 cookie，继续跑", "√")
        return n > 0

    # ------------------------------------------------------------ ① WAF
    def edge_land(self) -> int:
        r = self._get(ENTRY_SEED, allow_redirects=True)
        code = self.classify(r)
        self.log(f"① WAF abs3/?lang=sc → {r.status_code} len={len(r.content)} "
                 f"cookies={list(self.s.cookies.keys())}", "1")
        if code:
            self.log(f"   被挡：退出码 {code}", "!")
        return code

    # ------------------------------------------------------------ ② 排队发现
    def discover(self) -> int:
        r = self._get(QUEUE_ENTRY, allow_redirects=False)
        code = self.classify(r)
        if code:
            self.log(f"② 排队发现被挡：{r.status_code} → {code}", "!")
            return code
        if r.status_code not in (301, 302, 303, 307, 308):
            self.log(f"② 无 302（{r.status_code}）→ 会话可能已在放行态", "2")
            self.ctx.target = QUEUE_ENTRY
            self.ctx.discovered_at = time.time()
            return 0
        loc = r.headers.get("Location", "")
        self.ctx.location = loc
        self.ctx.discovered_at = time.time()
        q = parse_qs(urlparse(loc).query)
        self.ctx.customer = (q.get("c") or [""])[0]
        self.ctx.event = (q.get("e") or [""])[0]
        self.ctx.target = (q.get("t") or [""])[0]
        self.log(f"② 排队入口 → c={self.ctx.customer} e={self.ctx.event}", "2")
        self.log(f"   target={self.ctx.target}")
        if not (self.ctx.customer and self.ctx.event):
            self.log("   Location 里没解析出 c/e，站点结构可能变了", "!")
            return 20
        return 0

    # ------------------------------------------------------------ ③ 取号
    def enqueue(self) -> int:
        url = f"{ABS4}/spa-api/queue/{self.ctx.customer}/{self.ctx.event}/enqueue"
        r = self._post(url, json={}, headers={
            "Accept": "application/json", "Origin": ABS3, "Referer": ABS3 + "/"})
        code = self.classify(r)
        if code:
            self.log(f"③ enqueue 失败 {r.status_code} → {code}", "!")
            return code
        if r.status_code == 400:
            self.log(f"③ enqueue 400：eventId 不对或事件已结束（{self.ctx.event}）", "!")
            return 43
        try:
            j = r.json()
        except Exception:
            self.log(f"③ enqueue 响应不是 JSON：{r.text[:200]}", "!")
            return 20
        self.ctx.queue_id = j.get("queueId") or ""
        self.log(f"③ enqueue → queueId={self.ctx.queue_id} "
                 f"challengeRequired={j.get('challengeRequired')} "
                 f"busy={j.get('serverIsBusy')}", "3")
        if j.get("challengeRequired"):
            self.log("   challengeRequired=true（Queue-it 自己的验证码）", "!")
            return 44
        if not self.ctx.queue_id:
            self.log(f"   没有 queueId：{j}", "!")
            return 43
        return 0

    # ------------------------------------------------------------ ④ 排队监控
    def status(self, n: int = 0) -> tuple[int, Status]:
        url = (f"{ABS4}/spa-api/queue/{self.ctx.customer}/{self.ctx.event}/"
               f"{self.ctx.queue_id}/status")
        r = self._post(url, json={}, headers={
            "Accept": "application/json", "Referer": ABS3 + "/"})
        st = Status(n=n, raw_len=len(r.content))
        code = self.classify(r)
        if code:
            return code, st
        try:
            j = r.json()
        except Exception:
            return 20, st
        st.raw = j
        st.queue_state = j.get("QueueState")
        st.page_class = j.get("pageClass")
        st.forecast = j.get("forecastStatus")
        st.redirect_url = j.get("redirectUrl")
        t = j.get("ticket") or {}
        st.progress = t.get("progress")
        st.users_ahead = t.get("usersInLineAheadOfYou")
        st.users_in_queue = t.get("usersInQueue")
        st.expected_utc = t.get("expectedServiceTimeUTC")
        st.window_start_utc = t.get("windowStartTimeUTC")
        return 0, st

    def watch(self, minutes: float = 10.0, interval: float = 3.0,
              max_errors: int = 3) -> tuple[int, Status]:
        deadline = time.time() + minutes * 60
        errs = 0
        n = 0
        last = Status()
        while time.time() < deadline:
            n += 1
            code, st = self.status(n)
            if code:
                errs += 1
                self.log(f"④ 第 {n} 次 status 失败（{code}），连续 {errs}/{max_errors}", "!")
                if errs >= max_errors:
                    return code, st
                time.sleep(min(interval * (2 ** errs), 30))
                continue
            errs = 0
            last = st
            self.log(f"④ #{n:03d} state={st.queue_state} page={st.page_class} "
                     f"forecast={st.forecast} ahead={st.users_ahead} "
                     f"inQ={st.users_in_queue} eta={st.expected_utc}", ".")
            self.log.json({"phase": "status", **asdict(st)})
            if st.released:
                # 放行那一刻必须改道到 redirectUrl：Queue-it 的放行票（QueueITAccepted）
                # 是在 GET 这个 URL 时由 Set-Cookie 下发的，回敲排队入口只会重新排一次。
                if st.redirect_url:
                    self.ctx.target = st.redirect_url
                self.log(f"④ 已放行：redirectUrl={st.redirect_url} page={st.page_class}", "√")
                self.log(f"   ⑤ 将进 {self.ctx.target[:100]}", "5")
                return 0, st
            time.sleep(interval + random.uniform(0, interval * 0.4))
        return 42, last

    # ------------------------------------------------------------ ⑤ 进入业务
    def enter(self) -> int:
        target = self.ctx.target or QUEUE_ENTRY
        if target.startswith("http://"):
            target = "https://" + target[len("http://"):]
        r = self._get(target, allow_redirects=True)
        code = self.classify(r)
        if code:
            self.log(f"⑤ 进入业务被挡 {r.status_code} → {code}", "!")
            return code
        p = LOG_DIR / f"enter-{time.strftime('%H%M%S')}.html"
        p.write_text(r.text or "", encoding="utf-8", errors="ignore")
        qi = [n for n in ("QueueITAccepted-SDFrts345E-V3_", "urltoken")
              if any(n in (c.name or "") for c in getattr(self.s.cookies, "jar", []))]
        self.log(f"⑤ 业务页 {r.status_code} len={len(r.content)} → {p.name}"
                 + (f"  放行票={qi}" if qi else "  ⚠ 未见放行票"), "5")
        cap = self.detect_captcha(r.text or "")
        if cap:
            self._captcha_alarm(cap, r.text or "", "enter")
            if self.rescue(target):
                return self.enter()
            return 44
        return 0

    # ------------------------------------------------------------ 会话复用
    def load_cookies(self, path: str | Path) -> int:
        """把浏览器导出的 cookie（playwright JSON 格式）灌进当前会话。

        这是「人工验证交互一次 → 之后全程无浏览器」的接口；
        runs/rev/grab_page.py 会生成 browser_cookies.json。
        """
        p = Path(path)
        if not p.exists():
            self.log(f"cookie 文件不存在：{p}", "!")
            return 0
        data = json.loads(p.read_text(encoding="utf-8"))
        n = 0
        for c in data:
            try:
                self.s.cookies.set(name=c["name"], value=c["value"],
                                   domain=c["domain"], path=c.get("path", "/"))
                n += 1
            except Exception:
                pass
        self.log(f"灌入 cookie {n}/{len(data)} 条（{p.name}）", "·")
        return n

    # ------------------------------------------------------------ ⑥ 判号
    def step1(self, app: dict | None = None, captcha_token: str = "") -> tuple[int, str]:
        """第 1 步：GET 认证页 → 按页面 JS 的 action 提交 → 判「有没有名额」。

        实测（2026-11-11）：`captchaToken` 留空也能提交，服务端照常回答名额问题。
        """
        r0 = self._get(STEP1_ENTRY, allow_redirects=False)
        if r0.status_code in (301, 302, 303):
            self.log(f"⑥ step1 被弹回 {r0.headers.get('Location','')[:80]}（会话没放行）", "!")
            return 42, ""
        code = self.classify(r0)
        if code:
            return code, ""
        html = self._decode(r0.content)
        if "frmAppointment" not in html:
            cap = self.detect_captcha(html)
            if cap:
                self._captcha_alarm(cap, html, "step1-get")
                if self.rescue(STEP1_ENTRY):
                    return self.step1(app=app, captcha_token=captcha_token)
                return 44, html
            self.log("⑥ step1 页面里没有 frmAppointment（结构可能变了）", "!")
            return 20, html

        fields: dict[str, str] = {}
        for m in re.finditer(r"<input\s[^>]*>", html):
            tag = m.group(0)
            nm = re.search(r'name="([^"]+)"', tag)
            if not nm:
                continue
            vm = re.search(r'value="([^"]*)"', tag)
            fields[nm.group(1)] = vm.group(1) if vm else ""

        act = re.search(r"frmAppointment\.action\s*=\s*'([^']+)'", html)
        if act:
            action = act.group(1)
            url = action if action.startswith("http") else ABS1 + action
        else:
            lang = fields.get("lang") or "tchinese"
            url = (f"{ABS1}/tdab2/tdabs_di_external/AdminServlet_{lang}"
                   "?cmd=cmdGetOfficeQuotaDIAction")
        self.log(f"⑥ step1 字段 {len(fields)} 个，提交地址 {url}", "6")

        if app:
            fields.update({
                "storedAppointmentIdentityDocumentType": "EEP",
                "storedAppointmentIdentityDocumentNumber": app.get("doc_no", ""),
                "storedAppointmentDrivingLicenceNumber": app.get("dl_no", ""),
                "storedAppointmentLicenseNumber": app.get("dl_no", ""),
                "storedAppointmentChineseName": app.get("name_cn", ""),
                "storedAppointmentLicenseOwnerName": app.get("name_cn", ""),
                "storedAppointmentEmailAddress": app.get("email", ""),
                "storedAppointmentConfirmationEmail": app.get("email", ""),
                "storedAppointmentReminderEmail": app.get("email", ""),
                "storedAppointmentIssuingCountry": "CN",
                "storedAppointmentAuthType": "withOutIas",
                "isAuthConfirm": "Y",
                "userAgent": UA.lower(),
            })
            if app.get("phone"):
                fields["storedAppointmentPhoneNumber"] = app["phone"]
        fields["captchaToken"] = captcha_token or fields.get("captchaToken", "")

        r1 = self._post(url, data=fields, allow_redirects=False, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": ABS1,
            "Referer": STEP1_ENTRY,
        })
        code = self.classify(r1)
        if code:
            return code, ""
        body = self._decode(r1.content)
        p = LOG_DIR / f"step1-{time.strftime('%H%M%S')}.html"
        p.write_text(body, encoding="utf-8")
        cap = self.detect_captcha(body)
        if cap:
            self._captcha_alarm(cap, body, "step1-post")
            if self.rescue(STEP1_ENTRY):
                return self.step1(app=app, captcha_token=captcha_token)
            return 44, body
        track = re.search(r"TrackId:\s*([0-9\-a-f]+)", body)
        no_quota = ("沒有可供預約" in body) or ("没有可供预约" in body) or \
                   ("no available" in body.lower())
        # 「有名额」的标志：页面里出现下一步的入口（不是导航栏里的「輸入資料」字样）
        get_in = ("cmdGetTimeslot" in body) or ("selectTimeslot" in body) or \
                 ("id=\"timeslot" in body)
        self.log(f"⑥ step1 → {r1.status_code} len={len(body)} 无名额={no_quota} "
                 f"可进下一步={get_in} TrackId={track.group(1) if track else '-'} → {p.name}", "6")
        if no_quota:
            return 10, body
        if get_in:
            return 0, body
        return 20, body

    # ------------------------------------------------------------ ⑦ 名额概览
    def booking_status(self) -> dict:
        """各办事处 × 日期 × 上午/下午 的剩余名额（不需要当前有名额就能查）。

        页面结构（实测 2026-11-11）：
          表头  日期 | 時間 | 香港牌照事務處(colspan=3) | 九龍(2) | 觀塘(2) | 沙田(2)
          数据  日期(rowspan=2) | 上午/下午 | 9 个名额格
                名额格是 <img src=".../cross.gif"> = 无号；有号时是文字
        """
        expiry = time.strftime("%d-%m-%Y")
        url = STATUS_CMD.format(expiry=expiry)
        r = self._get(url, allow_redirects=False, headers={"Referer": STEP1_ENTRY})
        code = self.classify(r)
        if code:
            return {"error": code}
        body = self._decode(r.content)
        p = LOG_DIR / f"booking-{time.strftime('%H%M%S')}.html"
        p.write_text(body, encoding="utf-8")
        cap = self.detect_captcha(body)
        if cap:
            self._captcha_alarm(cap, body, "booking")
            if self.rescue(url):
                return self.booking_status()
            return {"error": 44, "captcha": cap, "file": p.name}

        mu = re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{1,2}:[0-9]{2})", body)
        updated = mu.group(1) if mu else ""

        # --- 表头：办事处 + 各占几列
        offices: list[dict] = []
        for tr in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", body):
            seg = tr.group(1)
            if "日期" not in seg or "時間" not in seg or "牌照事務處" not in seg:
                continue
            for tm in re.finditer(r"(?is)<td([^>]*)>(.*?)</td>", seg):
                attrs, inner = tm.group(1), tm.group(2)
                name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner)).strip()
                if "牌照事務處" not in name:
                    continue
                cs = re.search(r'colspan="(\d+)"', attrs)
                offices.append({"name": name, "cols": int(cs.group(1)) if cs else 1})
            break
        # 列 → 办事处 的映射
        col_owner: list[str] = []
        for o in offices:
            col_owner.extend([o["name"]] * o["cols"])

        # --- 数据行
        rows: list[dict] = []
        free: list[dict] = []
        cur_date = ""
        for tr in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", body):
            seg = tr.group(1)
            tds = re.findall(r"(?is)<td[^>]*>(.*?)</td>", seg)
            if not tds:
                continue
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip() for t in tds]
            crosses = ["cross.gif" in t for t in tds]

            idx = 0
            d = re.match(r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日", cells[0])
            if d:
                cur_date = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
                idx = 1
            if not cur_date:
                continue
            if any("休息" in c for c in cells):
                rows.append({"date": cur_date, "ampm": "休息", "closed": True, "cells": []})
                continue
            if idx >= len(cells) or cells[idx] not in ("上午", "下午"):
                continue
            ampm = cells[idx]
            idx += 1
            slots = cells[idx:]
            slot_cross = crosses[idx:]
            row = {"date": cur_date, "ampm": ampm, "closed": False,
                   "cells": slots, "owner": col_owner[idx - 1:]}
            rows.append(row)
            for i, c in enumerate(slots):
                has = bool(c) and not (i < len(slot_cross) and slot_cross[i])
                if has:
                    who = col_owner[i] if i < len(col_owner) else f"列{i}"
                    free.append({"date": cur_date, "ampm": ampm,
                                 "office": who, "value": c})

        out = {"updated": updated,
               "offices": [o["name"] for o in offices],
               "col_layout": [{"name": o["name"], "cols": o["cols"]} for o in offices],
               "rows": len(rows), "free": free, "file": p.name}
        self.log(f"⑦ 名额概览：{len(offices)} 个办事处 {col_owner and len(col_owner) or 0} 列 / "
                 f"{len(rows)} 个时段行 / 有号 {len(free)} 处（页面时间 {updated or '-'}）", "7")
        for f in free[:12]:
            self.log(f"   ★ {f['office']} {f['date']} {f['ampm']} = {f['value']}", "★")
        (LOG_DIR / f"booking-{time.strftime('%H%M%S')}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    # ------------------------------------------------------------ 兼容：模板重放
    def quota_form_from_capture(self) -> dict | None:
        p = RUNS / "net" / "net.jsonl"
        if not p.exists():
            return None
        best = None
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if "cmdGetOfficeQuotaDIAction" not in (rec.get("url") or ""):
                    continue
                d = {k: v for k, v in (rec.get("fields") or []) if isinstance(v, str)}
                if len(d) >= 20 and (best is None or len(d) > len(best)):
                    best = d
        return best

    def apply_applicant(self, form: dict, app: dict) -> dict:
        f = dict(form)
        f.update({
            "storedAppointmentServiceType": "DI",
            "storedAppointmentRequestChannel": "W",
            "storedAppointmentIdentityDocumentNumber": app.get("doc_no", ""),
            "storedAppointmentDrivingLicenceNumber": app.get("dl_no", ""),
            "storedAppointmentChineseName": app.get("name_cn", ""),
            "storedAppointmentEmailAddress": app.get("email", ""),
            "storedAppointmentIssuingCountry": "CN",
            "isAuthConfirm": "Y",
        })
        if app.get("phone"):
            f["storedAppointmentPhoneNumber"] = app["phone"]
        return f

    def query_quota(self, form: dict | None = None, app: dict | None = None) -> tuple[int, str]:
        if form is None:
            return self.step1(app=app)
        form = self.apply_applicant(form, app) if app else dict(form)
        r = self._post(QUOTA_CMD, data=form, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": ABS3 + "/", "Origin": ABS3,
        }, allow_redirects=False)
        code = self.classify(r)
        if code:
            return code, ""
        if r.status_code in (301, 302, 303):
            return 42, ""
        text = self._decode(r.content)
        no_quota = ("沒有可供預約" in text) or ("没有可供预约" in text)
        return (10 if no_quota else 20), text

    # ------------------------------------------------------------ 组合
    def prepare(self) -> int:
        for fn in (self.edge_land, self.discover, self.enqueue):
            code = fn()
            if code:
                return code
        return 0


# ------------------------------------------------------------------ CLI
def load_config(worker: str | None) -> dict:
    p = BASE / "config.json"
    if not p.exists():
        return {}
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    apps = cfg.get("applicants") or []
    if worker:
        cfg["_app"] = next((a for a in apps if a.get("id") == worker), None)
    elif apps:
        cfg["_app"] = apps[0]
    return cfg


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="BOOKING 纯 HTTP 全流程（WAF → Queue-it → 判号 → 名额概览）")
    ap.add_argument("--probe", action="store_true", help="体检：①→④×3")
    ap.add_argument("--watch", action="store_true", help="挂机监控排队直到放行")
    ap.add_argument("--run", action="store_true", help="放行后进业务+判号")
    ap.add_argument("--step1", action="store_true", help="只判号（需要有效会话）")
    ap.add_argument("--quota", action="store_true", help="同 --step1")
    ap.add_argument("--status", action="store_true", help="只查各办事处名额概览")
    ap.add_argument("--all", action="store_true", help="一条 IP 跑完整条：进业务→判号→名额概览")
    ap.add_argument("--cookies", default="", help="浏览器导出的 cookie JSON（跳过排队）")
    ap.add_argument("--align", default="",
                    help="R1：票文件（mint_align.py 产物）。按票逐字重放 UA/sec-ch-ua/cookie，"
                         "并自动发现可用 TLS 画像 —— 真零浏览器")
    ap.add_argument("--synth-secfetch", action="store_true",
                    help="配合 --align：补 sec-fetch-*（推断项，排查 400 时才开）")
    ap.add_argument("--align-budget", type=int, default=3,
                    help="--align 首次画像发现的请求上限（默认 3，别再往上加）")
    ap.add_argument("--proxy", default="", help="出口代理，例如 http://ip:port 或 http://u:p@ip:port")
    ap.add_argument("--tls-profile", default="",
                    help="curl_cffi 画像；认证型转发代理不支持 h2/ALPS 时用 none")
    ap.add_argument("--http1", action="store_true", help="强制 HTTP/1.1（配合 --tls-profile none）")
    ap.add_argument("--worker", default="", help="config.json 里的申请人 id（w1..w4）")
    ap.add_argument("--minutes", type=float, default=10.0, help="--watch 观察窗口（分钟）")
    ap.add_argument("--interval", type=float, default=3.0, help="status 轮询间隔秒")
    ap.add_argument("--reuse-event", default="", help="复用已知 eventId（跳过 ②）")
    ap.add_argument("--reuse-target", default="", help="配合 --reuse-event 的目标 t=")
    ap.add_argument("--json", action="store_true", help="结构化输出")
    ap.add_argument("--auto-rescue", action="store_true",
                    help="撞到验证码时自动开浏览器，你拖一下滑块后脚本自动继续")
    ap.add_argument("--rescue-timeout", type=float, default=300.0,
                    help="等人验证交互的时限（秒）")
    a = ap.parse_args()

    # --align 单独用时默认判号（票已经是过挑战的会话，再跑一遍排队只是白烧配额）
    if a.align and not any([a.probe, a.watch, a.run, a.quota, a.step1, a.status, a.all]):
        a.step1 = True

    if not any([a.probe, a.watch, a.run, a.quota, a.step1, a.status, a.all]):
        a.probe = True

    log = Log("http_flow" + (f"-{a.worker}" if a.worker else ""))
    cfg = load_config(a.worker)
    app = cfg.get("_app")

    # ---------- R1：按票对齐（真零浏览器）
    align_session = None
    if a.align:
        # --align 依赖"票据环境画像对齐"能力。这部分实现已从共享树移出
        # （见 ARCHIVE_MANIFEST.md 的 capability_review/env_spoofing）。
        # 共享版本保留参数定义但功能不可用，避免调用点静默降级。
        try:
            from align_client import session_for
        except ImportError:
            log("--align 不可用：环境画像对齐模块已移出共享树，请改用 --cookies 复用浏览器会话", "!")
            log.close()
            return 2
        try:
            align_session = session_for(
                a.align, log=lambda m, lv="·": log(m, lv),
                probe=True, budget=a.align_budget,
                synth_secfetch=a.synth_secfetch)
        except SystemExit as e:
            log(str(e), "!")
            log("→ 这张票在纯 HTTP 下不通；换出口重铸，或用 R2（patchright 无窗口内核）取票", "!")
            log.close()
            return 41

    sp = Sniper(proxy=a.proxy, log=log,
                auto_rescue=a.auto_rescue, rescue_timeout=a.rescue_timeout,
                session=align_session, tls_pref=a.tls_profile, http1=a.http1)
    result: dict[str, Any] = {"ok": False, "exit": 0, "queue": {}, "quota": None}

    # ---------- 复用已有会话：跳过排队（R1 票 或 浏览器导出 cookie）
    if (a.cookies or a.align) and (a.step1 or a.quota or a.run or a.status or a.all):
        if a.cookies and not a.align:
            if not sp.load_cookies(a.cookies):
                log("cookie 没读到", "!")
                log.close()
                return 20
        result["queue"] = {"mode": "align-r1" if a.align else "reuse-browser-session",
                           "ticket": a.align or a.cookies, "proxy": a.proxy}
        code, _ = sp.step1(app=app)
        result["quota"] = code
        if code == 0:
            log("★ 有名额：会话可用，可进第 2 步", "★")
        elif code == 10:
            log("当前无名额（正常；工作日 17:00 更新）", "-")
        elif code == 42:
            log("会话没放行（cookie 过期 / 换过 IP 且票作废）", "!")
        if a.status or a.all:
            st = sp.booking_status()
            result["booking"] = st
            if st.get("free"):
                result["ok"] = True
        result["ok"] = result["ok"] or (code == 0)
        result["exit"] = code
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        log.close()
        return code

    # ---------- ① ② ③
    if a.reuse_event:
        sp.ctx.customer = "tdabsditest"
        sp.ctx.event = a.reuse_event
        sp.ctx.target = a.reuse_target or QUEUE_ENTRY
        code = sp.enqueue()
    else:
        code = sp.prepare()
    result["queue"].update({
        "customer": sp.ctx.customer, "event": sp.ctx.event,
        "queue_id": sp.ctx.queue_id, "target": sp.ctx.target})
    if code:
        log(f"前置失败，退出码 {code}")
        result["exit"] = code
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        log.close()
        return code

    # ---------- ④
    if a.probe and not (a.watch or a.run or a.all):
        for i in range(1, 4):
            code, st = sp.status(i)
            if code:
                result["exit"] = code
                break
            log(f"④ #{i} state={st.queue_state} page={st.page_class} "
                f"forecast={st.forecast} ahead={st.users_ahead} eta={st.expected_utc}", ".")
            time.sleep(a.interval)
        if code or not (a.status or a.all):
            result["exit"] = code or 0
            if a.status or a.all:
                pass
            else:
                if a.json:
                    print(json.dumps(result, ensure_ascii=False))
                log.close()
                return result["exit"]

    if a.watch or a.run or a.all:
        code, st = sp.watch(minutes=a.minutes, interval=a.interval)
        result["last_status"] = {k: v for k, v in asdict(st).items() if k != "raw"}
        if code == 42:
            log("排队未放行（事件未开或已过期）；若已有浏览器会话，改 --cookies 直接验业务层")
            result["exit"] = 42
            if a.json:
                print(json.dumps(result, ensure_ascii=False))
            log.close()
            return 42

    # ---------- ⑤⑥⑦
    if a.run or a.all:
        code = sp.enter()
        if code:
            result["exit"] = code
            if a.json:
                print(json.dumps(result, ensure_ascii=False))
            log.close()
            return code
        code, _ = sp.step1(app=app)
        result["quota"] = code
        if a.status or a.all:
            result["booking"] = sp.booking_status()
        result["exit"] = code
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        log.close()
        return code

    # ---------- 只查名额概览
    if a.status:
        st = sp.booking_status()
        result["booking"] = st
        result["exit"] = 0 if st.get("free") else 10
        if a.json:
            print(json.dumps(result, ensure_ascii=False))
        log.close()
        return result["exit"]

    result["ok"] = True
    result["exit"] = 0
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
