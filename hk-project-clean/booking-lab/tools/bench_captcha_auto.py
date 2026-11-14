# -*- coding: utf-8 -*-
"""对本地复刻题连跑 N 道，统计「自动识别 + 自动拖动」的通过率与提交误差。

用法：python tools\\bench_captcha_auto.py 20
前置：booking-lab\\captcha_mock 已在 127.0.0.1:8777 运行（node server.js）
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import captcha_auto

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
ok = 0
diffs = []
fails = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    for i in range(N):
        pg.goto(f"http://127.0.0.1:8777/?n={i}", wait_until="networkidle")
        pg.wait_for_selector("#handle")
        pg.wait_for_timeout(400)
        r = captcha_auto.solve_page(pg, who=f"bench{i}", log=lambda *_a: None)
        g = pg.evaluate("() => window.__grade || null")
        if r.get("ok") and g and g.get("pass"):
            ok += 1
            diffs.append(abs(float(g.get("diff") or 0)))
        else:
            fails.append({"i": i, "r": r.get("note"), "grade": g})
    b.close()

print(f"通过 {ok}/{N} = {ok / max(1, N) * 100:.1f}%")
if diffs:
    print(f"提交误差 mean={sum(diffs)/len(diffs):.3f}px max={max(diffs):.3f}px")
for f in fails[:5]:
    print("FAIL", f)
