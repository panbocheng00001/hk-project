# -*- coding: utf-8 -*-
"""修正演练命令：--probe 加上 --channel chrome，避免依赖 9222。"""
from pathlib import Path

P = Path("build_doc.py")
t = P.read_text(encoding="utf-8")

old = '"python grab.py --probe --workers 1",'
new = '"python grab.py --probe --workers 1 --channel chrome",'
assert t.count(old) == 1, f"count={t.count(old)}"
t = t.replace(old, new)

old2 = '"若报「文件不存在」：检查 files\\ 下两张图"'
new2 = ('"若提示连不上 9222：这条已加 --channel chrome，不依赖端口；'
        '若报「文件不存在」：检查 files\\ 下两张图"')
if t.count(old2) == 1:
    t = t.replace(old2, new2)
    print("备注已更新")
else:
    print(f"备注未匹配（count={t.count(old2)}），跳过")

P.write_text(t, encoding="utf-8")
print("已修正演练命令")
