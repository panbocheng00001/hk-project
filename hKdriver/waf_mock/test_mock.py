# -*- coding: utf-8 -*-
"""test_mock.py —— 用本地 mock 回归「识图 + 算 dx」这一段
=====================================================================
做的事：
    1) 用 headless Chrome 把 mock 页面渲染成图（几何与线上一致）
    2) 用线上同款算法（live_solve.pair_dx2）从图里算 dx
    3) 与页面自己上报的真值（/api/state 的 expect_dx）对比，给出误差

用法：
    python waf_mock/test_mock.py                 # 跑 1 轮
    python waf_mock/test_mock.py --rounds 5      # 跑 5 轮（每题随机）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import cv2                      # noqa: E402
import numpy as np              # noqa: E402

import live_solve as L          # noqa: E402

CHROME = BASE / "cft" / "chrome-win64" / "chrome.exe"
PROFILE = BASE / "runs" / "_mockprof"
SHOT = BASE / "runs" / "_mock_test.png"
PORT = 8766


def state():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=5) as r:
        return json.loads(r.read().decode())


def render(url: str) -> np.ndarray:
    if SHOT.exists():
        SHOT.unlink()
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", f"--user-data-dir={PROFILE}",
        "--window-size=560,660", "--virtual-time-budget=4000",
        f"--screenshot={SHOT}", url,
    ], capture_output=True, timeout=40)
    for _ in range(20):
        if SHOT.exists() and SHOT.stat().st_size > 2000:
            break
        time.sleep(0.2)
    img = cv2.imdecode(np.fromfile(str(SHOT), dtype=np.uint8), cv2.IMREAD_COLOR)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--dir", default="pos", choices=["pos", "neg"])
    a = ap.parse_args()

    url = f"http://127.0.0.1:{PORT}/?dir={a.dir}"
    ok_n = 0
    rows = []
    for i in range(a.rounds):
        img = render(url)
        if img is None:
            print(json.dumps({"round": i, "err": "渲染失败"}, ensure_ascii=False)); break
        panel = L.find_panel2(img)
        if not panel:
            print(json.dumps({"round": i, "err": "找不到面板"}, ensure_ascii=False)); continue
        _, pr = L.pair_dx2(img, panel)
        st = state()
        truth = (st.get("task") or {}).get("expect_dx")
        got = round(pr["dx"], 1) if pr else None
        err = abs(got - truth) if (got is not None and truth is not None) else None
        okflag = err is not None and err <= 6
        ok_n += 1 if okflag else 0
        rows.append(dict(round=i, truth=truth, got=got, err=round(err, 1) if err is not None else None,
                         ok=okflag, fam=(st.get("task") or {}).get("fam"),
                         solid=pr["solid"] if pr else None, hollow=pr["hollow"] if pr else None))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    print(json.dumps({"rounds": a.rounds, "ok": ok_n,
                      "pass_rate": round(ok_n / max(1, len(rows)), 2)}, ensure_ascii=False))
    return 0 if ok_n == len(rows) and rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
