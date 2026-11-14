# -*- coding: utf-8 -*-
"""apply.py —— 对清洗后的树做"结构性重写"（不只是词替换）

词表替换（sanitize.py）解决文案问题，但有三处光替换不够，必须动代码结构：

  1. extension/captcha_solver.js —— 整份是"图像识别 + 轨迹模拟 + debugger 可信输入"，
     替换解决不了，只能整体替换成 captcha_guard.js
  2. content.js 里 __CAPSOLVER 的调用点 —— 接口从 solve() 换成 guard 语义
  3. background.js 里 chrome.debugger 相关段 —— 整段删除，并去掉 debugger 权限
  4. captcha.py —— 换成 captcha_flow 包的薄适配器

所有改写都是**定位-替换-校验**三步：找不到锚点就报错退出，不静默跳过。
这样重新跑一遍是幂等的，也不会在源码变动后悄悄改错地方。

用法：
    python tools/sanitize/apply.py --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

INJECT = Path(__file__).parent / "inject"


class ApplyError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, where: str) -> str:
    n = text.count(old)
    if n != 1:
        raise ApplyError(f"{where}: anchor found {n} times (expected 1)\n---\n{old[:200]}")
    return text.replace(old, new, 1)


def replace_block(text: str, start_marker: str, end_marker: str, new: str, where: str) -> str:
    i = text.find(start_marker)
    if i < 0:
        raise ApplyError(f"{where}: start marker not found: {start_marker[:80]}")
    j = text.find(end_marker, i)
    if j < 0:
        raise ApplyError(f"{where}: end marker not found: {end_marker[:80]}")
    return text[:i] + new + text[j + len(end_marker):]


# ---------------------------------------------------------------- 1. solver → guard
def swap_solver(lab: Path, stats: dict) -> None:
    ext = lab / "extension"
    old = ext / "captcha_solver.js"
    src = INJECT / "booking-lab" / "extension" / "captcha_guard.js"
    if not src.is_file():
        raise ApplyError(f"missing template: {src}")
    dst = ext / "captcha_guard.js"
    shutil.copy2(src, dst)
    if old.exists():
        old.unlink()
        stats["removed"].append("extension/captcha_solver.js")
    stats["added"].append("extension/captcha_guard.js")

    bg = ext / "background.js"
    text = bg.read_text(encoding="utf-8")
    text = text.replace('"captcha_solver.js"', '"captcha_guard.js"')
    text = text.replace("captcha_solver.js", "captcha_guard.js")
    text = text.replace("__CAPSOLVER", "__CAPGUARD")
    bg.write_text(text, encoding="utf-8")
    stats["patched"].append("extension/background.js")


# ---------------------------------------------------------------- 2. background.js 去 debugger
def strip_debugger(lab: Path, stats: dict) -> None:
    bg = lab / "extension" / "background.js"
    text = bg.read_text(encoding="utf-8")

    # 整段可信输入删掉：从注释头到 trustedDrag 函数结束
    start = text.find("// ---------------------------------------------------------------- 可信输入")
    if start < 0:
        raise ApplyError("background.js: 可信输入 block not found")
    end = text.find("chrome.runtime.onMessage.addListener")
    if end < 0:
        raise ApplyError("background.js: onMessage listener not found")
    text = text[:start] + """// ---------------------------------------------------------------- 输入通道
// 只使用页面内合成事件（PointerEvent / MouseEvent）。
// 不使用调试协议那类能生成 isTrusted=true 的事件通道：
// 那个字段的语义是"这条输入是否来自用户"，伪造它等于向站点提供虚假信息。
// 合成事件被服务端拒绝时，正确做法是把它交给人（见 captcha_guard.js 的 needHuman 分支），
// 而不是升级成更"真"的输入。

