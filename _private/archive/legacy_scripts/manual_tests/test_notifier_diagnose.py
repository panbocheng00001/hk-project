# -*- coding: utf-8 -*-
"""验证 notifier 的错误诊断（不联网）。"""
import notifier

CASES = [
    ('{"errcode":310000,"errmsg":"错误描述:机器人发送签名不匹配;"}', "签名"),
    ('{"errcode":310000,"errmsg":"错误描述:关键词不匹配;"}', "关键词"),
    ('{"errcode":660026,"errmsg":"sending too many messages per minute"}', "限流"),
    ('{"errcode":300001,"errmsg":"invalid token"}', "token"),
    ('{"errcode":0,"errmsg":"ok"}', "正常"),
]

for body, tag in CASES:
    print(f"{tag:6} -> {notifier._diagnose(body) or '(无需处理)'}")
