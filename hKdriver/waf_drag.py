# -*- coding: utf-8 -*-
"""waf_drag.py —— 用真实鼠标（SendInput）过华为 WAF 的形状滑块
=====================================================================
为什么需要它：
    chrome.debugger 的 Input.dispatchMouseEvent 对这个 widget 无效（17:28 实测：
    连续 4 次样本 sha256 完全一致，图形 0 位移）。真实 SendInput 是硬件级输入，
    widget 无法区分。

流程：
    1) 把目标窗口前置（AttachThreadInput 绕过前台锁）
    2) 抓全屏 → 找深色验证码面板 → 在面板内找「实心/空心」配对，算 dx
    3) 找滑块手柄（蓝色圆点）→ SendInput 按下 → 分段移动 → 抬起
    4) 抓屏复核位移；不够就按实际比例补拖

用法：
    python waf_drag.py --hwnd 2954318            # 自动算 dx 并拖一次
    python waf_drag.py --hwnd 2954318 --dx 57.2 # 指定 dx
    python waf_drag.py --hwnd 2954318 --submit  # 拖完点 Submit
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import math
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageGrab

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- 输入
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _screen_size():
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),      # 虚拟桌面原点
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))      # 虚拟桌面宽高


def move_to(x: float, y: float):
    """虚拟桌面口径的绝对移动。

    2026-11-16 17:44 实测：用 GetSystemMetrics(0/1)（主屏尺寸）归一化会偏
    （目标 690 → 实际 531）；改用虚拟桌面（76/77/78/79）后逐点精确命中。
    """
    ox, oy, vw, vh = _screen_size()
    ax = int((x - ox) * 65535 / max(1, vw - 1))
    ay = int((y - oy) * 65535 / max(1, vh - 1))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(ax, ay, 0, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def button(down: bool):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# ---------------------------------------------------------------- 窗口
def _enum_windows():
    out = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, l):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(h, buf, n + 2)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((int(h), pid.value, buf.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def _w1_pids():
    """命令行含 chrome_ext_w1 的 chrome 进程。"""
    pids = set()
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                            "Where-Object { $_.CommandLine -match 'chrome_ext_w1' } | "
                            "Select-Object -ExpandProperty ProcessId"],
                           capture_output=True, text=True, timeout=25)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    except Exception:
        pass
    return pids


def find_w1_window():
    pids = _w1_pids()
    for h, pid, title in _enum_windows():
        if pid in pids and any(k in title for k in ("Continue", "TDABS", "預約", "预约", "td.gov.hk", "Chrome for Testing")):
            return h, title
    # 退一步：只按标题找
    for h, pid, title in _enum_windows():
        if any(k in title for k in ("Continue - Google Chrome", "Chrome for Testing")):
            return h, title
    return 0, ""


def focus(hwnd: int) -> bool:
    user32.ShowWindow(hwnd, 9)                      # SW_RESTORE
    time.sleep(0.2)
    if user32.GetForegroundWindow() == hwnd:
        return True
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(user32.AttachThreadInput(tid_me, tid_fg, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    ok = user32.GetForegroundWindow() == hwnd
    if attached:
        user32.AttachThreadInput(tid_me, tid_fg, False)
    return ok


# ---------------------------------------------------------------- 视觉
def grab():
    return np.array(ImageGrab.grab())[:, :, ::-1].copy()      # RGB→BGR


def find_panel(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 70).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    cnts, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        ar = w / float(max(1, h))
        if w < 240 or h < 240 or not (0.85 < ar < 1.15):
            continue
        if best is None or w * h > best[2] * best[3]:
            best = (x, y, w, h)
    return best


def find_handle(img, panel):
    """滑轨上的蓝色圆点（BGR 高蓝低红）。"""
    px, py, pw, ph = panel
    roi = img[py + int(ph * 0.7):py + ph, px:px + pw]
    b, g, r = (roi[:, :, i].astype(int) for i in range(3))
    blue = ((b > 140) & (g > 55) & (g < 175) & (r < 120)).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if not (10 <= w <= 44 and 10 <= h <= 44):
            continue
        if abs(w - h) > 12:
            continue
        M = cv2.moments(c)
        if M["m00"] <= 0:
            continue
        cx = px + M["m10"] / M["m00"]
        cy = py + int(ph * 0.7) + M["m01"] / M["m00"]
        if best is None or w > best[2]:
            best = (cx, cy, w)
    return best


def shapes_in_panel(img, panel):
    px, py, pw, ph = panel
    roi = img[py + int(ph * 0.12):py + int(ph * 0.72), px:px + pw]     # 跳过文案与滑轨
    q = (roi // 8).reshape(-1, 3)
    code = (q[:, 0].astype(np.int32) * 1024 + q[:, 1].astype(np.int32) * 32 + q[:, 2])
    vals, counts = np.unique(code, return_counts=True)
    bgc = vals[counts.argmax()]
    bg = np.array([bgc // 1024, (bgc // 32) % 32, bgc % 32], dtype=np.float32) * 8 + 4

    diff = np.linalg.norm(roi.astype(np.float32) - bg, axis=2)
    mask = (diff > 42).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        area = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)
        if area < 120 or w < 12 or h < 12 or w > 90 or h > 90:
            continue
        kx, ky = max(1, int(w * 0.35)), max(1, int(h * 0.35))
        core = mask[y + ky:y + h - ky, x + kx:x + w - kx]
        core_fill = cv2.countNonZero(core) / float(core.size) if core.size else 0
        out.append(dict(solid=core_fill > 0.55, w=w, h=h, area=int(area),
                        cx=px + x + w / 2.0, cy=py + int(ph * 0.12) + y + h / 2.0))
    return out


def pair_dx(shapes):
    solids = [s for s in shapes if s["solid"]]
    hollows = [s for s in shapes if not s["solid"]]
    best = None
    for s in solids:
        for t in hollows:
            if abs(t["cy"] - s["cy"]) > 40:
                continue
            sc = -abs(s["w"] - t["w"]) * 2 - abs(s["h"] - t["h"]) * 2 - abs(s["cy"] - t["cy"]) * 0.8
            if best is None or sc > best[0]:
                best = (sc, s, t)
    if not best:
        return None
    _, s, t = best
    return dict(dx=t["cx"] - s["cx"], solid=[round(s["cx"]), round(s["cy"])],
                hollow=[round(t["cx"]), round(t["cy"])])


# ---------------------------------------------------------------- 拖动
def drag(handle, dx, steps=24, jitter=1.2):
    hx, hy = handle[0], handle[1]
    # 真人会先把指针移过来
    move_to(hx - 90, hy + 40)
    time.sleep(random.uniform(0.08, 0.18))
    move_to(hx - 2, hy + 1)
    time.sleep(random.uniform(0.10, 0.22))
    move_to(hx, hy)
    time.sleep(random.uniform(0.08, 0.16))
    button(True)
    time.sleep(random.uniform(0.10, 0.20))
    for i in range(1, steps + 1):
        t = i / steps
        # 先快后慢，带轻微超调回归
        e = 1 - (1 - t) ** 2.2
        x = hx + dx * e + random.uniform(-jitter, jitter) * (1 - t * 0.7)
        y = hy + random.uniform(-0.8, 0.8)
        move_to(x, y)
        time.sleep(random.uniform(0.008, 0.022))
    time.sleep(random.uniform(0.15, 0.3))
    button(False)
    time.sleep(random.uniform(0.2, 0.35))


# 行程比：滑轨走 1px，图形只走 0.317px（2026-11-16 实测，见 验证码特征_华为WAF形状滑块.md）
# 所以要消除 dx，手柄必须走 dx / TRAVEL_RATIO ≈ 3.15 × dx
TRAVEL_RATIO = 0.317


def solve_once(hwnd, scale=1.0, submit=True, dry=False):
    """返回 dict：找到面板并处理的一轮结果。"""
    if hwnd:
        focus(hwnd)          # 干跑也要前置：否则抓到的屏是别的窗口
    img = grab()
    panel = find_panel(img)
    if not panel:
        return dict(found=False)
    handle = find_handle(img, panel)
    shapes = shapes_in_panel(img, panel)
    pr = pair_dx(shapes)
    res = dict(found=True, panel=panel,
               handle=[round(handle[0]), round(handle[1]), handle[2]] if handle else None,
               pair=dict(dx=round(pr["dx"], 1), solid=pr["solid"], hollow=pr["hollow"]) if pr else None)
    if dry:
        return res
    if not handle or not pr:
        return res
    # 图形位移 → 手柄行程（比例换算，不再按 1:1）
    dx = (pr["dx"] / max(0.05, TRAVEL_RATIO)) * scale
    res["drag_px"] = round(dx, 1)
    if abs(dx) > 3:                                     # 还差才拖
        drag(handle, dx)
        time.sleep(0.45)
        img2 = grab()
        pr2 = pair_dx(shapes_in_panel(img2, panel))
        res["after"] = dict(need_left=round(pr2["dx"], 1) if pr2 else None)
        if pr2 and abs(pr2["dx"]) > 6:                  # 补一次（同样按比例）
            h2 = find_handle(img2, panel)
            if h2:
                drag(h2, pr2["dx"] / max(0.05, TRAVEL_RATIO))
                time.sleep(0.4)
                img3 = grab()
                pr3 = pair_dx(shapes_in_panel(img3, panel))
                res["after2"] = dict(need_left=round(pr3["dx"], 1) if pr3 else None)
    if submit:
        px, py, pw, ph = panel
        sx, sy = px + pw / 2, py + int(ph * 0.945)
        move_to(sx, sy)
        time.sleep(0.12)
        button(True); time.sleep(0.07); button(False)
        res["submitted"] = [round(sx), round(sy)]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, default=0, help="0 = 自动找 w1 窗口")
    ap.add_argument("--dx", type=float, default=None)
    ap.add_argument("--scale", type=float, default=1.0, help="dx 放大倍数")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--watch", type=int, default=0, help="持续监听 N 秒，出现验证码就解")
    a = ap.parse_args()

    hwnd = a.hwnd
    if not hwnd:
        hwnd, title = find_w1_window()
        if hwnd:
            print(json.dumps({"window": hwnd, "title": title}, ensure_ascii=False))

    if a.watch:
        t0 = time.time()
        last_sig = None
        solved = 0
        while time.time() - t0 < a.watch:
            h = hwnd or find_w1_window()[0]
            r = solve_once(h, a.scale, submit=(not a.submit and True) or a.submit, dry=a.dry)
            if r.get("found"):
                sig = (tuple(r["panel"]), r["pair"]["dx"] if r["pair"] else None)
                if r["pair"] and abs(r["pair"]["dx"]) > 3 and sig != last_sig:
                    solved += 1
                    print(json.dumps(dict(t=time.strftime("%H:%M:%S"), n=solved, **{k: r[k] for k in r if k != "found"}), ensure_ascii=False))
                    last_sig = sig
                elif r["pair"] is None:
                    print(json.dumps(dict(t=time.strftime("%H:%M:%S"), note="面板在但配不上对", panel=r["panel"]), ensure_ascii=False))
            else:
                print(json.dumps(dict(t=time.strftime("%H:%M:%S"), note="没有验证码面板"), ensure_ascii=False))
                if solved:
                    print(json.dumps({"ok": True, "note": "验证码已消失，视为通过", "rounds": solved}, ensure_ascii=False))
                    return 0
            time.sleep(1.6)
        print(json.dumps({"ok": solved > 0, "rounds": solved}, ensure_ascii=False))
        return 0

    if not hwnd:
        print(json.dumps({"ok": False, "err": "找不到 w1 浏览器窗口"}, ensure_ascii=False))
        return 2

    if a.dx is not None:
        if a.dry:
            print(json.dumps({"dry": True, "dx": a.dx}, ensure_ascii=False)); return 0
        focus(hwnd)
        img = grab()
        panel = find_panel(img)
        handle = find_handle(img, panel) if panel else None
        if not handle:
            print(json.dumps({"ok": False, "err": "找不到滑块手柄"}, ensure_ascii=False)); return 4
        drag(handle, a.dx)
        print(json.dumps({"ok": True, "dragged": a.dx, "handle": [round(handle[0]), round(handle[1])]}, ensure_ascii=False))
        return 0

    r = solve_once(hwnd, a.scale, submit=a.submit, dry=a.dry)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if not r.get("found"):
        return 3
    return 0 if (a.dry or r.get("pair")) else 4


if __name__ == "__main__":
    raise SystemExit(main())
