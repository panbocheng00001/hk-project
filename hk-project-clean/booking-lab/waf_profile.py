# -*- coding: utf-8 -*-
"""waf_profile.py —— 浏览器身份与请求头画像的唯一出口

定位
----
把四样东西收在一处，其它模块只从这里取：

    · Chrome 主版本号（决定 UA / sec-ch-ua / TLS 画像名）
    · 三类请求（文档导航、JSON 接口、表单 POST）的头部集合与物理顺序
    · curl_cffi 的画像关键字（经 tls_profile.tls_kwargs 转义）
    · 身份自洽检查（UA ↔ sec-ch-ua ↔ TLS 画像 ↔ locale/timezone）

    http_flow.py  纯 HTTP 全流程
    session_pool.py  票据池消费端
    site_monitor.py  urllib 降级探测

输入输出
--------
    profile()                          -> ChromeProfile
    from_user_agent(ua)                -> ChromeProfile
    header_plan("document", p, ...)    -> [(name, value), ...]
    session_kwargs(p)                  -> {"impersonate": "chrome136"} 或 {}
    audit(p, tls_name=..., ua=...)     -> [问题描述, ...]（空列表 = 自洽）

不变量
------
    1. 同一个 ChromeProfile 生成的 UA、sec-ch-ua、TLS 画像名主版本号相同。
    2. header_plan 的顺序只由 kind 与入参决定，不含随机项。
    3. 纯函数：不联网、不读时钟、不 sleep、不改调用方全局。

失败路径
--------
    主版本号解析不出来时回落到 DEFAULT_MAJOR，不抛异常。
    kind 不认识时抛 ValueError；画像名不被 curl_cffi 支持时 session_kwargs 返回 {},
    由 audit() 给出「画像不受支持」这一条，调用方打日志后继续用库默认栈。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from tls_profile import tls_kwargs

# curl_cffi 0.13 内置画像的最高主版本是 136（typing 里可枚举）。
# 这里不追 UA 上更大的数字：UA 与 TLS 画像主版本必须能互相对上，对不上就是自相矛盾。
DEFAULT_MAJOR = 136

# 与「不指定画像」等价的取值，和 tls_profile._OFF_VALUES 保持同一语义。
_OFF_VALUES = {"", "none", "off", "0", "default"}

UA_TEMPLATE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")

DOC_ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7")
IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"

_ACCEPT_LANGUAGE = {
    "zh-CN": "zh-CN,zh;q=0.9,en;q=0.8",
    "zh-HK": "zh-HK,zh;q=0.9,en;q=0.8",
    "en-US": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class ChromeProfile:
    """一个 Chrome 身份：主版本 + 平台 + 语言/时区。

    locale 与 timezone 必须和出口 IP 的归属地对得上（见 docs/WAF_ALIGNMENT.md）。
    """

    major: int = DEFAULT_MAJOR
    platform: str = "Windows"
    platform_ua: str = "Windows NT 10.0; Win64; x64"
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"

    @property
    def major_text(self) -> str:
        """主版本号的字符串形态，用于 UA 与客户端提示里。"""
        return str(int(self.major))

    @property
    def ua(self) -> str:
        """与该主版本号一致的 User-Agent。"""
        return UA_TEMPLATE.format(major=self.major_text)

    @property
    def tls_name(self) -> str:
        """curl_cffi 的画像名；主版本号与 UA 同源。"""
        return f"chrome{self.major_text}"

    @property
    def sec_ch_ua(self) -> str:
        """Sec-CH-UA；版本号与 UA 同源，GREASE 项固定在末位。"""
        m = self.major_text
        return f'"Google Chrome";v="{m}", "Chromium";v="{m}", "Not_A Brand";v="24"'

    @property
    def accept_language(self) -> str:
        """与 locale 对应的 Accept-Language（q 值顺序固定）。"""
        return accept_language(self.locale)

    def describe(self) -> Dict[str, Any]:
        return {"major": self.major, "platform": self.platform,
                "locale": self.locale, "timezone": self.timezone,
                "ua": self.ua, "tls": self.tls_name}


def _as_major(value: Any) -> int:
    """把 "156" / "Mozilla/...Chrome/156.0.0.0..." / 156 归一到整数主版本。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else DEFAULT_MAJOR
    text = str(value or "")
    if "Chrome/" in text:
        text = text.split("Chrome/", 1)[1]
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return DEFAULT_MAJOR
    major = int(digits)
    return major if 60 <= major <= 300 else DEFAULT_MAJOR


