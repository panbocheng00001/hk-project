# -*- coding: utf-8 -*-
"""_inspect412b.py —— 为什么离线复放里 $_ts 是空的"""
import io
import re

s = io.open("runs/20261109-163025/w1/01_entry.html", encoding="utf-8",
            errors="replace").read()
print("长度", len(s))
print("<script 数:", len(re.findall(r"<script", s)), " </script> 数:", s.count("</script>"))
print("CSP meta:", "Content-Security-Policy" in s, " nonce:", "nonce=" in s)
for key in ("_$c3", "$_ts.cd", "nonce", "integrity=", "type=\"module\""):
    print(f"  {key!r:22} x {s.count(key)}")

# 依次打印每个 script 的内容长度与开头
for i, m in enumerate(re.finditer(r"<script([^>]*)>(.*?)</script>", s, re.S)):
    attrs, body = m.group(1), m.group(2)
    print(f"[{i}] attrs={attrs[:80]!r}  body {len(body)} 字"
          f"  开头={body[:90]!r}")

# $_ts.cd 与 chall.js 的先后
i_cd = s.find("$_ts.cd")
i_js = s.find("8qcykgusbcwc.5057567.js")
i_c3 = s.find("_$c3")
print(f"\n位置: $_ts.cd={i_cd}  chall.js={i_js}  _$c3={i_c3}")
print("顺序:", "<".join(["cd" if i_cd < i_js else "js",
                        "cd" if i_c3 < i_js else "js"]))
