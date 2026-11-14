# -*- coding: utf-8 -*-
"""收紧并发：workers 默认 2、启动错峰可调、超 2 条给提示；并加请求节奏说明。"""
import json
from pathlib import Path

g = Path("grab.py")
t = g.read_text(encoding="utf-8")

old = '''    ap.add_argument("--workers", type=int, default=1)'''
new = '''    ap.add_argument("--workers", type=int, default=1,
                    help="同时排几条队列。超过 2 条会提高同 IP 并发，容易被限速")
    ap.add_argument("--stagger", type=float, default=2.5,
                    help="多条队列之间的启动间隔秒数（默认 2.5，别设太小）")'''
assert t.count(old) == 1, f"workers anchor={t.count(old)}"
t = t.replace(old, new)

old2 = '''    ws = [Worker(i + 1, cfg, a.probe, run_dir) for i in range(max(1, a.workers))]
    for w in ws:
        w.start()
        time.sleep(1.5)'''
new2 = '''    n_workers = max(1, a.workers)
    if n_workers > 2:
        print(f"提示：{n_workers} 条队列会让同一出口 IP 的并发翻倍，官方 WAF 可能限速；"
              f"建议 2 条（--workers 2）。要更多请确认站点稳定。")
    ws = [Worker(i + 1, cfg, a.probe, run_dir) for i in range(n_workers)]
    for w in ws:
        w.start()
        time.sleep(max(0.5, a.stagger))'''
assert t.count(old2) == 1, f"start anchor={t.count(old2)}"
t = t.replace(old2, new2)

g.write_text(t, encoding="utf-8")
print("grab.py：已加 --stagger 与并发提示")

cfg = Path("config.json")
c = json.loads(cfg.read_text(encoding="utf-8"))
c["workers"] = 2
cfg.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
print("config.json：workers 已改为 2")
