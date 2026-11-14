# -*- coding: utf-8 -*-
"""插入 8.15「钉钉通知」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
    h2(doc, "8.15 推到钉钉：抢到了 / 要你点验证码，手机上就能看到")
    body(doc, "17:00 那十几分钟你未必一直盯着电脑。现在关键事件都会推到钉钉群，手机响一下就知道。", size=10)

    table(
        doc,
        ["什么时候推", "消息标题", "里面有什么"],
        [
            ["名额出现", "有名额了！", "提示立刻跑 grab.py，附当前状态"],
            ["排队放行、进入填写页", "已放行，进入填写页", "20 分钟倒计时开始，别磨蹭"],
            ["出现要人工的验证码", "需要你完成验证码", "类型（图片题/图形码/hCaptcha）+ 已等待秒数"],
            ["电邮验证码到达", "验证码已到达", "**OTP 明文**，已写进 otp.txt"],
            ["抢到预约", "★ 抢到预约了", "预约编号 + 提示去查确认邮件"],
            ["本轮失败 / 验证码超时", "本轮没抢到 / 出错", "原因与当前 URL"],
            ["官方 502 / 维护", "官方站点异常", "提示等恢复，别白跑"],
        ],
        widths=[4.4, 4.6, 7.0],
        size=8.5,
    )

    body(doc, "需要你提供的东西（三样，两分钟搞定）：", size=10, bold=True)
    steps = [
        ("① 建机器人",
         "钉钉群 → 右上角设置 → 机器人 → 添加机器人 → 自定义（通过 Webhook 接入）→ 起个名字。"),
        ("② 拿 Webhook",
         "添加完会给你一个地址，形如：https://oapi.dingtalk.com/robot/send?access_token=一长串"),
        ("③ 安全设置选一种",
         "推荐「加签」，会给你一个以 SEC 开头的密钥；也可以用「自定义关键词」（填「预约」）。"
         "两种都填进配置，脚本都支持。"),
    ]
    for title, text in steps:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(title)
        set_cn_font(r)
        r.font.size = Pt(10.5)
        r.bold = True
        body(doc, text, size=10, indent=0.6)

    body(doc, "填进 config.json：", size=10, bold=True)
    code_block(
        doc,
        [
            '"notify": {',
            '  "enabled": true,',
            '  "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxxx",',
            '  "dingtalk_secret": "SECxxxx",        // 选了加签就填，没选留空',
            '  "dingtalk_keyword": "预约",          // 选了自定义关键词就填，没选留空',
            '  "at_mobiles": ["13631539547"],      // 要 @ 谁，可留空',
            '  "min_interval": 5                    // 同样内容 5 秒内不重复发',
            '}',
        ],
    )

    body(doc, "配好后发一条测试消息：", size=10, bold=True)
    code_block(
        doc,
        [
            "python -c \"import notifier; print(notifier.enabled()); notifier.send('测试','钉钉通道已打通')\"",
        ],
    )
    body(doc, "返回 True 且群里收到消息，就算通了。返回 False 会打印具体原因（HTTP 码/钉钉错误码）。", size=10)

    callout(
        doc,
        "三个容易踩的点",
        "① 机器人安全设置选了「自定义关键词」，消息里就必须含那个词，否则钉钉直接拒收——"
        "所以 dingtalk_keyword 要跟你在钉钉里设置的词一模一样；"
        "② Webhook 等于「往这个群发消息的权限」，别贴到群里/仓库里；"
        "③ 没配置时所有通知静默跳过，不影响抢号——这一点自测过。",
    )

    body(doc, "实现细节：发送在后台线程里做，网络慢或钉钉挂了都不会卡住抢号流程；"
              "同一类消息 5 秒内不重复发，避免 17:00 刷屏。", size=10)

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.15 节")
