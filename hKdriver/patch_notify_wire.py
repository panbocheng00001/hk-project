# -*- coding: utf-8 -*-
"""把通知接到 grab.py / mail_watch.py / check.py，并给 config.json 加 notify 段。"""
import json
from pathlib import Path

# ---------------------------------------------------------------- grab.py
G = Path("grab.py")
t = G.read_text(encoding="utf-8")

t = t.replace("import captcha\n", "import captcha\nimport notifier as N\n", 1)

# 1) 出现验证码需要人工时，推钉钉
anchor = '''    def captcha_pack(self, page, info: dict, note: str = "") -> None:'''
add = '''    def notify_captcha(self, kind: str, waited: int = 0) -> None:
        if kind in captcha.NEED_HUMAN:
            N.captcha_need_human(kind, waited)

    def captcha_pack(self, page, info: dict, note: str = "") -> None:'''
assert t.count(anchor) == 1
t = t.replace(anchor, add)

# 2) 放行进入第 2 步（无名额时不推）
old = '''        no_quota = "没有可供预约的时段" in txt
        if no_quota:
            log(self.who, "⚠ 系统提示：该服务现时没有可预约时段")'''
new = '''        no_quota = "没有可供预约的时段" in txt
        if no_quota:
            log(self.who, "⚠ 系统提示：该服务现时没有可预约时段")
        else:
            log(self.who, "✅ 已放行：进入第2步填写页（20 分钟倒计时开始）")
            N.admitted(f"{self.who} · {page.url[:80]}")'''
assert t.count(old) == 1
t = t.replace(old, new)

# 3) 验证码 guard 调用处以 lambda 传入通知
old_g = '''        if cst == "timeout":
            self.result = {"stage": "captcha_timeout", "url": page.url}
            return'''
new_g = '''        if cst == "timeout":
            self.result = {"stage": "captcha_timeout", "url": page.url}
            N.failed("验证码超时，本轮放弃")
            return'''
assert t.count(old_g) >= 1
t = t.replace(old_g, new_g)

# 4) 成功 / 失败通知
old_ok = '''        if ok:
            _STOP.set()
            beep(5)
            log(self.who, "★★★ 拿到预约，请立刻检查电邮 tdabs@td.gov.hk ★★★")'''
new_ok = '''        if ok:
            _STOP.set()
            beep(5)
            log(self.who, "★★★ 拿到预约，请立刻检查电邮 tdabs@td.gov.hk ★★★")
            ref = ""
            for line in txt.splitlines():
                if "编号" in line and len(line) < 80:
                    ref = line.strip()
                    break
            N.success(ref)
        else:
            N.failed(f"{self.who} 走完流程但未确认成功：url={page.url[:80]}")'''
assert t.count(old_ok) == 1
t = t.replace(old_ok, new_ok)

G.write_text(t, encoding="utf-8")
print("grab.py 已接入通知")

# ---------------------------------------------------------------- mail_watch.py
M = Path("mail_watch.py")
m = M.read_text(encoding="utf-8")
m = m.replace("from pathlib import Path\n", "from pathlib import Path\n\nimport notifier as N\n", 1)
old_m = '''    OTP_FILE.write_text(hit, encoding="utf-8")
    log(f"★★★ 验证码 {hit} 已写入 otp.txt，grab.py 会在 2 秒内填入 ★★★")
    beep()'''
new_m = '''    OTP_FILE.write_text(hit, encoding="utf-8")
    log(f"★★★ 验证码 {hit} 已写入 otp.txt，grab.py 会在 2 秒内填入 ★★★")
    beep()
    N.otp_arrived(hit)'''
assert m.count(old_m) == 1
m = m.replace(old_m, new_m)
M.write_text(m, encoding="utf-8")
print("mail_watch.py 已接入通知")

# ---------------------------------------------------------------- check.py
C = Path("check.py")
c = C.read_text(encoding="utf-8")
c = c.replace("import human as H\n", "import human as H\nimport notifier as N\n", 1)
old_c = '''        if code == 0:
            if a.ring:
                ring()
            sys.exit(0)'''
new_c = '''        if code == 0:
            if a.ring:
                ring()
            N.seat_open(url=msg[:160])
            sys.exit(0)'''
assert c.count(old_c) == 1
c = c.replace(old_c, new_c)
old_c2 = '''        if code == 30:
            wait = max(gap, 300) if gap else 0'''
new_c2 = '''        if code == 30:
            N.site_error(msg[:120])
            wait = max(gap, 300) if gap else 0'''
assert c.count(old_c2) == 1
c = c.replace(old_c2, new_c2)
C.write_text(c, encoding="utf-8")
print("check.py 已接入通知")

# ---------------------------------------------------------------- config.json
CFG = Path("config.json")
cfg = json.loads(CFG.read_text(encoding="utf-8"))
cfg.setdefault("notify", {
    "_说明": "钉钉自定义机器人。没配置时所有通知静默跳过，不影响抢号。",
    "enabled": True,
    "dingtalk_webhook": "",
    "dingtalk_secret": "",
    "at_mobiles": ["13631539547"],
    "min_interval": 5,
})
CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("config.json 已加 notify 段")
