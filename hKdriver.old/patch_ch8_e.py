# -*- coding: utf-8 -*-
"""插入 8.12「全程录制与复盘」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
    h2(doc, "8.12 全程录制：每一步的截图 + 页面代码（为优化脚本留档）")
    body(doc, "抢到号那一刻只有 20 分钟，根本来不及研究页面长什么样。所以 grab.py 现在会"
              "自动把每一步的截图、页面源码、DOM 结构全部存下来，事后对着看就能改脚本。", size=10)

    body(doc, "每次运行生成一个独立目录：", size=10, bold=True)
    code_block(
        doc,
        [
            "runs\\20261109-170012\\",
            "  w1\\",
            "    01_entry.png  01_entry.html  01_entry.json      ← 每一帧三件套",
            "    02_agreed.png ...",
            "    steps.jsonl        每行一条记录（序号/时间/URL/备注/字段数）",
            "    index.md           时间线表格，先看这个",
            "  w2\\  w3\\              其余队列各自一份",
        ],
    )

    body(doc, "会录哪些时刻（共 19 个节点，序号由脚本自动加）：", size=10, bold=True)
    table(
        doc,
        ["文件名", "录的时机", "为什么值得留"],
        [
            ["entry", "入口页加载完", "确认勾选框何时出现（实测要等 40 秒渲染）"],
            ["agreed", "勾选同意后", "确认勾选状态与按钮是否可用"],
            ["auth", "第1步 认证页", "两种身份验证方式的原始 DOM"],
            ["auth_chosen", "选「不使用智方便」后", "确认单选状态，避免点歪"],
            ["queue", "进入 abs4 排队", "排队页结构，用来分析等待时长"],
            ["country / country_mainland", "选签发国家/地方 / 选内地后", "单选值、label 文案"],
            ["step2_blank", "第2步「输入资料」空表", "**最关键**：所有字段的 name/id/label"],
            ["step2_filled", "填完资料、传完图（提交前）", "核对填进去的值对不对"],
            ["after_step2", "点继续之后", "成功进入第3步 / 报错的分界"],
            ["calendar_N", "第3步 日历/时段", "可选日期、禁用日期的判定规则"],
            ["bureau_N / date_N / slot_N", "选办事处 / 选日期 / 选时段后", "选中状态长什么样"],
            ["confirm", "第4步 确认页", "递交前要勾什么、按钮文案"],
            ["submitted", "点递交后", "系统处理中的状态"],
            ["otp_page", "需要输入 OTP 时", "验证码输入框的位置与 name"],
            ["result", "最终页面", "成功/失败如何判定"],
        ],
        widths=[4.6, 5.4, 6.0],
        size=8.5,
    )

    body(doc, "每帧存三样东西：", size=10, bold=True)
    table(
        doc,
        ["文件", "内容", "怎么用"],
        [
            [".png", "整页截图（含滚动区域）", "肉眼对版式，看有没有弹窗/验证码"],
            [".html", "当时完整页面源码", "搜按钮文案、隐藏字段、form action"],
            [".json", "表单字段表 + 按钮表 + 文本摘要", "写选择器时直接抄 name/id"],
        ],
        widths=[2.4, 6.2, 7.4],
        size=9,
    )

    callout(
        doc,
        "隐私：证件号已自动打码",
        "录制时会把证件号、驾照号、邮箱、手机号、姓名替换成 12******28 这种形式（.html 和 .json 都处理）。"
        "但**截图是像素级的，无法打码**，所以 runs\\ 目录不要外传、不要发群。",
    )

    body(doc, "index.md 怎么读（实测样例）：", size=10, bold=True)
    code_block(
        doc,
        [
            "| # | 时间 | 步骤 | URL | 备注 | 字段 | 按钮 |",
            "| 01 | 22:26:51 +0.0s | 01_entry | abs3.td.gov.hk | 入口页已加载 | 1 | 预约, 更改/取消预约, 查询预约 |",
            "| 03 | 22:26:55 +4.6s | 03_auth | /SelectAuth | 第1步 认证页 | 2 | 返回, 继续 |",
            "| 07 | 22:27:22 +30.7s | 07_step2_blank | abs2...AdminServlet | 第2步空表 | 72 | 查询各牌照事务处预约情况, 返回主页, 继续 |",
        ],
        size=8.5,
    )
    body(doc, "「+30.7s」是相对第一步的耗时——抢号时看这一列就知道时间花在哪；"
              "「字段/按钮」两列能一眼看出页面结构有没有变（比如按钮从「继续」变成「下一步」）。", size=10)

    body(doc, "三个具体用法：", size=10, bold=True)
    for t in [
        "① 改选择器：打开 step2_blank.json，直接抄字段的 name/id，不用再猜。",
        "② 找瓶颈：看 index.md 的 +Xs 列，哪一步吃掉 20 分钟里的大部分时间，就优化那一步。",
        "③ 抓变化：官方改版后，对比新旧两份 index.md 的「按钮」列，立刻知道哪里变了。",
    ]:
        bullet(doc, t, size=10)

    body(doc, "关掉录制（默认开启）：config.json 里加 \"record\": false。", size=10)

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.12 节")
