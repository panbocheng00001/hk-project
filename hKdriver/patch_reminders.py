# -*- coding: utf-8 -*-
"""补齐三类提醒：
1) 进入排队页 → 推「已拿到排队位」
2) OTP 等待超时（grab.py 内）→ 推「OTP 没收到（含邮箱地址）」
3) mail_watch.py 加 --alert-after，超时也推
"""
from pathlib import Path

# ---------------------------------------------------------------- grab.py
G = Path("grab.py")
t = G.read_text(encoding="utf-8")

# 1) 排队页首次出现时推送
old_q = '''                if self.on_queue(pg) and not self._queue_shot_done:
                    self._queue_shot_done = True
                    self.shot(pg, "queue", "排队页（abs4，随机分位中）")'''
new_q = '''                if self.on_queue(pg) and not self._queue_shot_done:
                    self._queue_shot_done = True
                    self.shot(pg, "queue", "排队页（abs4，随机分位中）")
                    log(self.who, "🎫 已拿到排队位")
                    N.queued(f"{self.who} · {pg.url[:80]}")'''
assert t.count(old_q) == 1, f"queue anchor={t.count(old_q)}"
t = t.replace(old_q, new_q)

# 2) OTP 等待超时推送（在等 otp.txt 的循环里）
old_otp = '''        # 7) OTP
        for _ in range(120):
            if _STOP.is_set():
                return
            code = read_otp()'''
new_otp = '''        # 7) OTP
        otp_t0 = time.time()
        otp_alerted = 0
        otp_email = (cfg.get("applicant") or {}).get("email", "未配置")
        for _ in range(120):
            if _STOP.is_set():
                return
            code = read_otp()
            if not code:
                waited = int(time.time() - otp_t0)
                # 每 120 秒提醒一次，最多 3 次：告诉你是哪个邮箱、等了多久
                stage = waited // 120
                if waited >= 120 and stage > otp_alerted and stage <= 3:
                    otp_alerted = stage
                    log(self.who, f"⚠ OTP 已等 {waited}s 未到（邮箱 {otp_email}）")
                    N.otp_missing(otp_email, waited)'''
assert t.count(old_otp) == 1, f"otp anchor={t.count(old_otp)}"
t = t.replace(old_otp, new_otp)

G.write_text(t, encoding="utf-8")
print("grab.py：已加排队提醒 + OTP 超时提醒")

# ---------------------------------------------------------------- mail_watch.py
M = Path("mail_watch.py")
m = M.read_text(encoding="utf-8")

# 在 once() 里加上"本轮是否抓到"的信息返回（已有 hit），main 里做超时判断
old_main = '''    ap.add_argument("--test", action="store_true", help="只测登录")
    a = ap.parse_args()

    conf = load_conf()
    gap = max(5, a.loop) if a.loop else 0'''
new_main = '''    ap.add_argument("--test", action="store_true", help="只测登录")
    ap.add_argument("--alert-after", type=int, default=180,
                    help="等多少秒还没抓到 OTP 就推钉钉（默认 180，0=不推）")
    a = ap.parse_args()

    conf = load_conf()
    gap = max(5, a.loop) if a.loop else 0
    t_start = time.time()
    alerted = 0'''
assert m.count(old_main) == 1, f"main anchor={m.count(old_main)}"
m = m.replace(old_main, new_main)

old_tail = '''        if code and not a.dry:
            return
        if not gap:
            return
        time.sleep(gap)'''
new_tail = '''        if code and not a.dry:
            return
        # 等太久还没有 OTP：推一条，明确写出是哪个邮箱、等了多久
        if a.alert_after and not a.dry:
            waited = int(time.time() - t_start)
            stage = waited // max(1, a.alert_after)
            if waited >= a.alert_after and stage > alerted and stage <= 5:
                alerted = stage
                log(f"⚠ 已等 {waited}s 仍未抓到 OTP（邮箱 {conf.get('email')}）")
                N.otp_missing(conf.get("email", "未配置"), waited)
        if not gap:
            return
        time.sleep(gap)'''
assert m.count(old_tail) == 1, f"tail anchor={m.count(old_tail)}"
m = m.replace(old_tail, new_tail)

M.write_text(m, encoding="utf-8")
print("mail_watch.py：已加 --alert-after 超时提醒")
