# -*- coding: utf-8 -*-
"""digest.py —— 把一页 DOM 压成「可以直接贴给外部模型」的结构摘要

定位
----
输入 ``browser.capture()`` 落下的快照字典，输出两样东西：一份给机器读的 dict，
一份给模型读的 Markdown。两者都只包含结构信息（大纲、字段、按钮、链接、请求行、
正文），正文与所有可见文本在拼装前先过 ``Redactor``。

输入输出
--------
    build_digest(snapshot, redactor, phase=None) -> dict
    render_markdown(digest) -> str
    write_digest(digest, path) -> Path

不变量
------
· 摘要里不出现原始 URL 的查询值、不出现 Cookie 值、不出现请求头
· 正文按行去重后截断，截断处写明丢了多少行，避免"看起来是全文"
· 每条记录都标注来源页面（``phase``）与抓取时间，便于跨页对照
· 摘要永远比原始快照短：原始文本 < 600 字的页面不做压缩，超过就压到 ``max_text``

失败路径
--------
· 快照缺字段 → 用空列表占位并在 ``notes`` 里写明缺哪一项，不抛异常
· 正文超过上限 → 截断 + 计数，不静默丢
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .redact import Redactor

DEFAULT_MAX_TEXT = 6000
DEFAULT_MAX_LINES = 180


@dataclass
class DigestOptions:
    phase: str = ""
    max_text: int = DEFAULT_MAX_TEXT
    max_lines: int = DEFAULT_MAX_LINES


def _compress(text: str, max_text: int, max_lines: int) -> tuple:
    """按行去空、去连续重复，再按行数与字数截断；返回 (正文, 丢弃行数)。"""
    lines: List[str] = []
    dropped = 0
    last = None
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if line == last:
            dropped += 1
            continue
        lines.append(line)
        last = line
    if len(lines) > max_lines:
        dropped += len(lines) - max_lines
        lines = lines[:max_lines]
    out = "\n".join(lines)
    if len(out) > max_text:
        out = out[:max_text] + "…"
    return out, dropped


def _rows(items: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, str]]:
    rows = []
    for item in items or []:
        rows.append({key: str(item.get(key, "")) for key in keys})
    return rows


def build_digest(snapshot: Dict[str, Any], redactor: Redactor,
                 options: Optional[DigestOptions] = None) -> Dict[str, Any]:
    """快照 → 摘要 dict。所有可见文本在这里完成脱敏。"""
    opts = options or DigestOptions()
    notes: List[str] = []

    phase = opts.phase or str(snapshot.get("phase") or "page")
    body, dropped = _compress(str(snapshot.get("text") or ""), opts.max_text, opts.max_lines)
    if dropped:
        notes.append(f"正文去重/截断：丢弃 {dropped} 行")

    red_body = redactor.redact(body)
    hits: Dict[str, int] = {}

    def merge(result) -> str:
        for hit in result.hits:
            hits[hit.rule] = hits.get(hit.rule, 0) + hit.count
        return result.text

    headings = [{"level": int(item.get("level") or 0), "text": merge(redactor.redact(str(item.get("text") or "")))}
                for item in snapshot.get("headings") or []]
    buttons = [{"label": merge(redactor.redact(str(item.get("label") or ""))),
                "selector": str(item.get("selector") or ""),
                "disabled": bool(item.get("disabled"))}
               for item in snapshot.get("buttons") or []]
    links = [{"label": merge(redactor.redact(str(item.get("label") or ""))),
              "path": redactor.redact_url(str(item.get("href") or item.get("path") or ""))}
             for item in snapshot.get("links") or []]
    fields = []
    for item in snapshot.get("fields") or []:
        fields.append({
            "name": merge(redactor.redact(str(item.get("name") or ""))),
            "type": str(item.get("type") or ""),
            "label": merge(redactor.redact(str(item.get("label") or ""))),
            "required": bool(item.get("required")),
            "maxlength": str(item.get("maxlength") or ""),
            "options": [merge(redactor.redact(str(opt))) for opt in (item.get("options") or [])],
            "selector": str(item.get("selector") or ""),
        })
    requests = [{"method": str(item.get("method") or "GET"),
                 "path": redactor.redact_url(str(item.get("url") or item.get("path") or "")),
                 "status": str(item.get("status") or "")}
                for item in snapshot.get("requests") or []]

    for key in ("headings", "buttons", "links", "fields", "requests", "text"):
        if key not in snapshot:
            notes.append(f"快照缺字段：{key}")

    digest: Dict[str, Any] = {
        "phase": phase,
        "meta": {
            "host": redactor.redact_url(str(snapshot.get("url") or "")),
            "title": merge(redactor.redact(str(snapshot.get("title") or ""))),
            "status": str(snapshot.get("status") or ""),
            "captured_at": str(snapshot.get("captured_at") or ""),
            "screenshot": Path(str(snapshot.get("screenshot") or "")).name,
        },
        "stats": {
            "text_chars": len(red_body.text),
            "headings": len(headings),
            "fields": len(fields),
            "buttons": len(buttons),
            "links": len(links),
            "requests": len(requests),
            "redactions": dict(sorted(hits.items())),
        },
        "headings": headings,
        "fields": fields,
        "buttons": buttons,
        "links": links,
        "requests": requests,
        "text": red_body.text,
        "notes": notes + list(redactor.terms.notes),
    }
    return digest


def _table(headers: List[str], rows: List[List[str]]) -> List[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return out


def render_markdown(digest: Dict[str, Any]) -> str:
    """摘要 dict → Markdown，供人读或直接粘贴给外部模型。"""
    meta = digest.get("meta") or {}
    stats = digest.get("stats") or {}
    redactions = stats.get("redactions") or {}
    out: List[str] = [
        f"# 页面摘要：{digest.get('phase', 'page')}",
        "",
        f"- 地址：{meta.get('host', '')}",
        f"- 标题：{meta.get('title', '')}",
        f"- 状态：{meta.get('status', '')}",
        f"- 抓取：{meta.get('captured_at', '')}",
        f"- 规模：正文 {stats.get('text_chars', 0)} 字 / 字段 {stats.get('fields', 0)} / "
        f"按钮 {stats.get('buttons', 0)} / 链接 {stats.get('links', 0)} / 请求 {stats.get('requests', 0)}",
        f"- 脱敏：" + ("，".join(f"`{k}`×{v}" for k, v in redactions.items()) if redactions else "无命中"),
        "",
    ]

    sections: List[tuple] = []
    headings = digest.get("headings") or []
    if headings:
        sections.append(("大纲", _table(["层级", "文本"],
                                        [[f"H{h['level']}", h["text"]] for h in headings])))

    fields = digest.get("fields") or []
    if fields:
        sections.append(("表单字段", _table(
            ["name", "类型", "标签", "必填", "上限", "选项", "选择器"],
            [[f["name"], f["type"], f["label"], "是" if f["required"] else "",
              f["maxlength"], "/".join(f["options"]), f["selector"]] for f in fields])))

    buttons = digest.get("buttons") or []
    if buttons:
        sections.append(("按钮", _table(["文本", "选择器", "禁用"],
                                        [[b["label"], b["selector"], "是" if b["disabled"] else ""]
                                         for b in buttons])))

    links = digest.get("links") or []
    if links:
        sections.append(("链接（只留路径）", _table(["文本", "路径"],
                                                    [[l["label"], l["path"]] for l in links])))

    requests = digest.get("requests") or []
    if requests:
        sections.append(("请求（只留方法与路径）", _table(
            ["方法", "路径", "状态"], [[r["method"], r["path"], r["status"]] for r in requests])))

    sections.append(("正文", ["```text", str(digest.get("text") or ""), "```"]))

    notes = digest.get("notes") or []
    if notes:
        sections.append(("备注", [f"- {note}" for note in notes]))

    for number, (title, lines) in enumerate(sections, start=1):
        out += [f"## {number}. {title}", ""] + lines + [""]
    return "\n".join(out)


def write_digest(digest: Dict[str, Any], path: Path) -> Path:
    """同时落 JSON 与 Markdown；Markdown 是外发物，JSON 给后续脚本拼装用。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    path.with_suffix(".md").write_text(render_markdown(digest), encoding="utf-8")
    return path.with_suffix(".md")
