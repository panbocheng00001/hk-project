# -*- coding: utf-8 -*-
"""cli.py —— pagekit 的命令行入口

定位
----
把「起浏览器 → 抓页面 → 压摘要 → 脱敏 → 落盘」串成四条命令，供人手工用，也供脚本调用。
每次运行都在 ``booking-lab/runs/pagekit/<时间戳>/`` 下留一份可回看的现场。

输入输出
--------
    python -m pagekit open   --url URL [--name home] [--headed] [--keep]
    python -m pagekit walk   --url URL --steps steps.json [--headed]
    python -m pagekit digest --snapshot runs/pagekit/<stamp>/home.snapshot.json
    python -m pagekit redact --in note.txt [--out note.redacted.md]
    python -m pagekit verify --in note.redacted.md

    外发物只有 ``<phase>.digest.md``；其余（``.html`` / ``.png`` / ``.snapshot.json``）留在本地。

不变量
------
· 摘要一律过 ``Redactor``；命令行打印出来的正文片段也是脱敏后的
· 退出码固定：0 成功 / 2 参数错 / 3 有未遮干净的残留 / 4 浏览器不可用 / 5 页面操作失败
· ``walk`` 每一步都先断言「页面已稳定」再抓，抓不到就停在那一步并报错，不跳过

失败路径
--------
浏览器起不来 → 4；点不到元素或抽取失败 → 5；``verify`` 发现残留 → 3。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .browser import (BrowserUnavailable, PageSession, PagekitError, SessionConfig,
                      default_run_dir)
from .digest import DigestOptions, build_digest, render_markdown, write_digest
from .redact import Redactor, load_terms, scan_leftovers

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_RESIDUAL = 3
EXIT_NO_BROWSER = 4
EXIT_PAGE = 5


def _utf8_stdout() -> None:
    """Windows 控制台默认不是 UTF-8，中文摘要会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _session(args: argparse.Namespace, run_dir: Path) -> PageSession:
    config = SessionConfig(headless=not getattr(args, "headed", False),
                           settle_ms=getattr(args, "settle_ms", 1500),
                           channel=getattr(args, "channel", "") or "")
    profile = Path(args.profile) if getattr(args, "profile", None) else None
    return PageSession(run_dir=run_dir, config=config, profile_dir=profile)


def _emit_digest(snapshot: Dict[str, Any], terms_path: Optional[Path],
                 phase: str, run_dir: Path, headless_note: bool = True) -> Dict[str, Any]:
    redactor = Redactor(load_terms(terms_path))
    digest = build_digest(snapshot, redactor, DigestOptions(phase=phase))
    md_path = write_digest(digest, run_dir / f"{_slug(phase)}.digest.json")
    stats = digest["stats"]
    print(f"  摘要：{md_path}")
    print(f"  规模：正文 {stats['text_chars']} 字 / 字段 {stats['fields']} / "
          f"按钮 {stats['buttons']} / 链接 {stats['links']} / 请求 {stats['requests']}")
    print(f"  脱敏：{_fmt_hits(stats['redactions'])}")
    for note in digest.get("notes") or []:
        print(f"  备注：{note}")
    if headless_note and not digest["meta"].get("title") and stats["text_chars"] < 40:
        print("  提示：标题与正文都是空的，页面可能仍在渲染；加 --settle-ms 3000 再试")
    return digest


def _fmt_hits(hits: Dict[str, int]) -> str:
    if not hits:
        return "无命中"
    return "，".join(f"{name}×{count}" for name, count in hits.items())


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text) or "page"


# --------------------------------------------------------------------------- 命令
def cmd_open(args: argparse.Namespace) -> int:
    run_dir = Path(args.out).resolve() if args.out else default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"运行目录：{run_dir}")
    session = _session(args, run_dir)
    try:
        session.start()
    except BrowserUnavailable as exc:
        print(f"浏览器不可用：{exc}", file=sys.stderr)
        return EXIT_NO_BROWSER

    try:
        info = session.open(args.url)
        print(f"导航：{info['status']} {info['url']}（{info['elapsed_ms']}ms）")
        snapshot = session.capture(args.name, status=info["status"])
        _emit_digest(snapshot, Path(args.terms) if args.terms else None, args.name, run_dir)
        if args.keep:
            print("浏览器保持打开；回车结束会话。")
            input()
        if args.hold is not None:
            print(f"会话结束原因：{_hold(session, args.hold)}")
    except PagekitError as exc:
        print(f"页面操作失败：{exc}", file=sys.stderr)
        return EXIT_PAGE
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        session.close()
    return EXIT_OK


