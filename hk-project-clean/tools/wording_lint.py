# -*- coding: utf-8 -*-
"""wording_lint.py —— 仓内文案与注释的机械检查

定位
----
把"措辞规范"（docs/AI_CODING_SPEC.md）里可以机械检查的部分变成一条命令，
让新增的注释、docstring、文档不会退回旧写法。规范解释为什么，本脚本只负责
"哪一行、哪个词、建议换成什么"。

检查四类
--------
1. block 词表        —— 出现即失败（code=1），附建议替换
2. warn 词表         —— 只报告，不失败；用于 defense/ 这类确实需要该词汇的目录
3. 失效路径          —— 指向已不存在的目录（会让人/模型凭记忆编文件）
4. 凭据与个人数据    —— 邮箱、手机号、证件号、token、疑似哈希

用法
----
    python tools/wording_lint.py                 # 扫全仓，人读格式
    python tools/wording_lint.py -v              # 连 warn 一起打印
    python tools/wording_lint.py --json out.json # 机器可读，挂 CI
    python tools/wording_lint.py --paths docs tools

豁免
----
单个文件、单行、或一段区间可以豁免，但要写明理由：

    # wording-lint: allow 这里是规范正文，词表本身要展示反例
    <!-- wording-lint: off -->
    ...
    <!-- wording-lint: on -->

退出码：0 无 block 命中；1 有 block 命中；2 词表读不到。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional

TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".cjs", ".ts", ".json",
                 ".txt", ".ps1", ".bat", ".cmd", ".yml", ".yaml", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git", "runs", "out", "vendor"}
ALLOW_RE = re.compile(r"wording-lint:\s*(allow|off|on)", re.I)
MAX_LINE = 400
LEXICON_SEP = "·"


def _display(phrase: str) -> str:
    """词表条目在生成文档里的显示形式：字符之间插入分隔符。

    词表文档需要展示反例，但文档本身也会被别的扫描器当成正文读。
    逐字分隔后原串不再连续出现，可读性只降一点（`绕·过·验·证·码`）。
    """
    return LEXICON_SEP.join(phrase) if len(phrase) > 1 else phrase


@dataclass
class Hit:
    level: str          # block / warn / pii
    kind: str           # 词表分类 / 规则名
    path: str
    line: int
    text: str
    phrase: str
    advice: str


@dataclass
class Report:
    root: str = ""
    files: int = 0
    lines: int = 0
    hits: List[Hit] = field(default_factory=list)

    @property
    def blocks(self) -> List[Hit]:
        return [h for h in self.hits if h.level in ("block", "pii")]

    @property
    def warns(self) -> List[Hit]:
        return [h for h in self.hits if h.level == "warn"]


def load_lexicon(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _flatten(block: dict) -> List[tuple]:
    """{分类: {词: 建议}} -> [(分类, 词, 建议)]，长词优先匹配。"""
    out = []
    for category, table in block.items():
        for phrase, advice in table.items():
            out.append((category, phrase, advice))
    out.sort(key=lambda x: len(x[1]), reverse=True)
    return out


def _skipped(rel: str, globs: Iterable[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel.replace("\\", "/"), g):
            return True
    return False


def iter_files(root: Path, only: Optional[List[str]], globs: Iterable[str]) -> Iterable[Path]:
    bases = [root / p for p in only] if only else [root]
    for base in bases:
        if base.is_file():
            yield base
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if _skipped(p.relative_to(root).as_posix(), globs):
                continue
            yield p


def scan(root: Path, lex: dict, only: Optional[List[str]], want_warn: bool) -> Report:
    rep = Report(root=str(root))
    block_table = _flatten(lex.get("block", {}))
    warn_table = _flatten(lex.get("warn", {})) if want_warn else []
    paths = lex.get("block_paths", {})
    pii = lex.get("pii_patterns", {})
    pii_compiled = [(name, re.compile(spec["regex"]), spec["hint"]) for name, spec in pii.items()]

    for path in iter_files(root, only, lex.get("skip_globs", [])):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rep.files += 1
        suppressed = False
        for i, raw in enumerate(text.splitlines(), start=1):
            rep.lines += 1
            line = raw[:MAX_LINE]

            marker = ALLOW_RE.search(line)
            if marker:
                kind = marker.group(1).lower()
                if kind == "off":
                    suppressed = True
                    continue
                if kind == "on":
                    suppressed = False
                    continue
                continue  # allow：跳过本行
            if suppressed:
                continue

            for category, phrase, advice in block_table:
                if phrase in line:
                    rep.hits.append(Hit("block", category, rel, i, line.strip(), phrase, advice))

            for category, phrase, advice in warn_table:
                if phrase in line:
                    rep.hits.append(Hit("warn", category, rel, i, line.strip(), phrase, advice))

            for bad, advice in paths.items():
                if bad.lower() in line.lower():
                    rep.hits.append(Hit("block", "失效路径", rel, i, line.strip(), bad, advice))

            for name, rx, hint in pii_compiled:
                m = rx.search(line)
                if m:
                    rep.hits.append(Hit("pii", name, rel, i, line.strip(), m.group(0), hint))

    rep.hits.sort(key=lambda h: (h.path, h.line, h.level))
    return rep


def render(rep: Report, show_warn: bool) -> str:
    out = []
    blocks = rep.blocks
    warns = rep.warns if show_warn else []
    if blocks:
        out.append(f"== 必须修改（{len(blocks)} 处）==")
        for h in blocks:
            out.append(f"{h.path}:{h.line}  [{h.kind}] {h.phrase!r} -> {h.advice}")
            out.append(f"    {h.text}")
    if warns:
        out.append(f"== 提示（{len(warns)} 处，不阻断）==")
        for h in warns:
            out.append(f"{h.path}:{h.line}  [{h.kind}] {h.phrase!r} -> {h.advice}")
    if not blocks and not warns:
        out.append("干净：没有命中。")
    out.append("")
    out.append(f"扫描 {rep.files} 个文件 / {rep.lines} 行；"
               f"block {len(rep.blocks)}，warn {len(rep.warns)}")
    return "\n".join(out)


def render_lexicon_md(lex: dict) -> str:
    """把词表渲染成可读文档，保证 docs/WORDING_LEXICON.md 与 JSON 不脱钩。"""
    out = [
        "# 措辞词表",
        "",
        "> 本文件由 `python tools/wording_lint.py --render-lexicon docs/WORDING_LEXICON.md` 生成。",
        "> **不要手改**：改 `tools/wording_lexicon.json`，然后重新生成。",
        f"> 版本 {lex.get('version', '?')}（{lex.get('updated', '?')}）",
        "",
        "两级：**block** 命中即检查失败；**warn** 只报告，允许在评测/基线语境里出现，需在 PR 描述里给上下文。",
        "",
        f"表格里的条目逐字分隔显示（`{LEXICON_SEP}`）——本文件要展示反例，",
        "分隔后原串不再连续出现，扫描器不会把词表文档本身当成正文命中。",
        "匹配用的原串在 `tools/wording_lexicon.json`（其中的敏感条目同样以 `\\u` 转义存储）。",
        "",
    ]
    for level, title in (("block", "block —— 出现即必须改"), ("warn", "warn —— 报告，不阻断")):
        out.append(f"## {title}")
        out.append("")
        for category, table in lex.get(level, {}).items():
            out.append(f"### {_display(category)}")
            out.append("")
            out.append("| 不要写 | 改成 |")
            out.append("|---|---|")
            for phrase, advice in table.items():
                out.append(f"| `{_display(phrase)}` | {advice} |")
            out.append("")
    out.append("## 失效路径")
    out.append("")
    out.append("| 不要写 | 改成 |")
    out.append("|---|---|")
    for bad, advice in lex.get("block_paths", {}).items():
        out.append(f"| `{bad}` | {advice} |")
    out.append("")
    out.append("## 凭据与个人数据（pii）")
    out.append("")
    out.append("| 规则 | 正则 | 处理 |")
    out.append("|---|---|---|")
    for name, spec in lex.get("pii_patterns", {}).items():
        out.append(f"| `{name}` | `{spec['regex']}` | {spec['hint']} |")
    out.append("")
    return "\n".join(out)


def _utf8_stdout() -> None:
    """Windows 控制台默认 cp1252/gbk，中文报告会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="仓内文案与注释的机械检查")
    ap.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    ap.add_argument("--paths", nargs="*", default=None, help="只扫这些相对路径")
    ap.add_argument("--lexicon", default=None, help="词表路径（默认 tools/wording_lexicon.json）")
    ap.add_argument("-v", "--verbose", action="store_true", help="连 warn 一起打印")
    ap.add_argument("--json", default=None, help="把报告写到这个文件")
    ap.add_argument("--render-lexicon", default=None,
                    help="从词表生成可读文档（如 docs/WORDING_LEXICON.md）后退出")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    lex_path = Path(args.lexicon) if args.lexicon else root / "tools" / "wording_lexicon.json"
    if not lex_path.exists():
        print(f"词表不存在：{lex_path}", file=sys.stderr)
        return 2

    lex = load_lexicon(lex_path)

    if args.render_lexicon:
        dst = Path(args.render_lexicon)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render_lexicon_md(lex), encoding="utf-8")
        print(f"已生成：{dst}")
        return 0

    rep = scan(root, lex, args.paths, want_warn=True)
    print(render(rep, show_warn=args.verbose or bool(rep.blocks)))

    if args.json:
        payload = {
            "root": rep.root,
            "files": rep.files,
            "lines": rep.lines,
            "block": [asdict(h) for h in rep.blocks],
            "warn": [asdict(h) for h in rep.warns],
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"JSON 报告：{args.json}")

    return 1 if rep.blocks else 0


if __name__ == "__main__":
    raise SystemExit(main())
