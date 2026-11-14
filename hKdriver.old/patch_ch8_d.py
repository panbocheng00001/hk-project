# -*- coding: utf-8 -*-
"""在 build_doc.py 里插入 8.11「自动收验证码」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
    h2(doc, "8.11 自动收验证码（可选，但推荐）")
    body(doc, "grab.py 每 2 秒读一次 otp.txt，所以只要把验证码自动写进去，整条链路就不用手动抄码：", size=10)
    code_block(
        doc,
        [
            "邮箱收到 tdabs@td.gov.hk 的邮件",
            "   ↓  mail_watch.py 每 15 秒看一次收件箱",
            "认出 From 含 td.gov.hk 的邮件 → 抓 4–8 位验证码",
            "   ↓",
            "写进 otp.txt",
            "   ↓  grab.py 每 2 秒读一次",
            "自动填入页面 → 继续 → 选日期时段",
        ],
        size=8.5,
    )

    body(doc, "配置：mail.json（凭据文件，已填好）", size=10, bold=True)
    code_block(
        doc,
        [
            "{",
            '  "email": "KalbEssick4488@outlook.com",',
            '  "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",',
            '  "refresh_token": "M.C511_..."',
            "}",
        ],
    )

    body(doc, "三条命令：", size=10, bold=True)
    code_block(
        doc,
        [
            "python mail_watch.py --test     # 测登录，只连一下，不发请求给官方",
            "python mail_watch.py --dry      # 列出最近 10 封邮件（看有没有被拦、进垃圾箱）",
            "python mail_watch.py --loop 15  # 盯着，抓到验证码就写 otp.txt 并响铃",
        ],
    )

    body(doc, "明天抢号时开两个窗口：", size=10, bold=True)
    code_block(
        doc,
        [
            "窗口 1：python mail_watch.py --loop 15",
            "窗口 2：python grab.py --workers 3",
        ],
    )

    callout(
        doc,
        "令牌轮换：只能留一份在用",
        "refresh_token 每刷新一次就换新，旧的立刻作废。所以：①  别在邮箱管理后台再点「批量刷新令牌」，"
        "否则脚本这边立刻失效；②  脚本已把 access_token 和过期时间写进 mail.json，重复运行不会反复刷新；"
        "③  抢号前 30 分钟先跑 mail_watch.py --test，登不上就立刻换自己注册的邮箱。",
    )

    body(doc, "实测记录（2026-11-08 22:17）：", size=10, bold=True)
    table(
        doc,
        ["检查项", "结果"],
        [
            ["刷新令牌 + IMAP 登录", "成功，8 个文件夹，读到 4 封邮件"],
            ["163 → 该 Outlook 跨域投递", "成功（收到一封来自 13631539547@163.com 的测试信）"],
            ["验证码提取正则", "7/7 通过（中英文、OTP 前缀、8 位流水号不误抓）"],
            ["otp.txt → grab.py 读取", "成功（写入 888999，grab 立即读到）"],
            ["微软安全通知", "出现「安全信息已删除」「新应用已连接」——说明该号没有辅助邮箱，丢了无法找回"],
        ],
        widths=[6.0, 10.0],
        size=9,
    )

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.11 节")