def profile(major: Any = None, *, locale: str = "", timezone: str = "") -> ChromeProfile:
    """构造画像；major 为空时依次取 BOOKING_CHROME_MAJOR、DEFAULT_MAJOR。"""
    raw = major if major not in (None, "") else os.environ.get("BOOKING_CHROME_MAJOR", "")
    return ChromeProfile(
        major=_as_major(raw),
        locale=locale or os.environ.get("BOOKING_LOCALE", "") or "zh-CN",
        timezone=timezone or os.environ.get("BOOKING_TZ", "") or "Asia/Shanghai",
    )


def from_user_agent(ua: str) -> ChromeProfile:
    """从 UA 反推同一主版本的画像；解析不出时回落到默认主版本。"""
    return profile(_as_major(ua))


def accept_language(locale: str) -> str:
    """locale -> Accept-Language；未收录的 locale 按「自身 + 主语言」生成。"""
    loc = (locale or "zh-CN").strip()
    if loc in _ACCEPT_LANGUAGE:
        return _ACCEPT_LANGUAGE[loc]
    primary = loc.split("-")[0] or "en"
    return f"{loc},{primary};q=0.9"


def resolve_tls(explicit: str = "") -> str:
    """解析本次使用的画像名。

    优先级：显式参数 > BOOKING_TLS_PROFILE > 画像默认（chrome<主版本>）。
    显式写了 none/off/0/default 时返回空串，表示不指定画像。
    """
    raw = (explicit or "").strip() or (os.environ.get("BOOKING_TLS_PROFILE", "") or "").strip()
    if not raw:
        return profile().tls_name
    if raw.lower() in _OFF_VALUES:
        return ""
    return raw


def supported_targets() -> frozenset[str]:
    """curl_cffi 内置画像名集合；库缺失或元数据不可读时返回空集。"""
    try:
        from typing import get_args
        from curl_cffi.requests.impersonate import BrowserTypeLiteral
        return frozenset(get_args(BrowserTypeLiteral)) | {"chrome"}
    except Exception:
        return frozenset()


def is_supported(name: str) -> bool:
    """画像名是否被当前 curl_cffi 接受；空串代表「用库默认栈」，永远可用。"""
    if not (name or "").strip():
        return True
    targets = supported_targets()
    if not targets:
        return True          # 读不到元数据时不拦，交给库自己报错
    return name in targets


def session_kwargs(p: ChromeProfile, enabled: bool = True,
                   name: str = "") -> Dict[str, Any]:
    """curl_cffi Session 的画像关键字。

    name 为空时用 p.tls_name；enabled=False 或画像不受支持时返回 {}，
    让调用方拿到库默认栈而不是一个会抛错的构造参数。
    """
    target = (name or p.tls_name) if enabled else ""
    if not is_supported(target):
        return {}
    return tls_kwargs(target)


def _sec_fetch(kind: str, site: str, mode: str, dest: str, user: bool) -> List[Tuple[str, str]]:
    out = [("Sec-Fetch-Site", site), ("Sec-Fetch-Mode", mode)]
    if user:
        out.append(("Sec-Fetch-User", "?1"))
    out.append(("Sec-Fetch-Dest", dest))
    return out


