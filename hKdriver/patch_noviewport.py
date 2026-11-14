# -*- coding: utf-8 -*-
"""把 viewport 改为 no_viewport，让 DPR / screen / window 全部取真实值。"""
from pathlib import Path

# grab.py: launch_persistent_context
g = Path("grab.py")
t = g.read_text(encoding="utf-8")
old = '''                    kw = dict(headless=self.cfg.get("headless", False), locale=loc,
                              timezone_id=tz, viewport=H.pick_viewport(), slow_mo=0,
                              permissions=[],
                              args=H.LAUNCH_ARGS)'''
new = '''                    # 不指定 viewport：让浏览器用真实窗口尺寸与系统缩放（DPR），
                    # 否则 screen/DPR 会与真人环境差一截
                    kw = dict(headless=self.cfg.get("headless", False), locale=loc,
                              timezone_id=tz, no_viewport=True, slow_mo=0,
                              permissions=[],
                              args=H.LAUNCH_ARGS)'''
assert t.count(old) == 1, f"grab={t.count(old)}"
t = t.replace(old, new)
# attach 分支 new_context 也不指定 viewport
t = t.replace('ctx = browser.new_context(locale=loc, timezone_id=tz)',
              'ctx = browser.new_context(locale=loc, timezone_id=tz, no_viewport=True)')
t = t.replace('ctx = browser.new_context(locale=loc, timezone_id=tz, viewport=H.pick_viewport())',
              'ctx = browser.new_context(locale=loc, timezone_id=tz, no_viewport=True)')
g.write_text(t, encoding="utf-8")
print("grab.py: no_viewport 已设置")

# check.py
c = Path("check.py")
s = c.read_text(encoding="utf-8")
s = s.replace("viewport=H.pick_viewport()", "no_viewport=True")
c.write_text(s, encoding="utf-8")
print("check.py: no_viewport 已设置")

# audit / fingerprint_check
for name in ("audit_fingerprint.py", "fingerprint_check.py"):
    p = Path(name)
    x = p.read_text(encoding="utf-8")
    x = x.replace("viewport=H.pick_viewport()", "no_viewport=True")
    p.write_text(x, encoding="utf-8")
    print(f"{name}: no_viewport 已设置")
