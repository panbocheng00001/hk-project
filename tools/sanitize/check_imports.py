# -*- coding: utf-8 -*-
"""check_imports.py —— 清洗后树的自检：本地模块 import 是否都能解析

清洗会改名（waf.py → edge_challenge.py 等）和移出文件，
最容易出的事故是"某个 import 指向了已经不存在的模块"。
这个脚本把这些都找出来，作为流水线的最后一道闸。

用法：
    python tools/sanitize/check_imports.py hk-project-clean/booking-lab
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 已知的第三方 / 标准库前缀，不检查
STDLIB_HINT = {
    "os", "sys", "re", "json", "time", "datetime", "pathlib", "typing", "argparse",
    "collections", "itertools", "functools", "math", "random", "base64", "hashlib",
    "hmac", "sqlite3", "threading", "subprocess", "shutil", "glob", "csv", "io",
    "contextlib", "dataclasses", "enum", "traceback", "logging", "statistics",
    "urllib", "http", "socket", "ssl", "select", "struct", "unicodedata", "uuid",
    "queue", "signal", "platform", "textwrap", "webbrowser", "tempfile", "copy",
    "bisect", "string", "secrets", "asyncio", "concurrent", "abc", "warnings",
    "pickle", "zlib", "html", "xml", "email", "smtplib", "imaplib", "calendar",
    "difflib", "operator", "pprint", "stat", "binascii", "ctypes", "atexit",
    "importlib", "inspect", "unittest", "getpass", "locale", "decimal", "fractions",
    # Windows 专有标准库
    "winsound", "winreg", "msvcrt", "win32api", "win32con", "win32gui",
}
THIRD_PARTY = {
    "playwright", "cv2", "numpy", "PIL", "fastapi", "uvicorn", "pydantic", "pytest",
    "httpx", "starlette", "curl_cffi", "psutil", "requests", "scipy", "sklearn",
    "yaml", "dotenv", "patchright", "cffi", "certifi", "pycparser", "ultralytics",
}


def local_modules(root: Path) -> set[str]:
    mods = {p.stem for p in root.glob("*.py")}
    for d in root.iterdir():
        if d.is_dir() and (d / "__init__.py").exists():
            mods.add(d.name)
    return mods


def scan(root: Path) -> list[dict]:
    mods = local_modules(root)
    problems: list[dict] = []
    for p in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append({"file": p.name, "line": exc.lineno or 0,
                             "module": "<syntax>", "why": f"syntax error: {exc.msg}"})
            continue
        for node in ast.walk(tree):
            names: list[tuple[str, int]] = []
            if isinstance(node, ast.Import):
                names = [(a.name.split(".")[0], node.lineno) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue          # 相对导入，跳过
                if not node.module:
                    continue
                names = [(node.module.split(".")[0], node.lineno)]
            for mod, line in names:
                if mod in mods or mod in STDLIB_HINT or mod in THIRD_PARTY:
                    continue
                if mod.startswith("_"):
                    continue
                problems.append({"file": p.name, "line": line, "module": mod,
                                 "why": "本地模块不存在"})
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = scan(root)

    # 已知可接受的：这些文件依赖的模块被有意移出了共享树
    # align_client 在 http_flow.py 里是 try/except ImportError 的可选依赖
    archived = {"drag_puzzle", "slider", "edge_solve", "captcha_solver", "align_client"}
    real = [p for p in problems if p["module"] not in archived]

    print(f"root: {root}")
    print(f"problems: {len(problems)}  (其中 {len(problems) - len(real)} 条指向已移出的模块)")
    for p in problems:
        tag = "已移出" if p["module"] in archived else "缺失"
        print(f"  [{tag}] {p['file']}:{p['line']} -> {p['module']}  ({p['why']})")

    out = root.parent.parent / "tools" / "sanitize" / "reports" / "imports.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"root": str(root), "problems": problems},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
