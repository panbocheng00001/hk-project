# -*- coding: utf-8 -*-
"""mcp_server.py —— 把 pagekit 暴露成 MCP 工具面（stdio，零第三方依赖）

定位
----
上游客户端（编辑器里的 MCP 面板、各类 agent host）通过 stdio 上的 JSON-RPC 调用本进程；
本进程持有唯一一个浏览器会话，工具名与 ``js-mcp`` 一类页面工具的习惯保持一致：
``page_open`` / ``page_capture`` / ``page_click`` / ``page_text`` / ``page_digest`` /
``redact_text`` / ``verify_text`` / ``page_close``。

输入输出
--------
    python -m pagekit.mcp_server            # stdin 收请求，stdout 出响应，一行一条
    请求：{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":...,"arguments":{...}}}
    响应：{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}]}}

不变量
------
· stdout 只输出协议帧；任何日志走 stderr，避免把客户端的解析器写坏
· 工具返回值一律是脱敏后的摘要或计数，不返回原始 HTML、不返回 Cookie 值
· 会话是单例：重复 ``page_open`` 复用同一浏览器，换 URL 不换进程
· 未识别的 method 回 -32601，工具内部错误回 ``isError: true``，进程不退出

失败路径
--------
· Playwright 缺失 → 工具返回 ``isError`` 与 ``BrowserUnavailable`` 文本，进程继续服务
· 参数缺失 → ``isError`` 文本说明缺哪个字段
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .browser import BrowserUnavailable, PageSession, PagekitError, SessionConfig, default_run_dir
from .digest import DigestOptions, build_digest, render_markdown, write_digest
from .redact import Redactor, load_terms, scan_leftovers

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "pagekit"
SERVER_VERSION = "0.1.0"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "page_open",
        "description": "渲染一个页面并回一份脱敏结构摘要；同时把原始快照写到本地 runs/pagekit/。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标地址"},
                "name": {"type": "string", "description": "这一页的名字，决定落盘文件名"},
                "headed": {"type": "boolean", "description": "显示窗口"},
                "profile": {"type": "string", "description": "持久化浏览器配置目录"},
                "terms": {"type": "string", "description": "本机词表路径"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "page_capture",
        "description": "对当前页面再抓一份快照与摘要（不点击、不填表）。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "terms": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "page_click",
        "description": "按可见文本或 CSS 选择器点一个元素，点完抓一份摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "name": {"type": "string"},
                "terms": {"type": "string"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "page_text",
        "description": "返回当前页面正文（已脱敏，按行去重后截断）。",
        "inputSchema": {"type": "object", "properties": {"terms": {"type": "string"}}},
    },
    {
        "name": "page_digest",
        "description": "返回最近一页的 Markdown 摘要全文（可直接贴给别的模型）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "redact_text",
        "description": "对任意文本做脱敏，返回替换结果与规则计数。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "terms": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "verify_text",
        "description": "检查一段文本里是否还有未遮干净的残留；有残留时 isError 为真。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "terms": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "page_close",
        "description": "关闭浏览器会话（下次调用会自动重开）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class Server:
    """一个进程一个会话；工具之间共享浏览器与最近一次的摘要。"""

    def __init__(self) -> None:
        self.session: Optional[PageSession] = None
        self.run_dir: Path = default_run_dir()
        self.last_digest: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 基础
    def _redactor(self, terms: Optional[str]) -> Redactor:
        return Redactor(load_terms(Path(terms) if terms else None))

    def _ensure_session(self, headed: bool = False, profile: Optional[str] = None) -> PageSession:
        if self.session and self.session.page:
            return self.session
        self.run_dir.mkdir(parents=True, exist_ok=True)
        session = PageSession(
            run_dir=self.run_dir,
            config=SessionConfig(headless=not headed),
            profile_dir=Path(profile) if profile else None,
        )
        session.start()
        self.session = session
        return session

    def _digest(self, snapshot: Dict[str, Any], terms: Optional[str]) -> Dict[str, Any]:
        digest = build_digest(snapshot, self._redactor(terms),
                              DigestOptions(phase=str(snapshot.get("phase") or "page")))
        write_digest(digest, self.run_dir / f"{snapshot.get('phase', 'page')}.digest.json")
        self.last_digest = digest
        return digest

    # ------------------------------------------------------------ 工具
    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name == "page_open":
            url = args.get("url")
            if not url:
                return _error("缺少 url")
            session = self._ensure_session(bool(args.get("headed")), args.get("profile"))
            info = session.open(str(url))
            snapshot = session.capture(str(args.get("name") or "home"), status=info["status"])
            digest = self._digest(snapshot, args.get("terms"))
            return _ok({"status": info["status"], "url": digest["meta"]["host"],
                        "stats": digest["stats"], "run_dir": str(self.run_dir),
                        "digest_md": render_markdown(digest)})

        if name == "page_capture":
            session = self.session
            if not session or not session.page:
                return _error("还没有打开的页面；先调 page_open")
            snapshot = session.capture(str(args.get("name") or "page"))
            digest = self._digest(snapshot, args.get("terms"))
            return _ok({"phase": digest["phase"], "stats": digest["stats"],
                        "digest_md": render_markdown(digest)})

        if name == "page_click":
            target = args.get("target")
            if not target:
                return _error("缺少 target")
            session = self.session
            if not session or not session.page:
                return _error("还没有打开的页面；先调 page_open")
            info = session.click(str(target))
            phase = str(args.get("name") or f"after-{target}")
            snapshot = session.capture(phase, status=info.get("status", ""))
            digest = self._digest(snapshot, args.get("terms"))
            return _ok({"clicked": target, "url": digest["meta"]["host"],
                        "stats": digest["stats"], "digest_md": render_markdown(digest)})

        if name == "page_text":
            session = self.session
            if not session or not session.page:
                return _error("还没有打开的页面；先调 page_open")
            result = self._redactor(args.get("terms")).redact(session.text())
            return _ok({"text": result.text,
                        "redactions": {hit.rule: hit.count for hit in result.hits}})

        if name == "page_digest":
            if not self.last_digest:
                return _error("还没有摘要；先调 page_open")
            return _ok({"digest_md": render_markdown(self.last_digest)})

        if name == "redact_text":
            text = args.get("text")
            if text is None:
                return _error("缺少 text")
            result = self._redactor(args.get("terms")).redact(str(text))
            return _ok({"text": result.text,
                        "redactions": {hit.rule: hit.count for hit in result.hits}})

        if name == "verify_text":
            text = args.get("text")
            if text is None:
                return _error("缺少 text")
            terms = load_terms(Path(args["terms"]) if args.get("terms") else None)
            leftovers = scan_leftovers(str(text), terms)
            payload = {"count": len(leftovers),
                       "leftovers": [{"rule": item.rule, "line": item.line, "length": item.length}
                                     for item in leftovers]}
            if leftovers:
                return _error(json.dumps(payload, ensure_ascii=False))
            return _ok(payload)

        if name == "page_close":
            if self.session:
                self.session.close()
                self.session = None
            return _ok({"closed": True})

        return _error(f"未知工具：{name}")


def _ok(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def handle(server: Server, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 请求；通知类消息返回 None（不写响应）。"""
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            result = server.call(name, args if isinstance(args, dict) else {})
        except BrowserUnavailable as exc:
            result = _error(f"BrowserUnavailable: {exc}")
        except PagekitError as exc:
            result = _error(f"PagekitError: {exc}")
        except Exception as exc:  # 工具内部错误不应带崩进程
            result = _error(f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    server = Server()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    print(f"[pagekit] mcp server ready, run_dir={server.run_dir}", file=sys.stderr, flush=True)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print("[pagekit] 收到非 JSON 行，已忽略", file=sys.stderr, flush=True)
            continue
        response = handle(server, message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    if server.session:
        server.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