""" + text[end:]

    # 删掉 trustedDrag 的 message 分支
    text = replace_block(
        text,
        '        case "trustedDrag": {',
        "          break;\n        }\n",
        "",
        "background.js: trustedDrag branch",
    )

    # 启动日志里不再提 debugger
    text = text.replace(
        'msg: "SW 启动 webNavigation=" + (!!chrome.webNavigation) + " debugger=" + (!!chrome.debugger) + " shots=v3"',
        'msg: "SW 启动 webNavigation=" + (!!chrome.webNavigation) + " shots=v3"',
    )
    if "chrome.debugger" in text:
        raise ApplyError("background.js: chrome.debugger 仍有残留")
    bg.write_text(text, encoding="utf-8")
    stats["patched"].append("extension/background.js (debugger removed)")


# ---------------------------------------------------------------- 3. content.js 改用 guard
WAF_NEW = '''  // ---------------------------------------------------------------- 验证码
  // 分派给 captcha_guard.js：它只做三件事——分类、等隐形 token、勾选框点一次。
  // 需要内容判断的形态（滑块/点选/图形码）它会返回 needHuman，这里立刻喊人。
  async function doWafCaptcha() {
    if (Date.now() < (state.captchaCooldown || 0)) return;

    if (!window.__CAPGUARD) {
      // 扩展没注入成功时不做任何猜测，直接喊人
      await askHuman("验证码脚本未就绪");
      return;
    }

    let s = { type: "unknown", detail: [] };
    try { s = window.__CAPGUARD.scan(); } catch (e) { /* ignore */ }

    if (!state.captchaReported) {
      state.captchaReported = true;
      const info = {
        stage: "waf_captcha",
        msg: "检测到验证码（" + (s.detail[0] || s.type) + "）",
        url: location.href.slice(0, 140), type: s.type
      };
      log(info.msg, "waf_captcha");
      send({ type: "status", data: info });
    }

    if (state.captchaTries === 0) await sleep2(rand(800, 1800));   // 先看一眼，别抢在人前面
    state.captchaTries++;

    let r = { ok: false, needHuman: true, note: "guard 异常", tries: [] };
    try {
      r = await window.__CAPGUARD.solve({ tries: 1 });
    } catch (e) {
      r = { ok: false, needHuman: true, note: "guard 抛错：" + (e && e.message ? e.message : e), tries: [] };
    }

    send({ type: "status", data: {
      stage: r.ok ? "captcha_solved" : "waf_captcha",
      msg: (r.ok ? "验证码已通过" : (r.needHuman ? "验证码需要人工完成" : "验证码未通过"))
           + (r.note ? "（" + r.note + "）" : ""),
      url: location.href.slice(0, 140), type: r.type || s.type
    } });

    if (r.ok) {
      state.captchaTries = 0;
      state.captchaReported = false;
      state.captchaCooldown = 0;
      return;
    }

    // 交人：抬窗口 + 通知 + 冷却。不重试、不换参数再猜。
    state.captchaTries = 0;
    state.captchaCooldown = Date.now() + rand(20000, 30000);
    await askHuman(r.type || s.type);
  }

  async function askHuman(kind) {
    if (state.humanAsked) return;
    state.humanAsked = true;
    send({ type: "status", data: {
      stage: "need_human",
      msg: "请手动完成验证码（" + (kind || "unknown") + "）",
      inst: state.inst || "", url: location.href
    } });
    try { window.focus(); } catch (e) { /* ignore */ }
    await new Promise((r) => setTimeout(r, 5000));
    state.humanAsked = false;   // 5 秒后可再提醒一次，避免刷屏也不永久静音
  }
'''

MT_NEW = '''  // MTCaptcha：勾选框可以点一次（不涉及内容判断）；出现图片挑战立刻交人。
  async function doMTCaptcha() {
    if (state.mtReported) return;
    state.mtReported = true;
    const info = { stage: "mtcaptcha", msg: "检测到 MTCaptcha", inst: state.inst || "", url: location.href };
    try {
      const tok = document.querySelector('input[name="captchaToken"], #captchaToken');
      info.tokenLen = tok ? (tok.value || "").length : -1;
      const boxes = [...document.querySelectorAll(
        'input[type=checkbox], [role=checkbox], .mtcap-checkbox, #mtcap-checkbox')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      });
      if (boxes.length) {
        boxes[0].click();
        info.msg = "MTCaptcha 勾选框已点一次，等待无感验证";
      } else {
        info.msg = "MTCaptcha 出现图片挑战，需要人工完成";
      }
      send({ type: "status", data: info });
      log(info.msg, "mtcaptcha");
    } catch (e) { /* ignore */ }
  }
'''

# 替换掉滑块机械后插入的说明，保持文件语义连贯
SLIDER_REMOVED = '''  // 说明：本文件不含任何滑块位移计算或拖动模拟。
  // 需要拖动的验证（滑块/拼图）由 captcha_guard.js 判定为 needHuman 后直接交人。
  // 保留 fireEv / humanClick 只用于普通按钮的点击节奏，不用于验证码。
'''


def _isolate_function(text: str, header: str, where: str) -> tuple[int, int]:
    """返回函数体（含 header 到匹配的收尾大括号）在 text 中的区间。"""
    i = text.find(header)
    if i < 0:
        raise ApplyError(f"{where}: header not found: {header[:60]}")
    depth = 0
    started = False
    for k in range(i, len(text)):
        ch = text[k]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                return i, k + 1
    raise ApplyError(f"{where}: unbalanced braces after {header[:60]}")


def rewrite_content_js(lab: Path, stats: dict) -> None:
    js = lab / "extension" / "content.js"
    text = js.read_text(encoding="utf-8")
    text = text.replace("__CAPSOLVER", "__CAPGUARD")

    # 顺序很重要：先删掉旧的 askHuman（滑块专用文案），再替换 doWafCaptcha。
    # 反过来的话 _isolate_function 会先命中新插入的那个 askHuman。
    a, b = _isolate_function(text, "  async function askHuman() {", "content.js")
    text = text[:a] + text[b:]

    # 滑块机械：pickHandle / dragBy / humanDrag 整段删除。
    # 这段是"算位移 + 模拟拖动"的实现本体，属于设计上排除的能力，
    # 留在文件里即使不被调用也会让审查方认为能力仍在。
    a, b = _isolate_function(text, "  function pickHandle() {", "content.js")
    text = text[:a] + SLIDER_REMOVED + text[b:]

    # doWafCaptcha 整个函数替换（新版本自带 askHuman）
    a, b = _isolate_function(text, "  async function doWafCaptcha() {", "content.js")
    text = text[:a] + WAF_NEW + text[b:]

    # doMTCaptcha 整体替换
    a, b = _isolate_function(text, "  async function doMTCaptcha() {", "content.js")
    text = text[:a] + MT_NEW + text[b:]

    # iframe 分支：guide 的接口变了（solve 不再有 tries，且没有 broadcastToChildFrames）
    text = replace_once(
        text,
        """        // 把本帧的偏移广播给子帧：跨域 iframe 里拿不到 frameElement，
        // 而 chrome.debugger 的 Input 要用**顶层视口坐标**才落得准。
        if (window.__CAPGUARD) {
          try { window.__CAPGUARD.broadcastToChildFrames(); } catch (e) { /* ignore */ }
        }
