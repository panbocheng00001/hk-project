# -*- coding: utf-8 -*-
"""在 8.15 的提醒清单里补两条：排队位、OTP 超时。"""
from pathlib import Path

P = Path("build_doc.py")
t = P.read_text(encoding="utf-8")

old = '''            ["名额出现", "有名额了！", "提示立刻跑 grab.py，附当前状态"],
            ["排队放行、进入填写页", "已放行，进入填写页", "20 分钟倒计时开始，别磨蹭"],'''

new = '''            ["名额出现", "有名额了！", "提示立刻跑 grab.py，附当前状态"],
            ["抢到排队位", "已拿到排队位", "开始排队了，别关窗口、别刷新"],
            ["排队放行、进入填写页", "已放行，进入填写页", "**20 分钟倒计时开始**，别磨蹭"],
            ["OTP 等太久（每 120 秒，最多 3 次）", "⚠ OTP 没收到",
             "写明**是哪个邮箱**、已等多少秒、三条排查建议"],'''

assert t.count(old) == 1, f"anchor={t.count(old)}"
t = t.replace(old, new)
P.write_text(t, encoding="utf-8")
print("已补两条提醒")