def header_plan(kind: str, p: ChromeProfile, *, referer: str = "",
                origin: str = "", accept: str = "", content_type: str = "",
                site: str = "", cross_site: bool = False) -> List[Tuple[str, str]]:
    """返回一类请求的头部序列（浏览器里从上到下的物理顺序）。

    kind
        document —— 顶层导航 GET（入口页、业务页）
        form     —— 同域/跨域表单 POST
        json     —— 页面内 fetch/XHR
        script   —— 主 JS 资源
        image    —— 图片资源

    Origin 只在跨域时为真（cross_site=True），同域请求不携带。
    """
    plan: List[Tuple[str, str]] = [
        ("sec-ch-ua", p.sec_ch_ua),
        ("sec-ch-ua-mobile", "?0"),
        ("sec-ch-ua-platform", f'"{p.platform}"'),
    ]
    if not accept:
        accept = {"document": DOC_ACCEPT, "form": DOC_ACCEPT,
                  "json": "application/json", "script": "*/*",
                  "image": IMAGE_ACCEPT}.get(kind, "")
    if kind in ("document", "form"):
        plan.append(("Upgrade-Insecure-Requests", "1"))
    if origin and cross_site:
        plan.append(("Origin", origin))
    plan.append(("User-Agent", p.ua))
    if accept:
        plan.append(("Accept", accept))
    if content_type:
        plan.append(("Content-Type", content_type))

    if kind == "document":
        plan += _sec_fetch(kind, site or "none", "navigate", "document", True)
    elif kind == "form":
        plan += _sec_fetch(kind, site or ("cross-site" if cross_site else "same-origin"),
                           "navigate", "document", True)
    elif kind == "json":
        plan += _sec_fetch(kind, site or ("cross-site" if cross_site else "same-origin"),
                           "cors", "empty", False)
    elif kind == "script":
        plan += _sec_fetch(kind, site or "same-origin", "no-cors", "script", False)
    elif kind == "image":
        plan += _sec_fetch(kind, site or "same-origin", "no-cors", "image", False)
    else:
        raise ValueError(f"未知请求类型 {kind!r}，可选 document/form/json/script/image")

    if referer:
        plan.append(("Referer", referer))
    plan.append(("Accept-Language", p.accept_language))
    return plan


def request_headers(kind: str, p: ChromeProfile, **kw: Any) -> Dict[str, str]:
    """header_plan 的字典形态；给 requests 的 headers= 参数用。

    保留插入顺序（Python 3.7+ dict 语义），调用方不要再 sort。
    """
    return dict(header_plan(kind, p, **kw))


def apply_headers(target: Any, plan: Iterable[Tuple[str, str]]) -> None:
    """把头部序列写进 session.headers：先清空，再按顺序逐条写。

    逐条写而不是 update：update 会保留旧键的位置，顺序特征就没了。
    """
    try:
        target.clear()
    except Exception:
        pass
    for name, value in plan:
        if value is None or value == "":
            continue
        try:
            target[name] = value
        except Exception:
            pass


def auth_headers(p: ChromeProfile, *, referer: str = "",
                 accept: str = DOC_ACCEPT) -> List[Tuple[str, str]]:
    """已带票据的会话使用的文档类头部（不含 Origin/Cookie）。"""
    return header_plan("document", p, referer=referer, accept=accept)


def audit(p: ChromeProfile, *, tls_name: str = "", ua: str = "",
          locale: str = "", timezone: str = "", require_tls: bool = True) -> List[str]:
    """身份自洽检查；返回问题列表，空列表表示各维度指向同一个浏览器。

    require_tls=False 时不把「未指定画像」算作问题（票据由浏览器铸出、
    纯 HTTP 只复用 UA 与 Cookie 的场景）。
    只做判断，不改入参，不打日志。
    """
    issues: List[str] = []
    if tls_name:
        if tls_name.startswith("chrome") and tls_name[len("chrome"):].isdigit():
            if int(tls_name[len("chrome"):]) != p.major:
                issues.append(f"TLS 画像 {tls_name} 与 UA 主版本 {p.major} 不一致")
        elif not is_supported(tls_name):
            issues.append(f"TLS 画像 {tls_name} 不在当前 curl_cffi 支持列表里")
    elif require_tls:
        issues.append("未指定 TLS 画像：UA 与传输层栈无法互相印证")
    if ua:
        ua_major = from_user_agent(ua).major
        if ua_major != p.major:
            issues.append(f"UA 主版本 {ua_major} 与画像主版本 {p.major} 不一致")
    if locale and locale != p.locale:
        issues.append(f"locale {locale} 与画像 {p.locale} 不一致")
    if timezone and timezone != p.timezone:
        issues.append(f"timezone {timezone} 与画像 {p.timezone} 不一致")
    return issues