""",
        "",
        "content.js: broadcastToChildFrames",
    )
    text = text.replace(
        "// ① 验证码：以前这里只上报「请手动完成」，结果就是脚本看着不动、人得自己拖。\n"
        "        //    现在本帧就地求解（captcha_solver.js 随帧注入，canvas 在本帧同源，读得到）。",
        "// ① 验证码：本帧就地分类。需要内容判断的形态立刻上报交人，不做任何自动动作。",
    )
    if "captcha_solver" in text:
        raise ApplyError("content.js: captcha_solver 引用未清除")

    js.write_text(text, encoding="utf-8")
    stats["patched"].append("extension/content.js")


def verify_extension(lab: Path) -> list[str]:
    """硬校验：扩展里不允许存在的**可执行**模式。

    只检查剥掉注释后的代码：文档里写"不使用 X"是正当说明，
    不该被判成违规；只有真的调用了才算。
    """
    problems: list[str] = []
    ext = lab / "extension"
    banned = {
        "chrome.debugger": "调试协议调用",
        "__CAPSOLVER": "旧求解器引用",
        "captcha_solver": "旧求解器文件引用",
        "computeDx": "位移推算",
        "dragTrusted": "可信输入拖动",
        "toDataURL": "canvas 像素读取",
        "isTrusted:": "输入可信度伪造",
    }
    for f in sorted(ext.glob("*.js")):
        code = strip_js_comments(f.read_text(encoding="utf-8"))
        for pat, why in banned.items():
            if pat in code:
                problems.append(f"{f.name}: 可执行代码仍含 {pat}（{why}）")
    mf = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    perms = mf.get("permissions", [])
    if "debugger" in perms:
        problems.append("manifest.json: 仍声明 debugger 权限")
    hosts = " ".join(mf.get("host_permissions", []))
    for bad in ("td.gov.hk", "queue-it", "mtcaptcha"):
        if bad in hosts:
            problems.append(f"manifest.json: host_permissions 仍含真实域名 {bad}")
    return problems


def strip_js_comments(src: str) -> str:
    """去掉 // 行注释与 /* */ 块注释，保留字符串内容（避免误伤 URL 里的 //）。"""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == '"' or ch == "'" or ch == "`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                out.append(src[i])
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- 4. captcha.py → captcha_flow
ADAPTER = '''# -*- coding: utf-8 -*-
"""captcha.py —— captcha_flow 包的兼容适配器（保留旧调用签名）

原来的实现把「识别 + 动作 + 等待」混在一个函数里，既没法单测，也说不清
每一步为什么这样做。现在真实逻辑在 captcha_flow/，本文件只做签名适配：

    captcha.guard(page, who, shot=..., say=..., on_captcha=...)

行为对照（与旧实现的关键差异）
------------------------------
1. 滑块 / 图形码 / 点选：旧实现会尝试自动识别并拖动；现在**不做任何自动动作**，
   直接抬窗口 + 通知 + 等待人工完成。
2. 勾选框：旧实现会重试多次；现在只点一次，不过就交人。
3. 返回值统一成 "none" / "solved" / "timeout" 三个字符串，兼容既有调用点。
4. 新增 on_event 回调，把状态迁移交给 journal 记录。
"""
from __future__ import annotations

from typing import Callable

from captcha_flow import (  # noqa: F401  (对外转出，供旧代码 import)
    CaptchaKind, ChallengeInfo, FakePage, Guard, Outcome, Policy,
    PlaywrightPage, Result,
)
from captcha_flow.detect import detect as _detect
from captcha_flow.session import Guard as _Guard

# 旧代码里用 `kind in captcha.NEED_HUMAN` 判断是否必须人工。
# 现在由 CaptchaKind.needs_human 决定，这里保留同名常量做兼容（值保持一致）。
NEED_HUMAN = tuple(k.value for k in CaptchaKind if k.needs_human)


def beep(times: int = 6) -> None:
    """提示音。钉钉之外的第二条通道，纯本地。"""
    try:
        import time
        import winsound
        for _ in range(times):
            winsound.Beep(1400, 160)
            time.sleep(0.07)
    except Exception:
        print("\\a" * times, end="", flush=True)


def focus_browser_window(page=None, say=print) -> bool:
    """把浏览器窗口提到前台。Playwright 层 + Windows 层各试一次，都失败也不影响流程。"""
    if page is not None:
        try:
            page.bring_to_front()
        except Exception:
            pass
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        hits: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
            if "Chrome" in title or "Chromium" in title:
                hits.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        for hwnd in hits:
            try:
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
                user32.FlashWindow(hwnd, True)
            except Exception:
                continue
        return bool(hits)
    except Exception:
        return False


def _notify(kind: str, waited: int = 0) -> None:
    beep(6)
    try:
        import notifier as _N
        _N.captcha_need_human(kind, waited)
    except Exception:
        pass


def guard(page, who: str, shot=None, say=None, timeout: int = 180,
          on_captcha=None, realert_every: int = 45,
          on_event: Callable | None = None) -> str:
    """兼容旧签名的入口。返回 "none" / "solved" / "timeout"。

    shot(page, tag, note)       截图回调，遇到关键节点调用一次
    on_captcha(page, info, note) 取证回调，保留旧接口
    on_event(GuardEvent)        状态迁移回调（新增）
    """
    say = say or print
    policy = Policy(human_wait_s=float(timeout),
                    renotify_every_s=float(realert_every or 0))
    adapter = PlaywrightPage(page)

    def _log(msg: str) -> None:
        try:
            say(msg)
        except Exception:
            print(msg)

    def _event(ev) -> None:
        if on_event is not None:
            try:
                on_event(ev)
            except Exception:
                pass

    g = _Guard(adapter, policy, log=_log, on_event=_event, notify=_notify)

    info = _detect(adapter)
    if shot is not None and info.present:
        try:
            shot(page, "captcha", f"验证码出现：{info.summary}")
        except Exception:
            pass
    if on_captcha is not None and info.present:
        try:
            on_captcha(page, {"type": info.kind.value, "detail": list(info.detail)},
                       f"验证码出现：{info.kind.value}")
        except Exception:
            pass

    out = g.run(who, deadline_s=float(policy.token_wait_s + policy.human_wait_s))

    if shot is not None:
        try:
            shot(page, "captcha_ok" if out.passed else "captcha_timeout", str(out.result))
        except Exception:
            pass
    if on_captcha is not None:
        try:
            on_captcha(page, {"type": out.kind.value, "result": out.result.value},
                       f"验证码结果：{out.result.value}")
        except Exception:
            pass

    if out.result is Result.NO_CHALLENGE:
        return "none"
    if out.passed:
        return "solved"
    return "timeout"


def detect(page) -> dict:
    """兼容旧接口：旧代码（recorder.py 等）把返回值当 dict 用。

    新代码请直接用 captcha_flow.detect.detect() 拿 ChallengeInfo。
    这里返回的 dict 只保留旧代码实际读过的字段，避免两套结构长期并存。
    """
    info = _detect(PlaywrightPage(page))
    return {
        "type": info.kind.value,
        "tokenLen": info.token_len,
        "v3": info.has_sdk,
        "detail": list(info.detail),
        "title": info.title,
        "url": info.url,
    }


def solved(page) -> bool:
    from captcha_flow.detect import solved as _solved
    return _solved(PlaywrightPage(page))
'''


