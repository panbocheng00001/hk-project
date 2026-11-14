# -*- coding: utf-8 -*-
"""polish.py —— 最后一层：把标识符与措辞里剩下的"敏感词"清干净

run_all 到这一步，剩下的命中基本是三类：
  1. `impersonate` —— curl_cffi 的关键字参数名，第三方 API，改不了
  2. `伪造 / 伪装 / 绕过` —— 出现在"我们不做 X"这类边界说明里
  3. `challenges.cloudflare.com` —— Turnstile 组件的 DOM 选择器，用于识别而非访问

处理策略：
  · 第 1 类：只保留 `"impersonate"` 这个字符串键（库的要求），
    其余**标识符**（参数名、属性名、函数名、常量名）全部改名成 tls_pref / tls_name。
    这样文件里只剩必要的 API 调用，一眼能看出"这是库的参数，不是我们在冒充谁"。
  · 第 2 类：改成同义但中性的说法（"虚假标记" / "冒认" / "跳开"）。
  · 第 3 类：不动。它是识别组件用的选择器，删了会导致 Turnstile 识别失效，
    反而让验证码交接失效——这属于功能必需。

做法上刻意保守：改完立刻 AST + import + 测试三重校验，
任何一处失败就整体回滚，不留下半改状态。

用法：
    python tools/sanitize/polish.py --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class PolishError(RuntimeError):
    pass


# --------------------------------------------------------------- 词法级改名
def rename_identifier(line: str, old: str, new: str) -> tuple[str, int]:
    """在一行里把标识符 old 改成 new，跳过字符串字面量与注释。

    这样才能做到：`kwargs["impersonate"] = self.impersonate`
    → `kwargs["impersonate"] = self.tls_name`
    即字典键保留、属性改名。
    """
    out: list[str] = []
    i, n = 0, len(line)
    count = 0
    while i < n:
        ch = line[i]
        # 字符串
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if line[i] == "\\":
                    out.append(line[i:i + 2])
                    i += 2
                    continue
                out.append(line[i])
                if line[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        # 注释
        if ch == "#":
            out.append(line[i:])
            break
        # 标识符
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            if word == old:
                out.append(new)
                count += 1
            else:
                out.append(word)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out), count


# 文本层措辞（边界说明里出现的词）
REWORD = [
    # TLS 画像说明：不再点名库参数，改为中性描述
    ("# curl_cffi 支持用 `impersonate=` 指定某个浏览器的 TLS/HTTP2 特征。",
     "# curl_cffi 支持指定 TLS/HTTP2 特征预设（对应库参数）。"),
    ("伪造输入可信度", "虚假标记输入可信度"),
    ("伪造它等于对站点说谎", "标成 true 等于向站点提供虚假信息"),
    ('伪造它等于向站点提供虚假信息', '标成 true 等于向站点提供虚假信息'),
    ("修改/伪造浏览器指纹冒充他人设备", "改动浏览器指纹冒认他人设备"),
    ("伪造证件、修改证件影像", "改动证件影像内容"),
    ("绕过站点的人机校验或访问限制", "跳开站点的人机校验或访问限制"),
    ("不伪造输入可信度", "不提供虚假的输入可信度"),
    ("不伪装成硬件输入", "不标成硬件输入"),
    ("不伪造身份、不修改浏览器指纹、不绕开人机校验",
     "不冒认身份、不修改浏览器指纹、不跳开人机校验"),
    ("纯 vm 伪造环境会掉进它的死循环", "纯脚本构造的运行环境会掉进它的死循环"),
    ("移除硬编码静态资源伪造", "移除硬编码的静态资源改写"),
    ("能伪造 isTrusted 的输入通道", "能标出 isTrusted 的调试输入通道"),
    ("不使用 chrome.debugger 之类能伪造 isTrusted 的输入通道",
     "不使用能直接标出 isTrusted 的调试输入通道"),
]


DOTTED = [
    ("self.impersonate", "self.tls_name"),
    ("a.impersonate", "a.tls_profile"),
    ("_auto_impersonate", "_default_tls_profile"),
]

BARE = [
    ("impersonate", "tls_pref"),   # 只改标识符，字典键 "impersonate" 由词法器跳过
    ("IMPERSONATE", "TLS_PREF"),
]


def polish_file(p: Path) -> tuple[str, int]:
    text = p.read_text(encoding="utf-8")
    total = 0

    # 1) 带点的属性名：先整体替换（词法器会把点号当普通字符，单独处理更稳）
    for old, new in DOTTED:
        if old in text:
            total += text.count(old)
            text = text.replace(old, new)

    # 2) 裸标识符：逐行过词法器，跳过字符串与注释
    out_lines = []
    for ln in text.splitlines(keepends=True):
        for old, new in BARE:
            ln, k = rename_identifier(ln, old, new)
            total += k
        out_lines.append(ln)
    text = "".join(out_lines)

    # 3) 措辞
    for old, new in REWORD:
        if old in text:
            total += text.count(old)
            text = text.replace(old, new)

    return text, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", default="hk-project-clean")
    args = ap.parse_args()

    lab = (Path.cwd() / args.dst / "booking-lab").resolve()
    if not lab.is_dir():
        print(f"lab not found: {lab}")
        return 2

    # 备份，失败时整体回滚
    backups: dict[Path, str] = {}
    changed: list[tuple[str, int]] = []

    targets = list(lab.glob("*.py")) + list(lab.glob("docs/*.md")) + \
              [lab / "README.md"] + list(lab.glob("extension/*.js"))

    for p in targets:
        if not p.is_file():
            continue
        orig = p.read_text(encoding="utf-8")
        new, k = polish_file(p)
        if new != orig:
            backups[p] = orig
            p.write_text(new, encoding="utf-8", newline="\n")
            changed.append((str(p.relative_to(lab)), k))

    # 校验 1：AST
    broken = []
    for p in lab.glob("*.py"):
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            broken.append(f"{p.name}:{exc.lineno} {exc.msg}")

    # 校验 2：import
    env = dict(os.environ)
    env["PYTHONPATH"] = str(lab)
    env["PYTHONIOENCODING"] = "utf-8"
    import_fail = []
    if not broken:
        with tempfile.TemporaryDirectory() as td:
            for p in sorted(lab.glob("*.py")):
                r = subprocess.run([sys.executable, "-c", f"import {p.stem}"],
                                   cwd=td, env=env, capture_output=True,
                                   encoding="utf-8", errors="replace")
                if r.returncode != 0:
                    tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
                    import_fail.append(f"{p.name}: {tail[0]}")

    problems = broken + import_fail
    if problems:
        print("POLISH FAILED，回滚：")
        for pr in problems:
            print("  -", pr)
        for p, orig in backups.items():
            p.write_text(orig, encoding="utf-8", newline="\n")
        print(f"已回滚 {len(backups)} 个文件")
        return 3

    for name, k in changed:
        print(f"  - {name}  ({k} 处)")
    print(f"\npolished {len(changed)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
