# -*- coding: utf-8 -*-
"""把 docx 表格文本里误写的字面 \\n 改成真正的换行转义 \n。"""
from pathlib import Path

P = Path("build_doc.py")
t = P.read_text(encoding="utf-8")
before = t.count("\\\\n")
t = t.replace("\\\\n", "\\n")
P.write_text(t, encoding="utf-8")
print(f"已把 {before} 处字面 \\\\n 改为换行转义")