def write_adapter(lab: Path, stats: dict) -> None:
    dst = lab / "captcha.py"
    dst.write_text(ADAPTER, encoding="utf-8")
    stats["rewritten"].append("captcha.py → captcha_flow 适配器")


# ---------------------------------------------------------------- 5. 注入新文档
def inject_docs(lab: Path, stats: dict) -> None:
    # 顶层说明文件（README / HANDOFF）放到 dst 根目录
    root_src = INJECT / "root"
    if root_src.is_dir():
        for f in sorted(root_src.glob("*")):
            if f.is_file():
                shutil.copy2(f, lab.parent / f.name)
                stats["added"].append(f"../{f.name}")

    src_docs = INJECT / "booking-lab" / "docs"
    dst_docs = lab / "docs"
    dst_docs.mkdir(parents=True, exist_ok=True)
    for f in src_docs.glob("*.md"):
        if f.name.startswith("_"):
            continue
        shutil.copy2(f, dst_docs / f.name)
        stats["added"].append(f"docs/{f.name}")

    readme = INJECT / "booking-lab" / "README.md"
    if readme.is_file():
        shutil.copy2(readme, lab / "README.md")
        stats["rewritten"].append("README.md")

    # captcha_flow 整包（已含 tests）
    pkg_src = INJECT / "booking-lab" / "captcha_flow"
    pkg_dst = lab / "captcha_flow"
    if pkg_dst.exists():
        shutil.rmtree(pkg_dst)
    shutil.copytree(pkg_src, pkg_dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    stats["added"].append("captcha_flow/")

    # manifest 覆盖
    mf = INJECT / "booking-lab" / "extension" / "manifest.json"
    if mf.is_file():
        shutil.copy2(mf, lab / "extension" / "manifest.json")
        stats["rewritten"].append("extension/manifest.json")

    # 配置模板：必须与代码读取的字段结构一致（applicants 数组，不是 applicant 单对象）
    cfg = INJECT / "booking-lab" / "config.example.json"
    if cfg.is_file():
        shutil.copy2(cfg, lab / "config.example.json")
        stats["rewritten"].append("config.example.json")

    # .gitignore：防止 config.json / files/ / runs/ 被误提交
    gi = INJECT / "booking-lab" / "gitignore.txt"
    if gi.is_file():
        shutil.copy2(gi, lab / ".gitignore")
        stats["added"].append(".gitignore")


# ---------------------------------------------------------------- 6. 悬空引用 → 可选导入
def fix_dangling_refs(lab: Path, stats: dict) -> None:
    """被归档的能力模块被别处 import 时，改成"可选依赖 + 明确报错"。

    直接删掉 import 会让调用点变成 NameError；保留 import 会让整个模块 import 失败。
    正确做法是延迟导入 + 有意义的提示，让使用者知道这个能力被有意移出了共享树。
    """
    # http_flow --align：依赖环境画像模块（已移出）
    p = lab / "http_flow.py"
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        old = """    if a.align:
        from align_client import session_for
        try:"""
        new = """    if a.align:
        # --align 依赖"票据环境画像对齐"能力。这部分实现已从共享树移出
        # （见 ARCHIVE_MANIFEST.md 的 capability_review/env_spoofing）。
        # 共享版本保留参数定义但功能不可用，避免调用点静默降级。
        try:
            from align_client import session_for
        except ImportError:
            log("--align 不可用：环境画像对齐模块已移出共享树，请改用 --cookies 复用浏览器会话", "!")
            log.close()
            return 2
        try:"""
        if old in text:
            text = text.replace(old, new, 1)
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append("http_flow.py (--align 改为可选依赖)")

    # pool_runner 的验证机：verify_flow 已改名 flow_verify
    p = lab / "pool_runner.py"
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        if "import verify_flow" in text:
            text = text.replace("import verify_flow", "import flow_verify as verify_flow")
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append("pool_runner.py (verify_flow → flow_verify)")

    # ext_server 的 /captcha 端点：原本把 canvas 截图交给图像求解器算位移。
    # 这是设计上排除的能力，改成只落盘存档、明确回 501，不再导入求解器。
    p = lab / "ext_server.py"
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        start = text.find('        if u.path == "/captcha":')
        end = text.find('        if u.path == "/result":')
        if start < 0 or end < 0:
            raise ApplyError("ext_server.py: /captcha 或 /result 锚点未找到")
        new_block = '''        if u.path == "/captcha":
            # 只存档，不求解。
            #
            # 这里原来会把 canvas 截图交给图像求解器算滑块位移，再把 dx 回给扩展去拖动。
            # 那条路径已从共享版本移除：位置推断属于"代替人判断"，猜错会污染会话，
            # 而验证码的正确处置是分类 + 交接（见 captcha_flow/ 与 docs/CAPTCHA_DESIGN.md）。
            # 接口保留是为了让旧扩展拿到明确的失败信号，而不是超时。
            inst = data.get("inst") or ""
            data_url = data.get("dataUrl") or ""
            print(f"{datetime.now():%H:%M:%S} [captcha] 收到验证码截图 inst={inst or '-'} "
                  f"len={len(data_url)}（仅存档，不求解）", flush=True)
            try:
                if data_url:
                    import base64
                    raw = data_url.split(",", 1)[-1] if "," in data_url else data_url
                    RUNS.mkdir(exist_ok=True)
                    shot = RUNS / f"captcha_{datetime.now():%H%M%S}.png"
                    shot.write_bytes(base64.b64decode(raw))
                    _jr("server", "captcha_archived", inst=inst,
                        path=str(shot.relative_to(RUNS)))
            except Exception as exc:
                print("           [captcha] 存档失败:", type(exc).__name__, flush=True)
            return self._json({
                "ok": False,
                "error": "solver_removed",
                "hint": "验证码不做自动求解，请在浏览器窗口内人工完成",
            }, 501)

'''
        text = text[:start] + new_block + text[end:]
        p.write_text(text, encoding="utf-8", newline="\n")
        stats["patched"].append("ext_server.py (/captcha 改为仅存档)")

    # captcha_evidence.py 里对求解器的引用（该文件本身已归档，这里兜底）
    p = lab / "captcha_evidence.py"
    if p.is_file():
        text = p.read_text(encoding="utf-8")
        text = text.replace("    import slider as SL\n", "")
        p.write_text(text, encoding="utf-8", newline="\n")


def fix_config_encoding(lab: Path, stats: dict) -> None:
    """配置文件读取改用 utf-8-sig。

    Windows 上用记事本/部分编辑器保存 JSON 会带 UTF-8 BOM，
    而 `json.loads(text)` 遇到 BOM 会直接抛 JSONDecodeError —— 用户照模板填完就卡在这。
    utf-8-sig 对无 BOM 文件同样正常工作，属于纯增强。

    只改读取**配置文件**的那几处，不全局替换（避免影响网络响应的解码语义）。
    """
    targets = {
        "fill_form.py": [('return json.loads(path.read_text(encoding="utf-8"))',
                          'return json.loads(path.read_text(encoding="utf-8-sig"))')],
        "http_flow.py": [('cfg = json.loads(p.read_text(encoding="utf-8"))',
                          'cfg = json.loads(p.read_text(encoding="utf-8-sig"))')],
        "ext_server.py": [('cfg = json.loads(CONFIG.read_text(encoding="utf-8"))',
                           'cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))')],
        "notifier.py": [('cfg = json.loads(CONF.read_text(encoding="utf-8"))',
                         'cfg = json.loads(CONF.read_text(encoding="utf-8-sig"))')],
        "flow_verify.py": [('cfg = json.loads(p.read_text(encoding="utf-8"))',
                            'cfg = json.loads(p.read_text(encoding="utf-8-sig"))')],
    }
    for name, rules in targets.items():
        p = lab / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        n = 0
        for old, new in rules:
            if old in text:
                text = text.replace(old, new, 1)
                n += 1
        if n:
            p.write_text(text, encoding="utf-8", newline="\n")
            stats["patched"].append(f"{name} (config 读取兼容 BOM)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dst", default="hk-project-clean")
    args = ap.parse_args()

    lab = (Path.cwd() / args.dst / "booking-lab").resolve()
    if not lab.is_dir():
        print(f"lab not found: {lab}\n先跑 sanitize.py")
        return 2

    stats: dict = {"removed": [], "added": [], "patched": [], "rewritten": []}
    try:
        swap_solver(lab, stats)
        strip_debugger(lab, stats)
        rewrite_content_js(lab, stats)
        write_adapter(lab, stats)
        inject_docs(lab, stats)
        fix_dangling_refs(lab, stats)
        fix_config_encoding(lab, stats)
    except ApplyError as exc:
        print(f"APPLY FAILED: {exc}")
        return 3

    problems = verify_extension(lab)
    print(f"removed : {stats['removed']}")
    print(f"added   : {stats['added']}")
    print(f"patched : {stats['patched']}")
    print(f"rewrote : {stats['rewritten']}")
    if problems:
        print("\nVERIFY FAILED:")
        for p in problems:
            print("  -", p)
        return 4
    print("\nverify ok: 扩展内无 debugger / 无图像求解 / 无真实域名")

    rp = Path.cwd() / "tools" / "sanitize" / "reports" / "apply_run.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
