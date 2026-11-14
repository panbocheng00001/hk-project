# -*- coding: utf-8 -*-
"""_win_pos_probe.py —— 找一个「窗口不出现在眼前、外框尺寸又正常」的坐标

问题（2026-11-11 实测）：`--window-position=-32000,-32000` 会被 Windows 夹取，
  外框变成 160x28（p1 票里就记成了这个）。而 outerWidth/outerHeight 是 WAF
  环境采集项之一，160x28 明显是异常特征。
本脚本只开 data: 页，逐个坐标量 outer 尺寸，不发网络请求。
"""
from __future__ import annotations
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")
BASE_ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run",
             "--no-default-browser-check", "--disable-dev-shm-usage",
             "--lang=zh-HK", "--window-size=1366,820"]

POSITIONS = ["-32000,-32000", "-1200,-800", "-400,-300", "2000,2000", "0,0"]

JS = "() => [window.outerWidth, window.outerHeight, window.innerWidth, window.innerHeight]"


def main() -> int:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as pw:
        for pos in POSITIONS:
            try:
                b = pw.chromium.launch(
                    executable_path=CHROME, headless=False,
                    args=BASE_ARGS + [f"--window-position={pos}"],
                    ignore_default_args=["--enable-automation"])
            except Exception as e:
                print(f"  {pos:16} 启动失败 {type(e).__name__}")
                continue
            try:
                ctx = b.new_context(viewport={"width": 1366, "height": 768})
                pg = ctx.new_page()
                pg.goto("data:text/html,<h1>x</h1>", wait_until="commit")
                pg.wait_for_timeout(600)
                outer, inner = None, None
                for _ in range(8):                    # 最多等 2 秒让它稳定
                    outer = pg.evaluate(JS)
                    if outer[0] >= 1000:
                        break
                    pg.wait_for_timeout(250)
                inner = outer
                sane = outer[0] >= 1200 and outer[1] >= 600
                print(f"  {pos:16} outer={outer[0]:>5}x{outer[1]:<5} "
                      f"inner={outer[2]:>5}x{outer[3]:<5} "
                      f"{'✅ 正常' if sane else '❌ 被夹'}")
            finally:
                try:
                    b.close()
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
