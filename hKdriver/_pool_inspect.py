# -*- coding: utf-8 -*-
"""临时：查看并清理票池里出口不可信的票（WARP/旧死代理）。"""
import sqlite3, sys, time
from pathlib import Path

DB = Path(__file__).resolve().parent / "runs" / "pool.sqlite3"
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

print("=== 表 ===")
for r in c.execute("select name from sqlite_master where type='table'"):
    print(" ", r[0])

print("\n=== slots 字段 ===")
cols = [d[1] for d in c.execute("pragma table_info(slots)")]
print(" ", cols)

print("\n=== 现有票 ===")
for r in c.execute("select * from slots"):
    d = dict(r)
    print(" ", {k: d[k] for k in cols})

if "--drop" in sys.argv:
    targets = sys.argv[sys.argv.index("--drop") + 1].split(",")
    for t in targets:
        n = c.execute("delete from slots where ip_id=?", (t.strip(),)).rowcount
        print(f"  删除 {t.strip()}: {n}")
    c.commit()
    print("\n=== 删除后 ===")
    for r in c.execute("select * from slots"):
        d = dict(r)
        print(" ", {k: d[k] for k in cols})
