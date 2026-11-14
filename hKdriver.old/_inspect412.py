# -*- coding: utf-8 -*-
"""_inspect412.py —— 看清存下来的 412 挑战页长什么样（只读，不发网络）"""
import io
import re
import sys

PAGE = "runs/20261109-163025/w1/01_entry.html"
JS = "runs/rev/abs3_waf_js.js"
META = "runs/rev/waf_meta.txt"

s = io.open(PAGE, encoding="utf-8", errors="replace").read()
print(f"页面: {PAGE}  {len(s)} 字符")
print("\n--- script 标签 ---")
for m in re.finditer(r"<script[^>]*>", s):
    print("  ", m.group(0)[:170].replace("\n", " "))
print("\n--- meta 标签 ---")
for m in re.finditer(r"<meta[^>]*>", s[:4000]):
    print("  ", m.group(0)[:200])
print("\n--- 配置对象出现次数 ---")
for key in ("$_ts", "nsd", "cd=", "HIK0"):
    print(f"  {key!r:8} x {s.count(key)}")
i = s.find("$_ts")
print("\n--- 配置片段 ---")
print(" ", s[max(0, i - 150):i + 320].replace("\n", " ")[:460])
print("\n--- 结尾 ---")
print(" ", s[-260:].replace("\n", " "))

try:
    j = io.open(JS, encoding="utf-8", errors="replace").read()
    print(f"\n挑战 JS: {JS}  {len(j)} 字符")
    for key in ("WebAssembly", "debugger", "constructor", "getImageData", "WEBGL_debug_renderer_info"):
        print(f"  {key!r:26} x {j.count(key)}")
    print("  首行:", j[:200].replace("\n", " "))
except Exception as e:
    print("JS 读不到:", e)

try:
    print("\nmeta 文本:", io.open(META, encoding="utf-8", errors="replace").read()[:200])
except Exception:
    pass
sys.exit(0)
