# -*- coding: utf-8 -*-
"""ext_integration_test.py —— 端到端验证「扩展 → 求解器 → 本地服务」这条链
=================================================================================
验证的不是算法（那是 captcha_selftest.py），而是**接线**：
    1) background 有没有把 captcha_solver.js 先于 content.js 注入
    2) content.js 主文档分支能不能调到 window.__CAPSOLVER
    3) 求解结果有没有经 /status 回到本地服务（钉钉就靠这条）
    4) 失败时会不会上报 need_human（而不是像原来那样一声不响）

做法：起一个本地 ext_server，用 CfT 加载扩展打开 /test_captcha（合成滑块题），
读 runs/ext_status.jsonl 的新增行。不碰真实站点。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFT = BASE / "cft" / "chrome-win64" / "chrome.exe"
PROFILE = BASE / "runs" / "_extest"
STATUS = BASE / "runs" / "ext_status.jsonl"


def _serves_frame() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/test_captcha_frame?SelectCountry=1", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _port_open(host: str, port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def run() -> int:
    if not CFT.exists():
        raise SystemExit(f"找不到 CfT：{CFT}")
    if PROFILE.exists():
        # --load-extension 会把扩展复制进 profile，复用旧目录会跑到旧代码
        import shutil
        shutil.rmtree(PROFILE, ignore_errors=True)
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    before = STATUS.stat().st_size if STATUS.exists() else 0

    # 本地服务可能已经开着（常驻的那个）。但如果它是旧代码（没有 /test_captcha_frame），
    # 这道题根本不会被服务出来 —— 先验一下，旧的就换掉。
    srv = None
    if _serves_frame():
        print("复用已在跑的 ext_server（版本匹配）")
    elif _port_open("127.0.0.1", 8765):
        print("8765 上是旧版 ext_server，替换掉")
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "$c=Get-NetTCPConnection -LocalPort 8765 -State Listen -EA SilentlyContinue;"
                        "if($c){Stop-Process -Id $c.OwningProcess -Force}"], capture_output=True)
        time.sleep(1.0)
        srv = subprocess.Popen([sys.executable, str(BASE / "ext_server.py"),
                                "--port", "8765", "--who", "w1"],
                               cwd=str(BASE), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        time.sleep(2.5)
    if not _port_open("127.0.0.1", 8765):
        srv = subprocess.Popen([sys.executable, str(BASE / "ext_server.py"),
                                "--port", "8765", "--who", "w1"],
                               cwd=str(BASE), stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        time.sleep(2.0)

    common = [
        str(CFT),
        f"--user-data-dir={PROFILE}",
        f"--load-extension={BASE / 'extension'}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=Translate", "--lang=zh-CN",
    ]
    # 先开空白页，让 service worker 起来，再另开一个标签打开题目页 ——
    # 直接在命令行带 URL 时，tab 的 complete 事件常在扩展加载前就跑完了，
    # 结果注入不触发（今天实测就是这样，/status 一条都没有）。
    chrome = subprocess.Popen(common + ["about:blank"], cwd=str(BASE),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6.0)
    subprocess.Popen(common + ["http://127.0.0.1:8765/test_captcha"], cwd=str(BASE),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8.0)
    # 第二道：验证码在 iframe 里（主文档看不到）—— 这是今晚卡住的那种形态
    subprocess.Popen(common + ["http://127.0.0.1:8765/test_captcha_frame?SelectCountry=1"], cwd=str(BASE),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("跑 30 秒，等扩展注入并自动解那道合成滑块…")
    time.sleep(30.0)

    for p in (chrome, srv):
        try:
            if p: p.terminate()
        except Exception:
            pass
    # 关掉可能残留的 chrome 子进程（只认这个 profile）
    try:
        subprocess.run(["taskkill", "/F", "/FI", f"WINDOWTITLE eq *test_captcha*"],
                       capture_output=True)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                        f"Where-Object {{ $_.CommandLine -like '*{PROFILE.name}*' }} | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       capture_output=True)
    except Exception:
        pass

    lines = []
    if STATUS.exists():
        with STATUS.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(before)
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    lines.append(json.loads(ln))
                except Exception:
                    pass

    print("=" * 68)
    print(f"/status 新增 {len(lines)} 条")
    seen = set()
    for r in lines:
        st = r.get("stage", "?")
        seen.add(st)
        print(f"  {r.get('t','')}  {st:20} {str(r.get('msg',''))[:90]}")
    print("-" * 68)
    need = {"init", "waf_captcha"}
    ok = need.issubset(seen)
    has_frame = any("iframe" in str(r.get("msg", "")) for r in lines)
    if has_frame:
        print("结论：iframe 内的验证码也被本帧求解器接管了（不再需要人工）")
    if "captcha_solved" in seen:
        print("结论：求解器在真实扩展环境里跑通（captcha_solved）")
    elif "need_human" in seen:
        print("结论：求解链路已接上，但合成题没解开（页面本身不会消失，属预期）—— 至少不再静默")
    elif ok:
        print("结论：注入与检测链路通，未见求解结果（看上面 msg）")
    else:
        print("结论：链路没接上，检查扩展是否被 Chrome 忽略（必须用 CfT）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
