# -*- coding: utf-8 -*-
"""在 8.15 末尾追加实测记录。"""
from pathlib import Path

P = Path("build_doc.py")
t = P.read_text(encoding="utf-8")

anchor = ('''    body(doc, "实现细节：发送在后台线程里做，网络慢或钉钉挂了都不会卡住抢号流程；"
              "同一类消息 5 秒内不重复发，避免 17:00 刷屏。", size=10)
''')

add = anchor + '''
    body(doc, "实测记录（2026-11-08 23:1x 真发）：", size=10, bold=True)
    table(
        doc,
        ["阶段", "返回", "说明"],
        [
            ["未配安全设置", 'errcode 310000　关键词不匹配',
             "机器人当时设的是「自定义关键词」，消息里必须含那个词"],
            ["改成加签后", 'errcode 310000　机器人发送签名不匹配',
             "说明安全设置已切到「加签」，还差 SEC 密钥"],
            ["填入 SEC 密钥", '{"errcode":0,"errmsg":"ok"}',
             "**通道打通**，消息已送达群"],
        ],
        widths=[4.0, 6.4, 5.6],
        size=8.5,
    )
    body(doc, "现在 config.json 里是加签模式：dingtalk_secret 已填、dingtalk_keyword 留空。"
              "notifier.send() 自测返回 True。", size=10)
'''

assert t.count(anchor) == 1, f"anchor={t.count(anchor)}"
t = t.replace(anchor, add)
P.write_text(t, encoding="utf-8")
print("已追加 8.15 实测记录")
