# -*- coding: utf-8 -*-
"""harden.py —— 第二轮：把"环境伪装"这类默认行为降级

apply.py 处理扩展与验证码结构；这一轮处理 Python 侧残留的
「默认用浏览器 TLS 画像发请求」和「伪造转发头」两类默认行为。

处理原则
--------
不是删功能，是把**默认值从「伪装」改成「不伪装」**：

  curl_cffi 的 impersonate 参数会把请求的 TLS/HTTP2 指纹做成某个浏览器的样子。
  这在本项目里的原用途是"让纯 HTTP 请求看起来像浏览器"。
  现在改成：
    · 默认空（用 curl_cffi 自身的默认 TLS 栈）
    · 需要通过环境变量 BOOKING_TLS_PROFILE 显式指定才启用
    · 启用时在日志里打一行，说明这次请求使用了非默认 TLS 画像

  --xff（伪造 X-Forwarded-For）直接删除：它唯一用途是探测服务端是否信任
  转发头，属于对目标站点的探测行为，不属于本工具需要的能力。

  verify=False（关闭证书校验）改成从配置读，默认 True。

用法：
    python tools/sanitize/harden.py --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class HardenError(RuntimeError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise HardenError(msg)


def patch_text(path: Path, rules: list[tuple[str, str]], *, optional: bool = False) -> int:
    """按 (old, new) 逐条替换，返回改动条数。

    optional=True 时允许某条没命中（用于只在部分文件出现的写法）。
    """
    if not path.is_file():
        if optional:
            return 0
        raise HardenError(f"missing file: {path}")
    text = path.read_text(encoding="utf-8")
    orig = text
    n = 0
    for old, new in rules:
        if old in text:
            text = text.replace(old, new)
            n += 1
        elif not optional:
            raise HardenError(f"{path.name}: anchor not found -> {old[:80]!r}")
    if text != orig:
        path.write_text(text, encoding="utf-8", newline="\n")
    return n


# ---------------------------------------------------------------- 1. TLS 画像默认关闭
IMPERSONATE_DOC = '''# TLS 画像说明
# -------------
# curl_cffi 支持用 `impersonate=` 指定某个浏览器的 TLS/HTTP2 特征。
# 本项目**不默认启用**这一项：
#   · 默认用 curl_cffi 自身的 TLS 栈发请求；
#   · 需要时通过环境变量 BOOKING_TLS_PROFILE 显式指定（例如 chrome131）。
# 如果某个出口在默认 TLS 下拿不到正常响应，说明该出口与站点不兼容，
# 正确做法是换出口或回到浏览器路径，而不是把 TLS 画像改成别的浏览器。
'''

TLS_HELPER = '''

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
'''


def harden_tls(lab: Path, stats: dict) -> None:
    targets = [
        "http_flow.py", "session_pool.py", "site_monitor.py",
        "edge_probe.py", "edge_token.py", "preflight.py",
        "edge_handshake.py", "edge_path_probe.py", "edge_replay.py",
    ]

    # session_pool 的 _auto_impersonate 是"自动挑一个浏览器画像"，改为返回空
    sp = lab / "session_pool.py"
    if sp.is_file():
        text = sp.read_text(encoding="utf-8")
        old = '''    @staticmethod
    def _auto_impersonate() -> str:
        try:
            from curl_cffi.requests.impersonate import DEFAULT_CHROME
            return DEFAULT_CHROME
        except Exception:
            return "chrome124"
'''
        new = '''    @staticmethod
    def _auto_impersonate() -> str:
        """默认不指定 TLS 画像。

        原实现会挑一个浏览器预设（DEFAULT_CHROME），让纯 HTTP 请求的 TLS 特征
        与浏览器一致。这里改成默认空：需要时由调用方显式传入，
        或用环境变量 BOOKING_TLS_PROFILE 指定（见 tls_profile()）。
        """
        return tls_profile()
'''
        if old in text:
            sp.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
            stats["patched"].append("session_pool.py (_auto_impersonate 默认不指定画像)")
        # __init__ 签名里的 verify 默认值
        text = sp.read_text(encoding="utf-8")
        text = text.replace('impersonate: str = "", verify: bool = False)',
                            'impersonate: str = "", verify: bool = VERIFY_TLS)')
        sp.write_text(text, encoding="utf-8", newline="\n")

    # site_monitor 的 import 段 + DEFAULT_CHROME 引用
    sm = lab / "site_monitor.py"
    if sm.is_file():
        text = sm.read_text(encoding="utf-8")
        old = """try:
    from curl_cffi import requests as _cffi
    from curl_cffi.requests.impersonate import DEFAULT_CHROME
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    DEFAULT_CHROME = ""
"""
        new = """try:
    from curl_cffi import requests as _cffi
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
"""
        if old in text:
            text = text.replace(old, new)
            stats["patched"].append("site_monitor.py (不再引用 DEFAULT_CHROME)")
        # 还有别处用到 DEFAULT_CHROME 的，统一处理：
        #   _m = re.search(r"(\d+)", DEFAULT_CHROME or "")  → 用字面量兜底版本号
        # 注意不能替换成 CHROME_VER：那一行在 CHROME_VER 定义之前，会 NameError。
        if "DEFAULT_CHROME" in text:
            text = text.replace(
                '_m = re.search(r"(\\d+)", DEFAULT_CHROME or "")',
                '_m = re.search(r"(\\d+)", "152")   # 兜底版本号，不再从 TLS 画像预设里取')
            text = text.replace("DEFAULT_CHROME", '""')
        sm.write_text(text, encoding="utf-8", newline="\n")

    for name in targets:
        p = lab / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        changed = False

        # 1) 关键字默认值：impersonate: str = "chrome" → ""
        new_text, k = re.subn(
            r'impersonate:\s*str\s*=\s*"chrome[0-9]*"',
            'impersonate: str = ""',
            text)
        if k:
            text, changed = new_text, True

        # 2) 字面 kw 里的 impersonate: "chrome" → 走 helper
        new_text, k = re.subn(
            r'kw\s*=\s*\{"impersonate":\s*"chrome[0-9]*",\s*',
            'kw = {**({"impersonate": _tls} if _tls else {}), ',
            text)
        if k:
            text, changed = new_text, True

        # 3) argparse 默认值
        new_text, k = re.subn(
            r'(ap\.add_argument\(\s*"--impersonate",\s*)default="chrome[0-9]*"',
            r'\1default=""',
            text)
        if k:
            text, changed = new_text, True

        # 4) DEFAULT_CHROME 直用 → 默认不指定
        new_text = text.replace(
            "IMPERSONATE = DEFAULT_CHROME or f\"chrome{CHROME_VER}\"",
            "IMPERSONATE = \"\"   # 默认不指定 TLS 画像，见文件头说明")
        if new_text != text:
            text, changed = new_text, True

        if changed:
            # 插入 helper（幂等）
            text = _insert_module_header(text, TLS_HELPER.strip() + "\n", marker="def tls_profile(")
            text = _insert_module_header(text, IMPERSONATE_DOC, marker="TLS 画像说明")
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append(f"{name} (TLS 画像默认关闭)")

    # 5) kw 里用到 _tls 的，补一行定义
    for name in targets:
        p = lab / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if "_tls" in text and "def tls_profile(" in text and "\n_tls = tls_profile()" not in text:
            # 在 tls_profile 函数结束后插一行模块级 _tls
            text = text.replace(
                "    return val\n",
                "    return val\n\n\n_tls = tls_profile()\n",
                1,
            )
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append(f"{name} (_tls 模块级默认值)")


# ---------------------------------------------------------------- 2. 删掉伪造转发头
def drop_xff(lab: Path, stats: dict) -> None:
    """删掉 --xff（伪造 X-Forwarded-For）。

    注意不能只删行：`if xff:` 这类语句体被删掉后会留下空块，产生语法错误。
    所以按"签名 + 整块"一起替换，并在结束前做一次 AST 校验。
    """
    p = lab / "edge_probe.py"
    if not p.is_file():
        return
    text = p.read_text(encoding="utf-8")

    # 1) 函数签名里的 xff 参数
    text = text.replace(', ua: str = UA, xff: str = "") -> dict:',
                        ', ua: str = UA) -> dict:').replace(
                        ', xff: str = "") -> dict:', ') -> dict:')

    # 2) 整个 if xff 块（可能不止一行）
    text = re.sub(r"\n    if xff:\n(?:        [^\n]*\n|\n)*", "\n", text)

    # 3) argparse 参数行
    text = re.sub(r'\n *ap\.add_argument\("--xff"[^\n]*\n', "\n", text)

    # 4) 调用处去掉 xff 实参
    text = re.sub(r',\s*xff=a\.xff', "", text)
    text = re.sub(r'xff=[^,)]*,\s*', "", text)

    p.write_text(text, encoding="utf-8", newline="\n")

    # AST 校验：语法坏了立刻报错，不留给下游
    import ast
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise HardenError(f"edge_probe.py 删除 --xff 后语法错误：{exc}") from exc

    if "X-Forwarded-For" in text or "--xff" in text:
        raise HardenError("edge_probe.py: --xff 仍有残留")
    stats["patched"].append("edge_probe.py (移除 --xff 伪造转发头)")


# ---------------------------------------------------------------- 3. 证书校验默认开启
def _module_body_start(text: str) -> int:
    """返回"模块头之后第一个真正的语句"的字符偏移。

    依次跳过：shebang、coding 行、模块 docstring、__future__ 导入。
    这几样都必须在文件最前面，插在它们之前的代码会直接语法错误。
    """
    lines = text.splitlines(keepends=True)
    i, n = 0, len(lines)

    # shebang / coding
    while i < n:
        s = lines[i].strip()
        if s.startswith("#!") or (s.startswith("#") and "coding" in s):
            i += 1
        else:
            break

    # 模块 docstring
    while i < n:
        s = lines[i].lstrip()
        if not s or s.startswith("#"):
            i += 1
            continue
        if s[:3] in ('"""', "'''"):
            quote = s[:3]
            if s.count(quote) >= 2:
                i += 1
                continue
            i += 1
            while i < n and quote not in lines[i]:
                i += 1
            i += 1
            continue
        break

    # __future__ 导入（必须紧跟 docstring）
    while i < n:
        if lines[i].strip().startswith("from __future__"):
            i += 1
            continue
        break

    return sum(len(x) for x in lines[:i])


