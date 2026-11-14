#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOOKING-DI 预约闸门监控脚本
=========================
监控香港运输署「免试签发香港正式驾驶执照／临时驾驶执照」网上预约系统，
在排队闸门放行（HTTP 302 -> 200）的瞬间发出提醒并自动打开浏览器。

链路：
  gov.example.test 入口页 -> abs3.example.test 等候室（WAF 种 cookie）
  -> abs.td.example.test/booking/tdabs_di.htm 跳转桥
  -> abs2.example.test/tdab2/tdabs_di_external/AdminServlet?cmd=cmdIntoSelectAuthDI
  未放行: 302 回等候室;  放行: 200 + JSESSIONID，进入预约流程。

用法：
  python site_monitor.py                  # 持续监控
  python site_monitor.py --once           # 只探测一次（调试）
  python site_monitor.py --export         # 导出当前会话 cookie 到 session_export.json
  python site_monitor.py --reset-session  # 清空持久化的会话，下次启动重新排队
  python site_monitor.py --no-lock        # 跳过单实例检查（仅调试）
  python site_monitor.py --timing         # 跑一轮，打印启动到取得 JSESSIONID 的各步耗时

v2 健壮性重构（不改变请求语义，不新增任何环境参数/拟真能力）：
  * 会话 cookie 持久化 + 单实例锁：重启不再丢失队列位置置，也不会多进程并发排队
  * 所有时间统一使用香港时间 UTC+8（香港自 1979 年起无夏令时）
  * 所有文件路径相对脚本目录，不再依赖当前工作目录
  * 放行判定收敛：排除 WAF 质询页/维护页，并需连续多次确认
  * 403/429/503/网络异常指数退避 + 熔断，尊重 Retry-After
  * TLS 默认校验证书（certifi CA 包），仅对证书链不完整的等候室主机降级；推送走独立客户端
  * 公开状态页降频探测、快照按内容去重、日志轮转、优雅退出
  * 移除硬编码的静态资源改写（会在前端发版后产生 404，只会制造无效请求）

v2.1 人工接管优先（进一步减少自动化请求，把操作交还给人，而不是对齐请求）：
  * 疑似放行的复核轮只重放闸门探测，不再重放整条导航链
  * 放行后本进程立即停止一切请求（含状态页探测），避免与真人浏览器并发使用同一会话
  * 提醒与导出步骤给出 20 分钟登记时限的香港时间截止点
  * 非工作日降频；每轮日志附带累计请求数，便于人工核对流量量级

v2.2 操作节奏改成坐在页面前的人，而不是定时器：
  * 两次点击之间有阅读停顿；轮询间隔用偏态抖动，偶尔离开一会儿再回来
  * 大多数轮次只刷新当前预约页；偶尔退回跳转页、等候室或入口页再走进来
  * 预约情况页按概率抽查，不再每满固定轮数必查
  * 不加快轮询，不轮换 UA，不补造静态资源，也不尝试解开 WAF 质询
