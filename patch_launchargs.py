# -*- coding: utf-8 -*-
"""统一三个脚本的浏览器启动参数（去掉「正受到自动测试软件控制」提示条）。"""
from pathlib import Path

g = Path("grab.py")
t = g.read_text(encoding="utf-8")
old = '''                              args=["--disable-blink-features=AutomationControlled",
                                    "--disable-features=IsolateOrigins,site-per-process"])'''
new = '''                              args=H.LAUNCH_ARGS)'''
assert t.count(old) == 1, f"grab anchor={t.count(old)}"
g.write_text(t.replace(old, new), encoding="utf-8")
print("grab.py 已用统一启动参数")

for name in ("fingerprint_check.py", "check.py"):
    p = Path(name)
    s = p.read_text(encoding="utf-8")
    n = s.count('args=["--disable-blink-features=AutomationControlled"]')
    s = s.replace('args=["--disable-blink-features=AutomationControlled"]', "args=H.LAUNCH_ARGS")
    p.write_text(s, encoding="utf-8")
    print(f"{name} 替换 {n} 处")