def _hold(session: PageSession, seconds: int) -> str:
    """把浏览器留在前台：``--hold 0`` 表示一直留到进程被中断。

    用途是「把站点跑起来」：窗口给操作者手动继续，进程负责保活。
    返回结束原因（时间到 / Ctrl+C / 窗口被关掉），由调用方打印——不允许静默退出，
    否则下一次看到「进程没了」时无法判断是窗口被关还是程序出错。
    """
    print(f"浏览器保持打开（{'直到中断' if seconds <= 0 else f'{seconds}s'}）；Ctrl+C 结束。")
    try:
        if seconds > 0:
            session.page.wait_for_timeout(seconds * 1000)
            return f"保持 {seconds}s 已到"
        while True:
            session.page.wait_for_timeout(3000)
    except KeyboardInterrupt:
        return "收到 Ctrl+C"
    except Exception as exc:
        print(f"窗口在保持期间不可用：{type(exc).__name__}（浏览器退出或页面被关闭）")
        return "窗口被关闭"
    return "循环退出"


def cmd_walk(args: argparse.Namespace) -> int:
    steps_path = Path(args.steps)
    if not steps_path.exists():
        print(f"步骤文件不存在：{steps_path}", file=sys.stderr)
        return EXIT_ARGS
    steps = json.loads(steps_path.read_text(encoding="utf-8"))
    if isinstance(steps, dict):
        steps = steps.get("steps", [])

    run_dir = Path(args.out).resolve() if args.out else default_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"运行目录：{run_dir}")

    session = _session(args, run_dir)
    try:
        session.start()
    except BrowserUnavailable as exc:
        print(f"浏览器不可用：{exc}", file=sys.stderr)
        return EXIT_NO_BROWSER

    index: List[Dict[str, Any]] = []
    ok = True
    try:
        info = session.open(args.url)
        print(f"导航：{info['status']} {info['url']}（{info['elapsed_ms']}ms）")
        snapshot = session.capture(args.name, status=info["status"])
        digest = _emit_digest(snapshot, Path(args.terms) if args.terms else None,
                              args.name, run_dir)
        index.append({"phase": args.name, "url": digest["meta"]["host"],
                      "digest": str(run_dir / f"{_slug(args.name)}.digest.md")})

        for position, step in enumerate(steps, start=1):
            phase = str(step.get("snapshot") or "")
            action = "click" if "click" in step else "goto" if "goto" in step \
                else "fill" if "fill" in step else "check" if "check" in step \
                else "wait_for" if "wait_for" in step else "snapshot" if phase else ""

            if "wait" in step:
                session.page.wait_for_timeout(int(step["wait"]))
                continue
            if "wait_for" in step:
                selector = str(step["wait_for"])
                if not selector.startswith(("css=", "css:")):
                    selector = "css=" + selector
                print(f"步骤 {position}：等元素 {selector}")
                try:
                    session.page.wait_for_selector(
                        selector[4:], state="visible", timeout=180000)
                    session.settle()
                except Exception as exc:
                    print(f"  等元素超时：{type(exc).__name__}（排队未放行或页面已变）",
                          file=sys.stderr)
            if action == "check":
                target = str(step["check"])
                print(f"步骤 {position}：勾选 {target}")
                locator = (session.page.locator(target[4:])
                           if target.startswith(("css=", "css:"))
                           else session.page.locator(target))
                try:
                    locator.first.check(timeout=session.config.timeout_ms)
                    session.settle()
                except Exception as exc:
                    print(f"  勾选失败：{type(exc).__name__}：{exc}", file=sys.stderr)
            elif action == "wait_for":
                print(f"步骤 {position}：抓取当前页 {phase or 'wait_for'}")
            elif action == "click":
                target = str(step["click"])
                print(f"步骤 {position}：点击 {target}")
                session.click(target)
            elif action == "goto":
                print(f"步骤 {position}：跳转 {step['goto']}")
                session.open(str(step["goto"]))
            elif action == "fill":
                spec = step["fill"]
                print(f"步骤 {position}：填 {spec.get('target')}")
                session.fill(str(spec.get("target")), str(spec.get("value", "")))
            elif action == "snapshot":
                print(f"步骤 {position}：抓取当前页 {phase}")
            else:
                print(f"步骤 {position}：未知动作，跳过 {sorted(step)}", file=sys.stderr)
                continue

            phase = phase or f"{position:02d}-{_slug(step.get('click', 'step'))}"
            snapshot = session.capture(phase)
            digest = _emit_digest(snapshot, Path(args.terms) if args.terms else None,
                                  phase, run_dir)
            index.append({"phase": phase, "url": digest["meta"]["host"],
                          "digest": str(run_dir / f"{_slug(phase)}.digest.md")})
    except PagekitError as exc:
        print(f"页面操作失败：{exc}", file=sys.stderr)
        ok = False
    except KeyboardInterrupt:
        ok = False
    finally:
        session.close()
        if index:
            lines = ["# 本次抓取", ""]
            lines += [f"- `{item['phase']}` → {item['url']} → `{Path(item['digest']).name}`"
                      for item in index]
            (run_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"索引：{run_dir / 'index.md'}")
    return EXIT_OK if ok else EXIT_PAGE


