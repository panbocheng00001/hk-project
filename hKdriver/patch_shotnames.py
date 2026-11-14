# -*- coding: utf-8 -*-
"""把 self.shot(...) 里的步骤名去掉序号前缀（序号由 Recorder 自己加）。"""
from pathlib import Path

P = Path("grab.py")
t = P.read_text(encoding="utf-8")

pairs = [
    ('self.shot(page, "01_entry"', 'self.shot(page, "entry"'),
    ('self.shot(page, "02_agreed"', 'self.shot(page, "agreed"'),
    ('self.shot(page, "03_auth"', 'self.shot(page, "auth"'),
    ('self.shot(page, "04_auth_chosen"', 'self.shot(page, "auth_chosen"'),
    ('self.shot(pg, "05_queue"', 'self.shot(pg, "queue"'),
    ('self.shot(page, "05b_country_missing"', 'self.shot(page, "country_missing"'),
    ('self.shot(page, "06_country"', 'self.shot(page, "country"'),
    ('self.shot(page, "07_country_mainland"', 'self.shot(page, "country_mainland"'),
    ('self.shot(page, "08_step2_blank"', 'self.shot(page, "step2_blank"'),
    ('self.shot(page, "09_step2_filled"', 'self.shot(page, "step2_filled"'),
    ('self.shot(page, "10_after_step2"', 'self.shot(page, "after_step2"'),
    ('self.shot(page, f"11_calendar_{attempt}"', 'self.shot(page, f"calendar_{attempt}"'),
    ('self.shot(page, f"12_bureau_{attempt}"', 'self.shot(page, f"bureau_{attempt}"'),
    ('self.shot(page, f"13_date_{attempt}"', 'self.shot(page, f"date_{attempt}"'),
    ('self.shot(page, f"14_slot_{attempt}"', 'self.shot(page, f"slot_{attempt}"'),
    ('self.shot(page, "15_confirm"', 'self.shot(page, "confirm"'),
    ('self.shot(page, "16_submitted"', 'self.shot(page, "submitted"'),
    ('self.shot(page, "17_otp_page"', 'self.shot(page, "otp_page"'),
    ('self.shot(page, "99_result"', 'self.shot(page, "result"'),
]
n = 0
for old, new in pairs:
    c = t.count(old)
    if c:
        t = t.replace(old, new)
        n += c
P.write_text(t, encoding="utf-8")
print(f"已替换 {n} 处步骤名")