def _insert_module_header(text: str, snippet: str, marker: str) -> str:
    """把 snippet 插到模块头之后（幂等：marker 已存在就不插）。"""
    if marker in text:
        return text
    idx = _module_body_start(text)
    return text[:idx] + "\n" + snippet + text[idx:]


def _insert_after_header(text: str, snippet: str) -> str:
    return _insert_module_header(text, snippet, marker="VERIFY_TLS = ")


def harden_verify(lab: Path, stats: dict) -> None:
    """把 verify=False 改成显式常量，并保证常量定义在模块级可见。

    两件事：
      1) verify=False → verify=VERIFY_TLS
      2) 如果文件里没有 VERIFY_TLS 定义，插到模块头之后（跳过 docstring / __future__）
    另外 session_pool 用了 VERIFY_TLS 但常量要在一处定义，统一处理。
    """
    const = (
        'import os\n\n'
        '# 证书校验默认开启。内网自签证书环境可设 BOOKING_INSECURE_TLS=1 关闭。\n'
        'VERIFY_TLS = os.environ.get("BOOKING_INSECURE_TLS", "") not in ("1", "true", "yes")\n'
    )
    for p in sorted(lab.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        changed = False

        if "verify=False" in text:
            text = text.replace("verify=False", "verify=VERIFY_TLS")
            changed = True

        # 用到 VERIFY_TLS 但没有定义 → 补定义
        uses = "VERIFY_TLS" in text
        defines = re.search(r"^VERIFY_TLS\s*=", text, re.M) is not None
        if uses and not defines:
            text = _insert_after_header(text, const)
            changed = True

        if changed:
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append(f"{p.name} (证书校验默认开启)")


def rename_user_facing_tls_flag(lab: Path, stats: dict) -> None:
    """把用户可见的 --impersonate 改名为 --tls-profile。

    curl_cffi 的 kwarg 只能叫 impersonate（第三方 API，改不了），
    但命令行参数是本项目自己的门面，不该用"冒充"这种词。
    """
    for p in sorted(lab.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if '"--impersonate"' not in text:
            continue
        text = text.replace('"--impersonate"', '"--tls-profile"')
        text = text.replace("--impersonate none", "--tls-profile none")
        # 帮助文本里的 self.xxx 保留（属性名与 kwarg 同名，不面向用户）
        p.write_text(text, encoding="utf-8", newline="\n")
        stats["patched"].append(f"{p.name} (--impersonate → --tls-profile)")


def fix_escape_warnings(lab: Path, stats: dict) -> None:
    """修掉 Windows 路径反斜杠在普通字符串/docstring 里引发的 SyntaxWarning。

    只修已知的模式（`\\s` 之类会被 Python 当成转义的路径写法），
    改成 raw 前缀或双反斜杠，语义不变。
    """
    fixed = []
    for p in sorted(lab.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        orig = text
        # 文档里的示例路径：runs\xxx → runs\\xxx（在非 raw 字符串里合法且语义不变）
        text = re.sub(r"(?<![\\r])runs\\(session|_|rev|net|tickets)", r"runs\\\\\1", text)
        if text != orig:
            p.write_text(text, encoding="utf-8", newline="\n")
            fixed.append(p.name)
    if fixed:
        stats["patched"].extend(f"{n} (路径反斜杠转义)" for n in fixed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", default="hk-project-clean")
    args = ap.parse_args()

    lab = (Path.cwd() / args.dst / "booking-lab").resolve()
    if not lab.is_dir():
        print(f"lab not found: {lab}")
        return 2

    stats: dict = {"patched": []}
    try:
        harden_tls(lab, stats)
        drop_xff(lab, stats)
        harden_verify(lab, stats)
        rename_user_facing_tls_flag(lab, stats)
        fix_escape_warnings(lab, stats)
    except HardenError as exc:
        print(f"HARDEN FAILED: {exc}")
        return 3

    for s in stats["patched"]:
        print("  -", s)
    print(f"\npatched {len(stats['patched'])} items")

    # 校验：不允许裸的 impersonate 默认值残留、不允许关闭证书校验
    problems = []
    for p in sorted(lab.glob("*.py")):
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r'impersonate\s*[=:]\s*"chrome[0-9]*"', t):
            problems.append(f"{p.name}: {m.group(0)}")
        if "verify=False" in t:
            problems.append(f"{p.name}: 仍有 verify=False")
        if "DEFAULT_CHROME" in t and "from curl_cffi.requests.impersonate import DEFAULT_CHROME" in t:
            problems.append(f"{p.name}: 仍自动引用 DEFAULT_CHROME")
    if problems:
        print("\nVERIFY FAILED:")
        for x in problems:
            print("  -", x)
        return 4

    # AST 校验：所有 .py 必须能解析
    import ast
    broken = []
    for p in sorted(lab.glob("*.py")):
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            broken.append(f"{p.name}:{exc.lineno} {exc.msg}")
    if broken:
        print("\nSYNTAX FAILED:")
        for x in broken:
            print("  -", x)
        return 5

    print("verify ok: 无默认 TLS 伪装 / 无关闭证书校验 / 全部可解析")

    # 运行期校验：每个模块都要能 import（最硬的一条）
    # 注意：必须在临时目录里跑。有些模块 import 时就会创建日志/运行目录，
    # 在 lab 目录里跑会在交付树里留下 side-effect 文件。
    import os
    import subprocess
    import tempfile
    env = dict(os.environ)
    env["PYTHONPATH"] = str(lab)
    env["PYTHONIOENCODING"] = "utf-8"
    failed = []
    with tempfile.TemporaryDirectory() as td:
        for p in sorted(lab.glob("*.py")):
            r = subprocess.run([sys.executable, "-c", f"import {p.stem}"],
                               cwd=td, env=env, capture_output=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
                failed.append(f"{p.name}: {tail[0]}")
    if failed:
        print("\nIMPORT FAILED:")
        for x in failed:
            print("  -", x)
        return 6
    print("import ok: 全部模块可导入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
