#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
captcha_rescue.py —— 验证码自动救援：脚本开浏览器、灌会话、你只拖一下，之后自动交回 HTTP
===========================================================================
场景：纯 HTTP 撞到 MTCaptcha，脚本自己产不出 token（token 是服务端签发的，算不出来）。

这条命令把「人工过验证」压缩成一次拖拽：

    ① 起一个独立的 Chrome for Testing（端口 9223，独立 profile，不碰你日常窗口）
    ② 把当前 HTTP 会话的 cookie 灌进去（会话不丢，不用重新排队）
    ③ 直接导航到刚出验证码的那个页面
    ④ 你在窗口里拖一下滑块 / 点几下
    ⑤ 脚本轮询到 captchaToken 有值（或页面跳到下一步）→ **自动导出 cookie 并公告**
    ⑥ 交给调用方（sniper / fleet）继续跑，你什么都不用做

用法
----
    # 手动救援：给它出验证码的页面 URL 和刚导出的 cookie
    python captcha_rescue.py --url "https://abs1.td.gov.hk/...AdminServlet?cmd=..." \
                             --cookies runs/sniper/rescue_in.json

    # 由 sniper 自动触发（撞到验证码时自己调这个）
    python sniper.py --cookies runs/rev/browser_cookies.json --step1 --auto-rescue

退出码：0 救援成功（cookie 已导出） / 42 超时没人动 / 41 页面本身打不开（WAF/412）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
RESCUE_DIR = RUNS / "rescue"
RESCUE_DIR.mkdir(parents=True, exist_ok=True)

CFT = BASE / "cft" / "chrome-win64" / "chrome.exe"
PORT = 9223
PROFILE = RUNS / "rescue_profile"

# 页面上出现这些东西 == 验证码已经过了 / 已经到下一步
DONE_HINTS = (
    "captchaToken",       # 值非空
    "continueBtn1",       # 回到了第 1 步表单
    "cmdGetTimeslot",     # 进了选时段
)


def ensure_browser(port: int = PORT) -> bool:
    """保证 9223 上有可用的 Chrome；没有就自己起一个（独立 profile）。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            r.read()
        return True
    except Exception:
        pass
    if not CFT.exists():
        print(f"! 找不到 {CFT}（可先跑 get_cft.ps1 下载）")
        return False
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [str(CFT), f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE}", "--no-sandbox", "--no-proxy-server",
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=CalculateNativeWinOcclusion", "about:blank"]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.4)
        try:
            with op.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                r.read()
            print(f"已启动救援浏览器（端口 {port}，profile {PROFILE.name}）")
            return True
        except Exception:
            continue
    return False


def to_pw_cookies(raw: list[dict], fallback_url: str) -> list[dict]:
    """把 curl_cffi 风格的 cookie 转成 playwright 能吃的格式。"""
    out = []
    for c in raw:
        dom = (c.get("domain") or "").lstrip(".") or "abs1.td.gov.hk"
        item = {
            "name": c["name"], "value": c["value"],
            "domain": c.get("domain") or dom,
            "path": c.get("path") or "/",
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", True)),
            "sameSite": "None",
        }
        # playwright 要求要么给 url，要么给 domain+path；两者都给最稳
        try:
            out.append(item)
        except Exception:
            pass
    return out


def dump(session, out: Path) -> int:
    cookies = []
    for c in session:
        try:
            cookies.append({"name": c["name"], "value": c["value"],
                            "domain": c["domain"], "path": c["path"],
                            "httpOnly": c.get("httpOnly", False),
                            "secure": c.get("secure", True)})
        except Exception:
            pass
    out.write_text(json.dumps(cookies, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(cookies)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="出验证码的那个页面")
    ap.add_argument("--cookies", default="", help="当前会话 cookie JSON")
    ap.add_argument("--timeout", type=float, default=300, help="等人拖滑块的时限（秒）")
    ap.add_argument("--out", default=str(RESCUE_DIR / "rescue_out.json"))
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()

    url = a.url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]

    if not ensure_browser(a.port):
        print("! 浏览器起不来，救援失败")
        return 41

    raw = []
    if a.cookies and Path(a.cookies).exists():
        raw = json.loads(Path(a.cookies).read_text(encoding="utf-8"))
        print(f"灌入 {len(raw)} 条 cookie（来自 {Path(a.cookies).name}）")

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
        ctx = b.contexts[0] if b.contexts else b.new_context()
        if raw:
            try:
                ctx.add_cookies(to_pw_cookies(raw, url))
            except Exception as e:
                print("! 灌 cookie 失败：", type(e).__name__, str(e)[:120])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(f"导航 → {url[:120]}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("! 导航异常：", type(e).__name__, str(e)[:140])
        print("\n" + "=" * 64)
        print("★ 请在浏览器窗口里完成验证（拖滑块 / 点图）")
        print("  脚本会自动发现「过了」，不需要你回来敲任何键")
        print("=" * 64 + "\n")

        deadline = time.time() + a.timeout
        last = ""
        token = ""
        while time.time() < deadline:
            time.sleep(1.2)
            try:
                info = page.evaluate(r"""() => {
                    const t = document.querySelector('input[name="captchaToken"]');
                    return {
                        url: location.href,
                        token: t ? String(t.value || '') : '',
                        hasForm: !!document.querySelector('form[name="frmAppointment"]'),
                        title: document.title,
                        bodyLen: (document.body && document.body.innerHTML || '').length
                    };
                }""")
            except Exception:
                continue
            tk = info.get("token") or ""
            cur = f"{info.get('title','')[:20]} token={len(tk)} url={info.get('url','')[:70]}"
            if cur != last:
                print(f"  … {cur}", flush=True)
                last = cur
            if len(tk) > 20:
                token = tk
                print(f"\n★ 检测到 captchaToken（{len(tk)} 字符）→ 验证已通过")
                break
            if info.get("url", "") != url and "captcha" not in info.get("url", "").lower() \
                    and info.get("hasForm"):
                print("\n★ 页面已跳到下一步（有 frmAppointment）→ 视为通过")
                break

        n = dump(ctx.cookies(), Path(a.out))
        try:
            b.close()
        except Exception:
            pass

    if token:
        (RESCUE_DIR / "captcha_token.txt").write_text(token, encoding="utf-8")
        print(f"captchaToken 已存 {RESCUE_DIR / 'captcha_token.txt'}")
    print(f"导出 {n} 条 cookie → {a.out}")
    if n == 0:
        return 42
    print("RESCUE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
