# -*- coding: utf-8 -*-
"""redact.py —— 页面文本的占位替换：个人资料与口径词一律换成占位符

定位
----
输入一段页面文本（DOM 正文、字段名、按钮名、请求行、表格单元格），输出一段只保留
结构信息的同义文本，可以直接贴给外部模型做分析。纯函数 + 一个可选的本地词表文件，
不启动浏览器、不发请求。

输入输出
--------
    load_terms(path=None) -> TermTable           # 词表：站点主机、口径词、自定义正则
    Redactor(terms).redact(text) -> Redacted     # .text 是脱敏结果，.hits 是规则计数
    Redactor(terms).redact_obj(obj) -> (obj, hits)
    Redactor(terms).redact_url(url) -> str       # 只留主机占位与路径，丢掉查询值
    scan_leftovers(text, terms) -> List[Leftover]

不变量
------
· 命中记录只写规则名、占位符、次数，绝不写命中原文——日志本身不能成为泄露源
· 同一个词在整份文档里映射到同一个占位符，跨页复用时编号不变
· 内置规则先跑，词表规则后跑；占位符形如 ``<HKID>`` / ``<T3>``，不含数字串，
  所以替换结果不会被后续规则二次匹配，``redact`` 幂等
· 宁可多遮：命中口径偏宽（例如 8 位数字串按电话处理），不追求精确
· ``example.test`` / ``localhost`` 一类占位邮箱不遮，避免测试夹具自遮自

失败路径
--------
· 词表文件不存在、JSON 损坏、单条规则写错 → 记一条 ``notes`` 后继续，不抛异常；
  内置规则永远可用，所以脱敏不会整段跳过
· 正则替换抛 ``re.error``（外部词表写坏）→ 该条规则跳过并记 ``notes``
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- 内置规则
# 顺序有意义：先遮长结构（JWT、Cookie 对、查询串），再遮编号，最后遮数字串。
# 全部按「宁可多遮」取值，占位符里不带数字，保证幂等。
_BUILTIN: Tuple[Tuple[str, Pattern[str], str], ...] = (
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
     "<TOKEN>"),
    ("bearer", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/\-]{16,}", re.I), "Bearer <TOKEN>"),
    ("cookie_pair", re.compile(
        r"\b(HWWAFSESID|HWWAFSESTIME|Queue-it|QueueITAccepted[-A-Za-z0-9]*|"
        r"ASP\.NET_SessionId|JSESSIONID|PHPSESSID|csrftoken|XSRF-TOKEN|connect\.sid)"
        r"\s*=\s*[^;\s,]+", re.I), r"\1=<COOKIE>"),
    ("url_param", re.compile(
        r"([?&](?:token|access_token|refresh_token|key|apikey|api_key|sig|signature|"
        r"auth|session|sid|ticket|secret|code|ref|refno|booking|appointment|hkid|idno)=)"
        r"[^&\s\"'<>]+", re.I), r"\1<PARAM>"),
    ("hkid", re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{6}\([0-9A]\)(?![A-Za-z0-9])"), "<HKID>"),
    ("cn_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "<ID_NO>"),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@(?!example\.(?:test|com|org)\b|localhost\b)"
                         r"[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<EMAIL>"),
    ("intl_phone", re.compile(r"(?<![A-Za-z0-9])\+?86[\s\-]?1[3-9]\d{9}(?![A-Za-z0-9])"), "<PHONE>"),
    # 前后都不许紧贴字母数字：否则 /static/media/xxx.856d7736…jpg 这类资源指纹会被当成电话
    ("hk_phone", re.compile(r"(?<![A-Za-z0-9])(?:\+?852[\s\-]?)?[2-9]\d{3}[\s\-]?\d{4}(?![A-Za-z0-9])"),
     "<PHONE>"),
    ("secret_field", re.compile(r"\b(password|passwd|pwd)\s*[=:]\s*\S+", re.I),
     r"\1=<SECRET>"),
    ("long_hex", re.compile(r"\b[a-f0-9]{32,}\b", re.I), "<HEX>"),
)

DEFAULT_TERMS_PATH = Path.home() / ".hk-project" / "pagekit_terms.json"


# --------------------------------------------------------------------------- 词表
@dataclass
class TermTable:
    """本机词表：主机 → 占位、口径词 → 占位、自定义正则 → 占位。"""

    hosts: List[Tuple[str, str]] = field(default_factory=list)
    literals: List[Tuple[str, str]] = field(default_factory=list)
    patterns: List[Tuple[str, Pattern[str], str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def load_terms(path: Optional[Path] = None) -> TermTable:
    """读本机词表；读不到就返回空表并把原因写进 ``notes``。

    查找顺序：显式参数 → 环境变量 ``PAGEKIT_TERMS`` → ``~/.hk-project/pagekit_terms.json``。
    词表结构见 ``pagekit/terms.example.json``；仓内只放示例，真实站点主机与口径词留在本机。
    """
    table = TermTable()
    if path is None:
        env = os.environ.get("PAGEKIT_TERMS")
        path = Path(env) if env else DEFAULT_TERMS_PATH
    path = Path(path)
    if not path.exists():
        table.notes.append(f"词表不存在：{path}（只用内置规则）")
        return table
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        table.notes.append(f"词表读取失败：{path}：{type(exc).__name__}（只用内置规则）")
        return table

    for item in raw.get("hosts", []):
        host = str(item.get("host", "")).strip()
        if host:
            table.hosts.append((host, str(item.get("placeholder") or "<SITE_HOST>")))
        else:
            table.notes.append("hosts 有一条缺 host，已跳过")

    for item in raw.get("terms", []):
        term = str(item.get("term", ""))
        if not term:
            table.notes.append("terms 有一条缺 term，已跳过")
            continue
        placeholder = str(item.get("placeholder") or "")
        table.literals.append((term, placeholder))
    # 长词优先，避免短词先吃掉长词的一部分（例如「牌照事务处」与「事务处」）
    table.literals.sort(key=lambda pair: len(pair[0]), reverse=True)

    for item in raw.get("patterns", []):
        name = str(item.get("name") or f"term_pattern_{len(table.patterns) + 1}")
        source = str(item.get("pattern") or "")
        placeholder = str(item.get("placeholder") or "<TERM>")
        if not source:
            table.notes.append(f"patterns/{name} 缺 pattern，已跳过")
            continue
        try:
            compiled = re.compile(source, re.I if item.get("ignore_case", True) else 0)
        except re.error as exc:
            table.notes.append(f"patterns/{name} 正则无效（{exc}），已跳过")
            continue
        table.patterns.append((name, compiled, placeholder))
    return table


# --------------------------------------------------------------------------- 结果
@dataclass(frozen=True)
class Hit:
    rule: str
    placeholder: str
    count: int


@dataclass(frozen=True)
class Leftover:
    rule: str
    line: int
    length: int


@dataclass
class Redacted:
    text: str
    hits: List[Hit] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.hits:
            return "无命中"
        return "，".join(f"{h.rule}×{h.count}" for h in self.hits)


class Redactor:
    """把内置规则与词表串成一条脱敏流水线；可复用于整份快照的递归处理。"""

    def __init__(self, terms: Optional[TermTable] = None, *,
                 host_placeholder: str = "<SITE_HOST>") -> None:
        self.terms = terms or TermTable()
        self.host_placeholder = host_placeholder
        self._assign: Dict[str, str] = {}
        self._auto = 0

    # ------------------------------------------------------------ 内部
    def _placeholder_for(self, term: str, declared: str) -> str:
        if term in self._assign:
            return self._assign[term]
        if declared:
            placeholder = declared
        else:
            self._auto += 1
            placeholder = f"<T{self._auto}>"
        self._assign[term] = placeholder
        return placeholder

    def _apply(self, text: str, pattern: Pattern[str], repl: str,
               hits: Dict[str, Tuple[str, int]], name: str, placeholder: str) -> str:
        def _sub(match: re.Match) -> str:
            return match.expand(repl) if "\\" in repl else repl
        try:
            out, count = pattern.subn(_sub, text)
        except re.error:
            return text
        if count:
            prev = hits.get(name)
            hits[name] = (placeholder, (prev[1] if prev else 0) + count)
        return out

    # ------------------------------------------------------------ 对外
    def redact(self, text: str) -> Redacted:
        """脱敏一段文本。返回 ``Redacted``；``hits`` 只含规则名与次数。"""
        if not text:
            return Redacted(text=text or "", notes=list(self.terms.notes))
        hits: Dict[str, Tuple[str, int]] = {}
        out = text

        for host, placeholder in self.terms.hosts:
            try:
                out = re.sub(re.escape(host), placeholder, out, flags=re.I)
            except re.error:
                pass

        for name, pattern, repl in _BUILTIN:
            # 反向引用（r"\1=<COOKIE>"）只用于替换本身；记进 hits 的占位符去掉编号引用
            display = re.sub(r"\\\d", "", repl)
            out = self._apply(out, pattern, repl, hits, name, display)

        for name, pattern, placeholder in self.terms.patterns:
            out = self._apply(out, pattern, placeholder, hits, name, placeholder)

        for term, declared in self.terms.literals:
            placeholder = self._placeholder_for(term, declared)
            try:
                out, count = re.subn(re.escape(term), placeholder, out,
                                     flags=re.I if term.isascii() else 0)
            except re.error:
                continue
            if count:
                # 命中记录的键用占位符而不是词本身：摘要里会打印这些键，键里带上词就等于没遮
                key = f"term:{placeholder}"
                prev = hits.get(key)
                hits[key] = (placeholder, (prev[1] if prev else 0) + count)

        ordered = [Hit(rule=k, placeholder=v[0], count=v[1]) for k, v in sorted(hits.items())]
        return Redacted(text=out, hits=ordered, notes=list(self.terms.notes))

    def redact_obj(self, obj: Any) -> Tuple[Any, List[Hit]]:
        """递归脱敏 dict / list / str；其它类型原样返回。"""
        collected: Dict[str, Hit] = {}

        def walk(node: Any) -> Any:
            if isinstance(node, str):
                res = self.redact(node)
                for hit in res.hits:
                    prev = collected.get(hit.rule)
                    merged = Hit(hit.rule, hit.placeholder, (prev.count if prev else 0) + hit.count)
                    collected[hit.rule] = merged
                return res.text
            if isinstance(node, list):
                return [walk(item) for item in node]
            if isinstance(node, tuple):
                return tuple(walk(item) for item in node)
            if isinstance(node, dict):
                return {key: walk(value) for key, value in node.items()}
            return node

        return walk(obj), list(collected.values())

    def redact_url(self, url: str) -> str:
        """只保留主机与路径：词表里的主机换成占位，查询串只留参数名。

        三类要分开处理，否则会把 ``mailto:`` 拼成 ``mailto://<SITE_HOST>…`` 这种假地址：
        相对路径、非 http(s) 协议（mailto / tel / javascript）、真正的绝对地址。
        未登记的主机原样保留——它们是公开的文档站与厂商站，站点身份那一条由词表负责。
        """
        if not url:
            return ""
        try:
            parts = urlsplit(url)
        except ValueError:
            return self.redact(url).text
        keys = "&".join(sorted({key for key, _ in _pairs(parts.query)}))

        if not parts.scheme and not parts.netloc:
            return (parts.path or "/") + (f"?{keys}" if keys else "")
        if parts.scheme and parts.scheme not in ("http", "https"):
            # mailto:tellme@… / tel:1823 / javascript:void(0)：只留协议与脱敏后的值
            return f"{parts.scheme}:{self.redact(parts.path or '').text}"

        host = parts.hostname or ""
        for real, placeholder in self.terms.hosts:
            if real.lower() == host.lower():
                host = placeholder
                break
        out = f"{parts.scheme}://{host}{parts.path or '/'}" + (f"?{keys}" if keys else "")
        # 兜底：路径里夹带的邮箱/编号仍要过一遍通用规则
        return self.redact(out).text


def _pairs(query: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for chunk in (query or "").split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        out.append((key, value))
    return out


def scan_leftovers(text: str, terms: Optional[TermTable] = None) -> List[Leftover]:
    """检查一段文本里是否还留着没遮干净的东西；空列表表示可以外发。

    只报规则名 / 行号 / 命中长度，不报原文；规则名也不含词本身（否则报告会二次泄露）。
    """
    table = terms or TermTable()
    checks: List[Tuple[str, Pattern[str]]] = [(name, pattern) for name, pattern, _ in _BUILTIN]
    checks.extend((name, pattern) for name, pattern, _ in table.patterns)
    for index, (term, declared) in enumerate(table.literals, start=1):
        label = f"term:{declared}" if declared else f"term#{index}"
        checks.append((label, re.compile(re.escape(term), re.I if term.isascii() else 0)))

    found: List[Leftover] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for name, pattern in checks:
            match = pattern.search(line)
            if match:
                found.append(Leftover(rule=name, line=index, length=len(match.group(0))))
    return found


def redact_text(text: str, terms_path: Optional[Path] = None) -> Redacted:
    """一次调用完成「读词表 + 脱敏」，命令行与 MCP 工具面共用。"""
    return Redactor(load_terms(terms_path)).redact(text)


def mask_middle(value: str) -> str:
    """把一段文本压成「首字符 + *** + 长度」，用于必须在本地留下定位线索的场合。"""
    if not value:
        return ""
    return f"{value[0]}***({len(value)})"


def iter_rule_names() -> Iterable[str]:
    """内置规则名列表；测试与文档用它确认规则集合没被悄悄改小。"""
    return (name for name, _, _ in _BUILTIN)