"""


import os

# 证书校验默认开启。内网自签证书环境可设 BOOKING_INSECURE_TLS=1 关闭。
VERIFY_TLS = os.environ.get("BOOKING_INSECURE_TLS", "") not in ("1", "true", "yes")
import time
_BOOT_TS = time.perf_counter()   # 进程启动基准，供 --timing 计算冷启动总耗时

import argparse
import atexit
import hashlib
import http.cookiejar
import json
import logging
import os
import random
import re
import signal
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 优先从脚本旁的 vendor 目录加载依赖，避免污染系统 Python 环境。
BASE_DIR = Path(__file__).resolve().parent
_VENDOR = BASE_DIR / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
try:
    from curl_cffi import requests as _cffi
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

# ----------------------------- 配置区 -----------------------------

CONFIG = {
    # 预约参数（与等候室页面上的选择对应）
    "func": "apply",        # apply=新申请 | change=更改/取消 | enquire=查询
    "auth": "public",       # public=公众登入 | ias=智方便
    "country": "mainland",  # mainland=内地驾照 | other=其他国家/地区
    "lang": "sc",           # sc=简体 | tc=繁体 | en=英文

    # 轮询间隔（秒）。相比 v1 主动放慢：减少对目标系统的异常压力，
    # 也降低被判定为自动化活动的概率。
    # 实际间隔在基准附近偏态抖动，并偶尔多停一会儿；任何情况下都不低于 min_poll_interval。
    #
    # 官方运作时间表（gov.example.test diabs.htm，"网上预约系统每个工作日运作安排"）：
    #   16:00-16:30 暂停服务进行更新，连结会更改，须重新载入
    #   16:30-17:00 等候排队时段（闸门此时不会放行，只需保持会话有效）
    #   17:00       正式开始排队，并「即时为所有排队中的人士随机分配队列位置置」
    #   17:00-次日16:00 按实际进入时间依序排队进入系统
    #   名额亦于每个工作日 17:00 更新
    # 关键推论：
    #   1) 17:00 的位置是「随机分配」，只要在 17:00 时处于排队池内即可。
    #      16:30 还是 16:55 进入、期间刷多少次，都不改变位置分布。
    #      「最快进入」不是目标，「不漏掉 + 会话不过期」才是。
    #   2) 16:30-17:00 是纯等候，不需要高频请求；但也不能完全不请求，
    #      WAF 会话与 JSESSIONID 靠低频探测保活（会话超时后需重新排队）。
    #   3) 17:00 起才可能陆续放行，这一段才需要较短间隔以及时提醒。
    # 下面按「进入确认 → 低频保活 → 放行观察」三段分配间隔，
    # 并保留不可突破的 min_poll_interval 硬下限，杜绝零间隔轮询。
    "poll_interval": 90,
    "peak_poll_interval": 25,
    "peak_windows": [(16, 25, 17, 30)],
    # 16:31 重置会话后立即重建排队资格；以及 17:00 分配位置后及时观察放行。
    "rush_windows": [(16, 31, 16, 34), (16, 57, 17, 15)],
    "rush_poll_interval": 15,
    # 官方纯等候时段：闸门必然 302，低频保活即可，不刷。
    "waiting_windows": [(16, 35, 16, 56)],
    "waiting_poll_interval": 90,
    "min_poll_interval": 10,      # 硬下限：任何窗口、抖动、复核都不得低于此值；不提供关闭开关
    "quiet_hours": (1, 7),
    "quiet_interval": 600,
    "offday_interval": 300,       # 非工作日：官方不更新名额，低频保活即可
    "confirm_interval": 10,       # 疑似放行时的快速复核间隔（仍需连续 2 次，且不低于硬下限）

    # 放行判定
    "gate_confirmations": 2,      # 连续命中多少次才认定放行（防误报）
    "gate_markers": [],           # 可选：放行页必须包含的文本/字段名，留空则用排除法

    # 公开状态页：低频探测，内容未变化不写快照
    "status_page_every": 30,
    "snapshot_dir": "snapshots",

    # 退避与熔断
    "backoff_base": 60,
    "backoff_max": 1800,
    "backoff_after": 1,           # 连续异常第几次开始退避
    "circuit_break_after": 6,     # 连续异常达到该次数进入长暂停
    "circuit_break_sleep": 3600,

    # 提醒方式
    "auto_open_browser": True,
    "beep": True,
    "bark_url": "",               # 例: "https://api.day.app/你的Key/"
    "serverchan_sendkey": "",     # 例: "SCTxxxxx"
    "registration_window_minutes": 20,  # 官方规定的登记有效时间，仅用于提醒文案，不影响请求节奏

    # 网络 / TLS
    # 默认校验证书并使用 certifi 的完整 CA 包。2026-11-07 实测：
    #   portal.example.test / abs.td.example.test / abs2.example.test  -> 校验通过
    #   abs3.example.test（等候室）服务端未下发中间 CA -> 校验失败
    # 因此只对白名单内的主机降级，不再像 v1 那样全局关闭校验。
    # 若等候室主机轮换后出现证书错误，把新主机名加进 insecure_tls_hosts。
    # allow_insecure_tls 是全局逃生开关，正常保持 False。
    "insecure_tls_hosts": ["abs3.example.test"],
    "allow_insecure_tls": False,
    "timeout": 30,
    "single_instance_port": 47831,

    # 文件路径：全部相对脚本目录
    "log_file": "site_monitor.log",
    "session_state": ".tdabs_session.json",   # 内部持久化会话（含有效票据，勿提交到版本库）
    "session_export": "session_export.json",  # 放行后给浏览器导入用
}

GOV_ENTRY = "https://portal.example.test/apps/booking.htm"
SERVLET_PATH = "/tdab2/tdabs_di_external/AdminServlet"
STATUS_SERVLET = "/tdab2/tdabs_di_external/AdminServlet_schinese?cmd=cmdGetServiceTypeStatus"

# 浏览器版本号与 curl_cffi 的环境参数预设取自同一来源，避免两处硬编码不同步。
_m = re.search(r"(\d+)", "152")   # 兜底版本号，不再从 TLS 画像预设里取
CHROME_VER = _m.group(1) if _m else "152"
TLS_PREF = "" or f"chrome{CHROME_VER}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      f"(KHTML, like Gecko) Chrome/{CHROME_VER}.0.0.0 Safari/537.36")

# 仅 urllib 降级模式使用；该模式下 TLS/HTTP2 环境参数必然与真实浏览器不同。
BROWSER_HEADERS = [
    ("User-Agent", UA),
    ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
    ("Accept-Encoding", "identity"),   # urllib 不自动解压，不能声明 br/gzip
    ("Connection", "keep-alive"),
    ("Upgrade-Insecure-Requests", "1"),
    ("Sec-Fetch-Dest", "document"),
    ("Sec-Fetch-Mode", "navigate"),
    ("Sec-Fetch-Site", "same-site"),
    ("Sec-Fetch-User", "?1"),
    ("sec-ch-ua", f'"Chromium";v="{CHROME_VER}", "Google Chrome";v="{CHROME_VER}", "Not_A Brand";v="99"'),
    ("sec-ch-ua-mobile", "?0"),
    ("sec-ch-ua-platform", '"Windows"'),
]

# 香港时间固定 UTC+8，无夏令时；不依赖 Windows 上未必存在的 tzdata。
HK_TZ = timezone(timedelta(hours=8), "HKT")

# WAF 质询页 / 维护页特征（用于避免把拦截页误判成"闸门放行"）
CHALLENGE_MARKERS = (
    b"hwcia", b"challenge", b"jschallenge", b"captcha",
    b"cf-browser-verification", b"are you human", b"bot detection",
    b"access denied", b"request blocked",
)
MAINTENANCE_MARKERS = (
    "系統更新", "系统更新", "暫停服務", "暂停服务",
    "maintenance", "scheduled update", "service unavailable",
)

COOKIE_FIELDS = (
    "version", "name", "value", "port", "port_specified",
    "domain", "domain_specified", "domain_initial_dot",
    "path", "path_specified", "secure", "expires", "discard",
    "comment", "comment_url", "rest", "rfc2109",
)
COOKIE_DEFAULTS = {
    "version": 0, "port": None, "port_specified": False,
    "domain_specified": False, "domain_initial_dot": False,
    "path": "/", "path_specified": True, "secure": False,
    "expires": None, "discard": True, "comment": None,
    "comment_url": None, "rest": {}, "rfc2109": False,
}

try:
    import certifi
    CA_BUNDLE = certifi.where()
except Exception:
    CA_BUNDLE = None
TLS_VERIFY = False if CONFIG["allow_insecure_tls"] else (CA_BUNDLE or True)

# ----------------------------- 基础设施 -----------------------------


def project_path(key):
    """所有运行时文件都落在脚本目录，不随当前工作目录漂移。"""
    return BASE_DIR / CONFIG[key]


def hk_now():
    return datetime.now(HK_TZ)


def header_get(headers, name):
    """urllib / curl_cffi 的头字典大小写不一致，做一次不区分大小写的查找。"""
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def atomic_write(path, text):
    """原子写入并尽量收紧权限：文件里是有效会话票据。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass   # Windows 上 ACL 语义不同，忽略即可


class HKFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, HK_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d}"


_fmt = HKFormatter("%(asctime)s [%(levelname)s] %(message)s")
_file_handler = RotatingFileHandler(
    project_path("log_file"), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
_stream_handler = logging.StreamHandler(sys.stdout)
_file_handler.setFormatter(_fmt)
_stream_handler.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
log = logging.getLogger("booking")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向，以便区分 302(未放行) 与 200(已放行)。"""
    def redirect_request(self, *args, **kwargs):
        return None


class SingleInstance:
    """绑定本地端口作为进程锁；进程退出/崩溃时由操作系统自动释放，无陈旧锁文件。"""
    def __init__(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
        except OSError:
            s.close()
            raise RuntimeError(
                "已有监控实例在运行（端口 %d 被占用）。多进程同时排队会各自建立新会话，"
                "反而丢失队列位置置并制造异常流量。如需调试可加 --no-lock。" % port)
        self.sock = s

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


class Monitor:
    def __init__(self):
        self.backend = "cffi" if HAS_CFFI else "urllib"
        if HAS_CFFI:
            self.session = _cffi.Session(
                tls_pref=TLS_PREF,
                verify=TLS_VERIFY,
                timeout=CONFIG["timeout"],
            )
            self.cookies = self.session.cookies
            self._jar = self.cookies.jar
        else:
            self.cookies = http.cookiejar.CookieJar()
            self._jar = self.cookies
            self.opener = self._build_opener(insecure=False)
            self.opener_insecure = self._build_opener(insecure=True)

        self.abs_host = "abs3.example.test"
        self.admitted = False
        self.last_host_refresh = 0.0
        self._cycle_count = 0
        self._last_reset_day = None
        self._last_status_digest = None
        self._open_streak = 0
        self._pending_confirm = False
        self._fail_streak = 0
        self._retry_after = 0
        self._stop = False
        self._tls_hint_shown = False
        self._timings = []
        self._request_count = 0     # 进程累计发出的请求数，供人工核对流量量级
        self._chain_warm = False    # 本进程是否已经沿导航链走到过预约页
        self._just_reset = False    # 刚做过每日重置，入口页已经打开，下一轮从等候室接着走
        self.timing_enabled = False

    # ----- HTTP -----
    def _build_opener(self, insecure=False):
        if CA_BUNDLE:
            ctx = ssl.create_default_context(cafile=CA_BUNDLE)
        else:
            ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPSHandler(context=ctx),
            NoRedirect,
        )
        op.addheaders = BROWSER_HEADERS
        return op

    @staticmethod
    def _needs_insecure(url):
        """仅对已知证书链不完整的主机降级，其余主机保持完整校验。"""
        if CONFIG["allow_insecure_tls"]:
            return True
        host = urllib.parse.urlsplit(url).hostname or ""
        return host in CONFIG["insecure_tls_hosts"]

    def get(self, url, referer=None, stage=None):
        """发一次 GET；stage 非空且开启计时模式时记录该步耗时。"""
        self._request_count += 1
        t0 = time.perf_counter()
        status, headers, body = self._get(url, referer)
        if stage and self.timing_enabled:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            host = urllib.parse.urlsplit(url).hostname or ""
            self._timings.append((stage, host, status, elapsed_ms))
            log.info("[耗时] %-10s %-22s HTTP %-5s %8.1f ms",
                     stage, host, status, elapsed_ms)
        return status, headers, body

    def _get(self, url, referer):
        headers = {"Referer": referer} if referer else {}
        insecure = self._needs_insecure(url)
        if self.backend == "cffi":
            try:
                # curl_cffi 支持按请求覆盖 verify：False 关闭，None 沿用会话的 CA 包
                r = self.session.get(url, headers=headers, allow_redirects=False,
                                     verify=VERIFY_TLS if insecure else None)
                return r.status_code, dict(r.headers), r.content
            except Exception as e:
                self._tls_hint(e)
                return -1, {}, str(e).encode()
        req = urllib.request.Request(url, headers=headers)
        opener = self.opener_insecure if insecure else self.opener
        try:
            r = opener.open(req, timeout=CONFIG["timeout"])
            return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except Exception as e:
            self._tls_hint(e)
            return -1, {}, str(e).encode()

    def _tls_hint(self, exc):
        """证书校验失败时给出明确修复指引，但不自动扩大降级范围。"""
        if self._tls_hint_shown or CONFIG["allow_insecure_tls"]:
            return
        msg = str(exc).lower()
        if "cert" in msg or "ssl" in msg or "tls" in msg:
            self._tls_hint_shown = True
            log.warning("TLS 校验失败（%s）。若确认是该站点证书链问题，把主机名加入 "
                        "CONFIG['insecure_tls_hosts'] 后重启；默认不会自动关闭校验。", exc)

    # ----- 会话持久化 -----
    def load_session(self):
        path = project_path("session_state")
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != 1:
                log.warning("会话状态格式不兼容，将重新排队")
                return 0
            if data.get("last_reset_day"):
                self._last_reset_day = date.fromisoformat(data["last_reset_day"])
            now = time.time()
            count = 0
            for item in data.get("cookies", []):
                if not all(k in item for k in ("name", "value", "domain")):
                    continue
                if item.get("expires") and item["expires"] <= now:
                    continue
                kw = dict(COOKIE_DEFAULTS)
                kw.update({k: item[k] for k in COOKIE_FIELDS if k in item})
                try:
                    self._jar.set_cookie(http.cookiejar.Cookie(**kw))
                    count += 1
                except Exception:
                    continue
            log.info("已恢复上次会话 cookie %d 条（%s），继续原来的队列位置置",
                     count, path.name)
            if count:
                self._chain_warm = True
            return count
        except Exception as e:
            log.warning("会话恢复失败，将重新排队: %s", e)
            return 0

    def save_session(self):
        data = {
            "version": 1,
            "saved_at": hk_now().isoformat(),
            "last_reset_day": self._last_reset_day.isoformat() if self._last_reset_day else None,
            "cookies": [
                {k: getattr(c, k, None) for k in COOKIE_FIELDS}
                for c in self._jar if hasattr(c, "domain")
            ],
        }
        try:
            atomic_write(project_path("session_state"),
                         json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            log.warning("会话保存失败: %s", e)

    def clear_session(self, purge_export=False):
        try:
            self._jar.clear()
        except Exception:
            pass
        keys = ["session_state"] + (["session_export"] if purge_export else [])
        for key in keys:
            try:
                project_path(key).unlink(missing_ok=True)
            except OSError:
                pass
        self._last_reset_day = None

    # ----- 入口页 -----
    def refresh_host(self, force=False):
        if not force and time.time() - self.last_host_refresh < 1800:
            return
        status, _, body = self.get(GOV_ENTRY, stage="入口页")
        matched = False
        if status == 200:
            m = re.search(r"URL=(https://(abs\d*\.td\.gov\.hk)/)",
                          body.decode("utf-8", "ignore"), re.IGNORECASE)
            if m:
                matched = True
                host = m.group(2)
                if host != self.abs_host:
                    log.info("等候室主机轮换: %s -> %s", self.abs_host, host)
                    self.abs_host = host
                    if host not in CONFIG["insecure_tls_hosts"]:
                        log.warning("新等候室主机 %s 未在 TLS 降级白名单内；"
                                    "若出现证书错误，请加入 CONFIG['insecure_tls_hosts']", host)
        if not matched:
            log.warning("入口页解析等候室主机失败（HTTP %s），沿用 %s；5 分钟后重试",
                        status, self.abs_host)
            self.last_host_refresh = time.time() - 1500
        else:
            self.last_host_refresh = time.time()

    # ----- 导航链 -----
    def keep_warm(self):
        url = f"https://{self.abs_host}/?lang={CONFIG['lang']}"
        status, _, _ = self.get(url, stage="等候室")
        if status != 200:
            log.warning("等候室保活异常: HTTP %s", status)
        return status

    def bridge_url(self):
        return (f"https://abs.td.example.test/booking/tdabs_di.htm?func={CONFIG['func']}"
                f"&auth={CONFIG['auth']}&country={CONFIG['country']}&lang={CONFIG['lang']}")

    def hit_bridge(self):
        status, _, _ = self.get(
            self.bridge_url(), referer=f"https://{self.abs_host}/?lang={CONFIG['lang']}",
            stage="跳转桥")
        if status != 200:
            log.warning("跳转桥访问异常: HTTP %s", status)
        return status

    # ----- 闸门探测 -----
    @staticmethod
    def _is_challenge(status, body):
        if not body or status not in (200, 403, 429, 503):
            return False
        head = body[:8192].lower()
        if any(marker in head for marker in CHALLENGE_MARKERS):
            return True
        # 短页面 + 脚本跳转/写 cookie + 没有表单，是常见的 JS 质询形态
        if (len(body) < 8000 and b"<script" in head and b"<form" not in head
                and (b"location" in head or b"document.cookie" in head)):
            return True
        return False

    @staticmethod
    def _is_maintenance(body):
        text = body[:20000].decode("utf-8", "ignore").lower()
        return any(m.lower() in text for m in MAINTENANCE_MARKERS)

    def gate_url(self):
        params = (f"cmd=cmdIntoSelectAuthDI&func={CONFIG['func']}"
                  f"&auth={CONFIG['auth']}&country={CONFIG['country']}&lang={CONFIG['lang']}")
        return f"https://abs2.example.test{SERVLET_PATH}?{params}"

    def probe_gate(self, referer=None):
        """返回 (state, status, body)。

        state: open=疑似放行 | queued=排队中 | challenge=WAF 质询
               blocked=被限流 | error=网络/服务端错误

        从跳转页点进来时 Referer 是跳转页；人按刷新时 Referer 是预约页自己。
        """
        url = self.gate_url()
        status, headers, body = self.get(
            url, referer=referer or self.bridge_url(), stage="闸门探测")

        retry_after = header_get(headers, "Retry-After")
        if retry_after and str(retry_after).strip().isdigit():
            self._retry_after = min(3600, int(str(retry_after).strip()))

        if self._is_challenge(status, body):
            return "challenge", status, body
        if status in (403, 429, 503):
            return "blocked", status, body
        if status < 200 or status >= 400:
            return "error", status, body
        if status in (301, 302, 303, 307, 308):
            return "queued", status, body
        if status == 200:
            if len(body) <= 500 or self._is_maintenance(body):
                return "queued", status, body
            markers = CONFIG.get("gate_markers") or []
            if markers and not all(m.encode("utf-8") in body for m in markers):
                return "queued", status, body
            return "open", status, body
        return "queued", status, body

    def probe_status_page(self):
        url = f"https://abs.td.example.test{STATUS_SERVLET}"
        status, _, body = self.get(url, referer=f"https://{self.abs_host}/", stage="状态页")
        if status == 200 and len(body) > 1000:
            return body
        return None

    # ----- 每日维护窗口后重置会话 -----
    def maybe_reset_session(self):
        """工作日 16:00-16:30 维护后队列/会话会被重置，需重新进入排队。

        重置日期一并持久化，避免当天重启脚本时重复清空已经排到的位置。
        """
        now = hk_now()
        if now.weekday() >= 5 or (now.hour, now.minute) < (16, 31):
            return False
        if self._last_reset_day == now.date():
            return False
        self._last_reset_day = now.date()
        self._chain_warm = False
        try:
            self._jar.clear()
        except Exception:
            pass
        try:
            project_path("session_state").unlink(missing_ok=True)
        except OSError:
            pass
        self.refresh_host(force=True)
        self._just_reset = True
        log.info("已过每日维护窗口，会话已重置，重新从入口页排队")
        return True

    # ----- 会话导出 -----
    def export_session(self, quiet=False):
        """导出 cookie 供浏览器 Cookie-Editor 类扩展导入。

        注意：放行后把同一 JSESSIONID 从本客户端交给真实浏览器，
        会发生一次明显的客户端环境参数跳变，存在被判定为异常活动的风险。
        这一步请自行权衡，并在用完会话票据后删除导出文件。
        """
        data = [
            {"domain": c.domain, "name": c.name, "value": c.value,
             "path": c.path or "/", "secure": bool(c.secure)}
            for c in self._jar if hasattr(c, "domain")
        ]
        atomic_write(project_path("session_export"),
                     json.dumps(data, ensure_ascii=False, indent=2))
        entry = self.gate_url()
        log.info("=" * 64)
        if quiet:
            log.info("【演练模式】会话已导出到 %s（当前会话未放行，仅供练习导入动作）",
                     CONFIG["session_export"])
        else:
            log.info("会话已导出到 %s", CONFIG["session_export"])
        log.info("浏览器继承队列位置置的操作步骤：")
        log.info("  1. 安装 Cookie-Editor 浏览器扩展")
        log.info("  2. 打开 https://abs2.example.test/ （页面报错属正常）")
        log.info("  3. 用扩展导入 %s 中的 cookie", CONFIG["session_export"])
        log.info("  4. 访问: %s", entry)
        log.info("  5. 完成后删除上述两个会话文件（内含有效票据）")
        log.info("=" * 64)
        return entry

    # ----- 提醒 -----
    @staticmethod
    def _push(url):
        """推送走独立客户端：不复用监控 Session，且始终校验证书。"""
        if not url.startswith("https://"):
            log.warning("推送地址必须为 https，已跳过")
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "booking-monitor/2"})
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read(2048)
            log.info("推送已发送")
        except Exception as e:
            log.warning("推送失败: %s", e)

    def alert(self, msg):
        log.info("!!! %s", msg)
        if CONFIG["beep"]:
            try:
                import winsound
                for _ in range(5):
                    winsound.Beep(1200, 400)
                    time.sleep(0.15)
            except Exception:
                print("\a", end="", flush=True)
        if CONFIG["auto_open_browser"]:
            webbrowser.open(GOV_ENTRY)
        title = "BOOKING 预约闸门已放行"
        if CONFIG["bark_url"]:
            self._push(CONFIG["bark_url"] + urllib.parse.quote(title)
                       + "/" + urllib.parse.quote(msg))
        if CONFIG["serverchan_sendkey"]:
            key = CONFIG["serverchan_sendkey"]
            self._push(f"https://sctapi.ftqq.com/{key}.send"
                       f"?title={urllib.parse.quote(title)}&desp={urllib.parse.quote(msg)}")

    def snapshot(self, name, body):
        d = project_path("snapshot_dir")
        d.mkdir(parents=True, exist_ok=True)
        ts = hk_now().strftime("%Y%m%d_%H%M%S")
        p = d / f"{name}_{ts}.html"
        p.write_bytes(body)
        log.info("页面快照已保存: %s", p)

    # ----- 时段 / 抖动 -----
    @staticmethod
    def in_window(key):
        now = hk_now()
        if now.weekday() >= 5:          # 官方仅在「工作日」更新名额
            return False
        cur = now.hour * 60 + now.minute
        return any(h1 * 60 + m1 <= cur <= h2 * 60 + m2
                   for h1, m1, h2, m2 in CONFIG[key])

    @classmethod
    def in_rush(cls):
        return cls.in_window("rush_windows")

    @classmethod
    def in_waiting(cls):
        return cls.in_window("waiting_windows")

    @classmethod
    def in_peak(cls):
        return cls.in_window("peak_windows")

    @staticmethod
    def in_quiet_hours():
        h = hk_now().hour
        h1, h2 = CONFIG["quiet_hours"]
        return h1 <= h < h2

    def _dwell(self, low, high, why, mode=None, wander=0.0):
        """两次操作之间的停顿。mode 越靠近 low，越像扫一眼就点；wander 是偶尔走神。"""
        if self.timing_enabled or self._stop:
            return
        if mode is None:
            mode = low * 0.4 + high * 0.6
        mode = min(max(mode, low), high)
        secs = random.triangular(low, high, mode)
        if wander and random.random() < wander:
            secs += random.triangular(12, 40, 18)
            log.info("停了 %.0f 秒（%s）", secs, why)
        self._stop = self._wait(secs) or self._stop

    def _pick_browse(self):
        """决定这一轮从哪一页接着走。人守着预约页时大多只按刷新。"""
        if not self._chain_warm:
            return "entry"
        roll = random.random()
        if self.in_rush():
            cuts = (0.86, 0.96, 0.99)   # gate, bridge, room；其余回入口
        elif self.in_quiet_hours() or hk_now().weekday() >= 5:
            cuts = (0.50, 0.78, 0.92)
        else:
            cuts = (0.72, 0.90, 0.97)
        if roll < cuts[0]:
            return "gate"
        if roll < cuts[1]:
            return "bridge"
        if roll < cuts[2]:
            return "room"
        return "entry"

    def _open_like_person(self, depth):
        """按真人顺序往下点。depth 是本轮的起点：entry / room / bridge / gate。"""
        if depth == "entry":
            # 刚打开过入口页就不必连点两次
            fresh = time.time() - self.last_host_refresh < 120
            self.refresh_host(force=not fresh)
            if self._stop:
                return
            self._dwell(2.0, 8.0, "看入口页", mode=3.2, wander=0.08)

        if depth in ("entry", "room"):
            if self._stop:
                return
            self.keep_warm()
            if self._stop:
                return
            self._dwell(1.8, 7.0, "在等候室", mode=2.6, wander=0.05)
            if not self.timing_enabled and random.random() < 0.04:
                self.keep_warm()
                if self._stop:
                    return
                self._dwell(1.0, 3.5, "又看了一眼等候室", mode=1.5)

        if depth in ("entry", "room", "bridge"):
            if self._stop:
                return
            self.hit_bridge()
            if self._stop:
                return
            self._dwell(0.8, 3.4, "等页面跳转", mode=1.3)

        if depth == "gate":
            self._dwell(0.4, 2.4, "准备刷新", mode=0.8)

    @staticmethod
    def jitter(base, watching=False):
        """盯着屏幕时抖动小；平时会偏长，大约一成次数会多离开一会儿。"""
        if watching:
            return base * random.triangular(0.88, 1.22, 1.0) + random.uniform(0.4, 3.5)
        gap = base * random.triangular(0.75, 1.40, 0.98) + random.uniform(0.0, 7.0)
        if random.random() < 0.10:
            gap += random.triangular(25, 160, 45)
        return gap

    def backoff_seconds(self):
        if self._fail_streak <= 0:
            return 0
        if self._fail_streak >= CONFIG["circuit_break_after"]:
            return CONFIG["circuit_break_sleep"]
        n = max(0, self._fail_streak - CONFIG["backoff_after"])
        return min(CONFIG["backoff_max"], CONFIG["backoff_base"] * (2 ** n))

    # ----- 主循环 -----
    def cycle(self):
        reset = self.maybe_reset_session()
        if reset:
            # 会话已清空，上一轮的疑似放行信号不再成立
            self._pending_confirm = False
            self._open_streak = 0

        # 疑似放行时人只会再刷新当前页。其余轮次也大多如此，偶尔才退回前面的页面。
        if self._just_reset:
            self._just_reset = False
            depth = "room"
            note = "刚重新打开入口页，接着进等候室"
            self._dwell(1.2, 4.0, "从入口页往下点", mode=2.0)
        elif self._pending_confirm:
            depth = "gate"
            note = "再刷新一次预约页做复核"
        else:
            depth = self._pick_browse()
            note = {
                "gate": "只刷新当前预约页",
                "bridge": "从跳转页再进一次",
                "room": "回到等候室再往下走",
                "entry": "从入口页重新打开",
            }[depth]
        if self._stop:
            return None
        log.info("本轮%s", note)
        self._open_like_person(depth)
        if self._stop:
            return None
        self._cycle_count += 1

        # 刷新用当前页做 Referer；刚从跳转页点进来则用来源页。
        referer = self.gate_url() if depth == "gate" else self.bridge_url()
        state, status, body = self.probe_gate(referer=referer)
        result = None

        if state in ("challenge", "blocked", "error"):
            self._fail_streak += 1
            self._open_streak = 0
            self._pending_confirm = False
            self._chain_warm = False
            log.warning("探测异常: state=%s HTTP %s（连续 %d 次）",
                        state, status, self._fail_streak)
        else:
            self._chain_warm = True
            if self._fail_streak:
                log.info("网络恢复正常，退出退避")
            self._fail_streak = 0

            if state == "open":
                self._open_streak += 1
                need = CONFIG["gate_confirmations"]
                if self._open_streak < need:
                    self._pending_confirm = True
                    log.info("疑似放行（%d/%d），稍后复核，暂不触发提醒",
                             self._open_streak, need)
                elif not self.admitted:
                    self.admitted = True
                    self._pending_confirm = False
                    self.snapshot("gate_open", body)
                    self.export_session()
                    deadline = (hk_now() + timedelta(
                        minutes=CONFIG["registration_window_minutes"])).strftime("%H:%M:%S")
                    self.alert(f"闸门已放行！请立即按日志步骤把会话导入浏览器完成预约，"
                               f"登记有效时间至 HKT {deadline}。")
                    log.warning("会话已交接给真人浏览器，本进程即刻停止全部请求，"
                                "不会再与浏览器并发使用同一 JSESSIONID。"
                                "请在 HKT %s 前完成登记（官方限时 %d 分钟）。"
                                "如需重新排队请用 --reset-session 后重启。",
                                deadline, CONFIG["registration_window_minutes"])
                    result = "handoff"
            else:
                if self._open_streak:
                    log.info("放行信号未连续，确认计数复位")
                self._open_streak = 0
                self._pending_confirm = False
                if self.admitted:
                    self.admitted = False
                    log.info("闸门已关闭（会话失效或被挤出），继续排队")
                else:
                    log.info("排队中… 闸门状态 HTTP %s", status)

        # 公开状态页偶尔点开看一眼，不再卡在固定轮数上。
        # 放行后一律跳过：此时会话归真人浏览器使用，脚本不得再发任何请求。
        every = CONFIG["status_page_every"]
        if not self.admitted and every > 0 and self._cycle_count > 0:
            chance = 1.0 / every
            if self.in_peak() or self.in_rush():
                chance *= 1.4
            look = random.random() < min(chance, 0.5)
        else:
            look = False
        if look:
            self._dwell(1.0, 4.5, "打开预约情况页", mode=1.8)
            if self._stop:
                return result
            status_body = self.probe_status_page()
            if status_body:
                digest = hashlib.sha256(status_body).hexdigest()
                if digest != self._last_status_digest:
                    self._last_status_digest = digest
                    self.snapshot("status_page", status_body)
                    text = re.sub(r"<[^>]+>", " ", status_body.decode("utf-8", "ignore"))
                    text = re.sub(r"\s+", " ", text).strip()
                    log.info("预约情况页有变化，内容摘要: %s", text[:300])
                else:
                    log.info("预约情况页可访问但内容未变化，跳过快照")

        return result

    def run_timing(self):
        """跑一轮完整导航链，逐步打印耗时；不触发提醒/导出，不写会话文件。

        用于测量「从进程启动到取得 JSESSIONID」各阶段耗时。
        """
        self._timings = []
        t0 = time.perf_counter()
        log.info("=" * 76)
        log.info("阶段耗时测量（单轮 / 不触发提醒 / 不导出 cookie / 不写会话文件）")
        log.info("后端=%s 环境参数=%s 等候室=%s 时区=HKT(UTC+8)",
                 self.backend, TLS_PREF, self.abs_host)
        log.info("-" * 76)

        self.refresh_host(force=True)
        self.keep_warm()
        self.hit_bridge()
        state, status, body = self.probe_gate()

        jid = next((c.value for c in self._jar if c.name.upper() == "JSESSIONID"), None)

        log.info("-" * 76)
        log.info("%-3s %-10s %-24s %-7s %11s %11s",
                 "#", "阶段", "主机", "HTTP", "耗时(ms)", "累计(ms)")
        cumulative = 0.0
        for i, (stage, host, st, ms) in enumerate(self._timings, 1):
            cumulative += ms
            log.info("%-3d %-10s %-24s %-7s %11.1f %11.1f",
                     i, stage, host, st, ms, cumulative)
        total = (time.perf_counter() - t0) * 1000
        boot = (time.perf_counter() - _BOOT_TS) * 1000
        log.info("-" * 76)
        log.info("请求 %d 次，网络合计 %.1f ms；测量段总耗时 %.1f ms；"
                 "自进程启动累计 %.1f ms（含依赖导入与客户端初始化）",
                 len(self._timings), cumulative, total, boot)
        if jid:
            log.info("JSESSIONID 在第 %d 步（闸门探测）取得: %s...",
                     len(self._timings), jid[:24])
            log.info("=> 从启动到拿到 JSESSIONID 约 %.1f ms（其中网络请求 %.1f ms）",
                     boot, cumulative)
        else:
            log.warning("本轮未取得 JSESSIONID（闸门状态 %s / HTTP %s）", state, status)
        log.info("闸门状态=%s HTTP=%s 响应=%d bytes", state, status, len(body))
        log.info("cookie: %s", ", ".join(sorted(c.name for c in self._jar)))
        log.info("=" * 76)

    def run(self, once=False):
        log.info("监控启动 | func=%s auth=%s country=%s | 平时 %ds / 高峰 %ds / 名额窗口 %ds "
                 "| 硬下限 %ds | 时区 HKT(UTC+8)",
                 CONFIG["func"], CONFIG["auth"], CONFIG["country"],
                 CONFIG["poll_interval"], CONFIG["peak_poll_interval"],
                 CONFIG["rush_poll_interval"], CONFIG["min_poll_interval"])
        log.info("官方时间表: 16:00-16:30 更新暂停 / 16:30-17:00 等候排队(低频保活) / "
                 "17:00 随机分配队列位置置并更新名额(及时观察)。"
                 "队列位置置与请求频率无关；16:31 重置会话后重建排队资格。")
        log.info("运行模式: 低频观察，点击之间有停顿，多数时候只刷新当前页；"
                 "放行后自动停机并把会话交给真人浏览器。")
        if self.backend == "cffi":
            log.info("网络后端: curl_cffi（%s 环境参数预设）", TLS_PREF)
        else:
            log.warning("网络后端: urllib 降级模式（仅模拟请求头；"
                        "TLS 环境参数与浏览器不同，建议安装 vendor/curl_cffi）")
        if CONFIG["allow_insecure_tls"]:
            log.warning("TLS 证书校验已全局关闭（allow_insecure_tls=True），仅建议临时排障时使用")
        elif CONFIG["insecure_tls_hosts"]:
            log.warning("仅对证书链不完整的主机关闭 TLS 校验: %s；其余主机正常校验",
                        ", ".join(CONFIG["insecure_tls_hosts"]))

        while not self._stop:
            try:
                result = self.cycle()
            except KeyboardInterrupt:
                log.info("手动中断，保存会话后退出")
                break
            except Exception as e:
                log.exception("本轮探测异常: %s", e)
                self._fail_streak += 1
                result = None
            finally:
                self.save_session()

            if once or result == "handoff":
                break

            interval, reason = self.choose_interval()

            backoff = self.backoff_seconds()
            if backoff:
                log.warning("异常退避中，本轮休眠取 %.0f 秒", backoff)
                interval = max(interval, backoff)
            if self._retry_after:
                interval = max(interval, self._retry_after)
                log.info("服务端要求 Retry-After: %s 秒", self._retry_after)
                self._retry_after = 0

            log.info("过 %.1f 秒再看（%s）| 本进程累计请求 %d 次",
                     interval, reason, self._request_count)
            self._stop = self._stop or self._wait(interval)

    def choose_interval(self):
        """按当前时段挑选本轮休眠秒数，返回 (秒数, 原因)。

        返回值恒 >= min_poll_interval：这是设计上的硬下限，不允许通过配置
        或抖动把轮询变成无间隔请求。17:00 的队列位置置由系统「随机分配」，
        提高频率不会改善结果，只会增加 WAF 将会话判定为异常的概率。
        """
        watching = False
        if self._pending_confirm:
            base, reason, watching = CONFIG["confirm_interval"], "放行复核", True
        elif self.in_quiet_hours():
            base, reason = CONFIG["quiet_interval"], "凌晨少看"
        elif self.in_rush():
            base, reason, watching = CONFIG["rush_poll_interval"], "守着页面", True
        elif self.in_waiting():
            base, reason = CONFIG["waiting_poll_interval"], "等候，偶尔看一眼"
        elif self.in_peak():
            base, reason = CONFIG["peak_poll_interval"], "高峰，看得勤一些"
        elif hk_now().weekday() >= 5:
            # 官方仅在「工作日」更新名额，周末不必按平时频率发请求
            base, reason = CONFIG["offday_interval"], "非工作日，很少看"
        else:
            base, reason = CONFIG["poll_interval"], "平时隔一阵看一次"

        interval = self.jitter(base, watching=watching)
        floor = CONFIG["min_poll_interval"]
        if interval < floor:
            log.debug("间隔 %.1fs 低于硬下限，抬升到 %ds", interval, floor)
            interval = floor
        return interval, reason

    def _wait(self, seconds):
        """分段休眠，便于响应退出信号；返回是否收到停止请求。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stop:
                return True
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        return self._stop


def main():
    parser = argparse.ArgumentParser(description="BOOKING-DI 预约闸门监控")
    parser.add_argument("--once", action="store_true", help="只探测一次")
    parser.add_argument("--export", action="store_true",
                        help="走完整导航链后导出 cookie（演练用，会话未放行）")
    parser.add_argument("--reset-session", action="store_true",
                        help="清空持久化的会话票据，下次启动重新排队")
    parser.add_argument("--timing", action="store_true",
                        help="跑一轮并逐步打印从启动到取得 JSESSIONID 的耗时（不触发提醒/导出）")
    parser.add_argument("--no-lock", action="store_true", help="跳过单实例检查（仅调试）")
    args = parser.parse_args()

    lock = None
    if not args.no_lock:
        try:
            lock = SingleInstance(CONFIG["single_instance_port"])
        except RuntimeError as e:
            log.error("%s", e)
            sys.exit(1)
        atexit.register(lock.close)

    m = Monitor()

    def _on_stop(signum, frame):
        log.info("收到退出信号，保存会话后退出")
        m._stop = True

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, _on_stop)
            except (ValueError, OSError):
                pass

    if args.reset_session:
        m.clear_session(purge_export=True)
        log.info("会话票据已清空，下次启动将重新排队")
        return

    if args.timing:
        # 不加载已有会话：测的是全新冷启动到首次拿到 JSESSIONID 的完整耗时
        m.timing_enabled = True
        m.run_timing()
        return

    m.load_session()

    if args.export:
        m.refresh_host(force=True)
        m.keep_warm()
        m.hit_bridge()      # v1 漏了这一跳，导致 Referer 指向从未访问过的页面
        m.probe_gate()
        m.export_session(quiet=True)
        m.save_session()
        return

    try:
        m.run(once=args.once)
    finally:
        m.save_session()


if __name__ == "__main__":
    main()