def cmd_digest(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"快照不存在：{snapshot_path}", file=sys.stderr)
        return EXIT_ARGS
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    phase = args.phase or str(snapshot.get("phase") or snapshot_path.stem)
    digest = build_digest(snapshot, Redactor(load_terms(Path(args.terms) if args.terms else None)),
                          DigestOptions(phase=phase))
    out_path = Path(args.out) / f"{_slug(phase)}.digest.json" if args.out else \
        snapshot_path.with_name(f"{_slug(phase)}.digest.json")
    md_path = write_digest(digest, out_path)
    print(f"摘要：{md_path}")
    print(f"脱敏：{_fmt_hits(digest['stats']['redactions'])}")
    return EXIT_OK


def cmd_redact(args: argparse.Namespace) -> int:
    src = Path(args.infile)
    if not src.exists():
        print(f"输入不存在：{src}", file=sys.stderr)
        return EXIT_ARGS
    redactor = Redactor(load_terms(Path(args.terms) if args.terms else None))
    result = redactor.redact(src.read_text(encoding="utf-8", errors="replace"))
    dst = Path(args.out) if args.out else src.with_suffix(".redacted.txt")
    dst.write_text(result.text, encoding="utf-8")
    print(f"已写出：{dst}")
    print(f"脱敏：{_fmt_hits({h.rule: h.count for h in result.hits})}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    src = Path(args.infile)
    if not src.exists():
        print(f"输入不存在：{src}", file=sys.stderr)
        return EXIT_ARGS
    text = src.read_text(encoding="utf-8", errors="replace")
    terms = load_terms(Path(args.terms) if args.terms else None)
    leftovers = scan_leftovers(text, terms)
    if not leftovers:
        print(f"干净：{src} 没有可命中的残留（内置规则 + 词表）")
        return EXIT_OK
    print(f"仍有残留：{len(leftovers)} 处（只报规则与位置，不打印原文）")
    for item in leftovers[:40]:
        print(f"  第 {item.line} 行  [{item.rule}]  长度 {item.length}")
    return EXIT_RESIDUAL


# --------------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pagekit",
        description="渲染页面 → 结构摘要 → 脱敏外发；原始快照只落 runs/pagekit/")
    parser.add_argument("--terms", default=None,
                        help="本机词表路径（默认 PAGEKIT_TERMS 或 ~/.hk-project/pagekit_terms.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--headed", action="store_true", help="显示窗口（调试时用）")
        p.add_argument("--out", default=None, help="运行目录（默认 runs/pagekit/<时间戳>）")
        p.add_argument("--profile", default=None, help="持久化浏览器配置目录（保留 Cookie）")
        p.add_argument("--settle-ms", dest="settle_ms", type=int, default=1500,
                       help="网络静默后再等多少毫秒")
        p.add_argument("--channel", default="", choices=["", "msedge", "chrome"],
                       help="用系统自带浏览器（msedge / chrome），空则用 Playwright 内置 Chromium")

    ops = sub.add_parser("open", help="打开一个页面并抓一份摘要")
    ops.add_argument("--url", required=True)
    ops.add_argument("--name", default="home", help="这一页的名字（决定落盘文件名）")
    ops.add_argument("--keep", action="store_true", help="抓完不关浏览器，回车结束")
    ops.add_argument("--hold", type=int, default=None,
                     help="抓完把浏览器留在前台多少秒；0 表示一直留到 Ctrl+C（不给则抓完就关）")
    add_common(ops)
    ops.set_defaults(func=cmd_open)

    walk = sub.add_parser("walk", help="按步骤文件走完一段流程，每步抓一份摘要")
    walk.add_argument("--url", required=True)
    walk.add_argument("--steps", required=True, help="步骤 JSON（见 pagekit/steps.example.json）")
    walk.add_argument("--name", default="home", help="首屏名字")
    add_common(walk)
    walk.set_defaults(func=cmd_walk)

    dig = sub.add_parser("digest", help="从已保存的快照重建摘要（不启动浏览器）")
    dig.add_argument("--snapshot", required=True)
    dig.add_argument("--phase", default=None)
    dig.add_argument("--out", default=None, help="输出目录（默认与快照同目录）")
    dig.set_defaults(func=cmd_digest)

    red = sub.add_parser("redact", help="脱敏任意文本文件（准备外发给模型前用）")
    red.add_argument("--in", dest="infile", required=True)
    red.add_argument("--out", dest="out", default=None)
    red.set_defaults(func=cmd_redact)

    ver = sub.add_parser("verify", help="检查一份外发物里是否还有残留；有则退出码 3")
    ver.add_argument("--in", dest="infile", required=True)
    ver.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
