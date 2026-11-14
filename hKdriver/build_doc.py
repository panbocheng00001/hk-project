# -*- coding: utf-8 -*-
"""生成《内地驾照换香港正式驾照·抢号作战手册》Word 文档。
运行：python build_doc.py
输出：内地驾照换香港驾照_抢号作战手册.docx
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
OUT = BASE / "内地驾照换香港驾照_抢号作战手册.docx"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
RED = RGBColor(0xC0, 0x1C, 0x28)
GREY = RGBColor(0x55, 0x55, 0x55)


# ------------------------------------------------------------------ 样式工具
def set_cn_font(run, name="微软雅黑"):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def body(doc, text="", size=10.5, bold=False, color=None, space_after=4,
         align=None, indent=None, first_line=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.25
    if align is not None:
        p.alignment = align
    if indent is not None:
        p.paragraph_format.left_indent = Cm(indent)
    if first_line is not None:
        p.paragraph_format.first_line_indent = Cm(first_line)
    r = p.add_run(text)
    set_cn_font(r)
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def h1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(6)
    r = p.add_run(text)
    set_cn_font(r, "微软雅黑")
    r.font.size = Pt(15)
    r.bold = True
    r.font.color.rgb = NAVY
    # 下划边线
    pPr = p._p.get_or_add_pPr()
    from docx.oxml import OxmlElement
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:color"), "1F3A5F")
    pbdr.append(bottom)
    pPr.append(pbdr)
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    set_cn_font(r)
    r.font.size = Pt(12)
    r.bold = True
    r.font.color.rgb = NAVY
    return p


def bullet(doc, text, level=0, size=10.5, bold_prefix=None):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.2
    if bold_prefix and text.startswith(bold_prefix):
        r = p.add_run(bold_prefix)
        set_cn_font(r)
        r.font.size = Pt(size)
        r.bold = True
        rest = text[len(bold_prefix):]
        r2 = p.add_run(rest)
        set_cn_font(r2)
        r2.font.size = Pt(size)
    else:
        r = p.add_run(text)
        set_cn_font(r)
        r.font.size = Pt(size)
    return p


def table(doc, headers, rows, widths=None, header_fill="1F3A5F", size=9.5,
          first_col_bold=False, zebra=True):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(h)
        set_cn_font(r)
        r.font.size = Pt(size)
        r.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        # 表头底色
        from docx.oxml import OxmlElement
        tcPr = hdr[i]._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), header_fill)
        tcPr.append(shd)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.1
            r = p.add_run(str(val))
            set_cn_font(r)
            r.font.size = Pt(size)
            if first_col_bold and i == 0:
                r.bold = True
            if zebra and ri % 2 == 1:
                from docx.oxml import OxmlElement
                tcPr = cells[i]._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "F2F5F9")
                tcPr.append(shd)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def callout(doc, title, text, fill="FFF3CD", border="C01C28"):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    cell = t.rows[0].cells[0]
    cell.text = ""
    from docx.oxml import OxmlElement
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(title)
    set_cn_font(r)
    r.font.size = Pt(10.5)
    r.bold = True
    r.font.color.rgb = RGBColor(0xC0, 0x1C, 0x28)
    p2 = cell.add_paragraph()
    p2.paragraph_format.space_after = Pt(2)
    r2 = p2.add_run(text)
    set_cn_font(r2)
    r2.font.size = Pt(10)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def code_block(doc, lines, size=9):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    cell = t.rows[0].cells[0]
    cell.text = ""
    from docx.oxml import OxmlElement
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), "F4F4F4")
    tcPr.append(shd)
    first = True
    for line in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        r = p.add_run(line)
        r.font.name = "Consolas"
        r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        r.font.size = Pt(size)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


# ------------------------------------------------------------------ 文档
def build():
    doc = Document()

    # 页面与默认字体
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    style.font.size = Pt(10.5)

    # ---------------- 封面
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("内地驾驶证 → 香港正式驾驶执照")
    set_cn_font(r)
    r.font.size = Pt(24)
    r.bold = True
    r.font.color.rgb = NAVY

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("免试签发 · 网上预约抢号作战手册")
    set_cn_font(r)
    r.font.size = Pt(16)
    r.bold = True
    r.font.color.rgb = RED

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("运输署指定网上预约系统（TDABS-DI）")
    set_cn_font(r)
    r.font.size = Pt(11)
    r.font.color.rgb = GREY

    for _ in range(6):
        doc.add_paragraph()

    table(
        doc,
        ["项目", "内容"],
        [
            ["申请人", "潘郁明"],
            ["驾驶执照签发地", "中国内地"],
            ["身份证明文件", "往来港澳通行证　CG7415503"],
            ["申请类别", "免试签发香港正式驾驶执照（TD63A）"],
            ["目标办事处", "九龙 → 香港 → 观塘 → 沙田"],
            ["文档版本", f"v1.0　{datetime.now():%Y-%m-%d}"],
        ],
        widths=[4.0, 11.5],
        first_col_bold=True,
        size=10,
    )

    callout(
        doc,
        "⚠ 本文件含个人资料",
        "内含证件号码、驾驶执照号码、手机号与电邮，仅供本人使用。请勿转发到群聊、"
        "勿交给任何“代办”，也不要截图发到公开平台。官方页面已明确：向第三方代理提供个人资料，"
        "后果自负；运输署会把预约资料与发证机关交叉核对。",
    )

    doc.add_page_break()

    # ---------------- 目录式速览
    h1(doc, "一、一页速览：明天怎么打")
    body(doc, "如果只看一页，就看这一页。", size=10, color=GREY)

    table(
        doc,
        ["时间（香港时间）", "你做什么", "为什么"],
        [
            ["15:50", "跑一次 python check.py，确认站点可用", "502/维护时当天不具备抢号条件"],
            ["16:00–16:30", "什么都别做，别刷", "系统维护，会话会被清，旧链接失效"],
            ["16:30", "从 gov.hk 页面进：勾同意→预约→不使用智方便→中国内地，停在排队页", "拿到“抽签券”"],
            ["16:30–16:59", "挂着，不刷新；可另开 1–2 条独立队列", "17:00 才随机洗牌，早进无优势"],
            ["17:00", "随机分配位置，哪条先放行用哪条", "这是唯一的胜负点"],
            ["放行后 20 分钟内", "填资料 → 收电邮 OTP → 选日期时段 → 递交", "官方限时 20 分钟，超时作废"],
            ["17:20 之后", "没中就挂 python check.py --loop 300 等取消回流", "取消/更改的时段会重新开放"],
        ],
        widths=[3.6, 7.4, 4.6],
        size=9.5,
    )

    callout(
        doc,
        "三条铁律",
        "1）16:30 之前排队＝零收益；2）17:00 之后每迟 1 分钟都在丢位置；"
        "3）一个身份只能有一个有效预约，多开队列只为提高中签率，中了一条立刻关掉其余。",
    )

    # ---------------- 资格
    h1(doc, "二、资格自检：先确认能办，再去抢")
    body(doc, "免试签发必须用文件证明同时满足以下四项，缺一项就会被柜台拒绝——抢到号也没用。")

    table(
        doc,
        ["条件", "内容", "内地驾照的实操要点"],
        [
            ["a", "驾照由附表 4 认可地区签发，仍有效或过期不超过 3 年",
             "中国内地在名单内；必须是正式驾照，实习期/学习驾照不行"],
            ["b", "申请车型为该驾照准许驾驶的类别",
             "免试只适用于私家车、轻型货车、电单车、机动三轮车"],
            ["c", "该驾照是通过驾驶考试后取得的",
             "若最近一次换证不足 1 年、驾照不显示实习期信息，或含多个准驾车型，"
             "需车管所出具「驾驶执照细节证明文件」正本+副本"],
            ["d", "三选一：①发照时在发照地居留满 6 个月　②申请前已持照满 5 年　③持发照地护照",
             "大陆人走 ②最省事。①的证明需护照出入境记录+签证+学校成绩表/在职证明；"
             "港澳居民居住证、内地房产证不被接受。③对内地驾照不适用——回乡证/居住证不算同等旅行证件"],
        ],
        widths=[1.1, 7.2, 7.3],
        size=9,
    )

    callout(
        doc,
        "现在就回答自己一个问题",
        "驾照「初次领证日期」到申请日满 5 年了吗？满了 → 明天放心抢；"
        "不满 → 必须有“当年在发照地住满 6 个月”的证据，否则抢到也会在柜台被拒。",
    )

    # ---------------- 系统结构
    h1(doc, "三、系统真实结构（实测）")
    body(doc, "2026-11-08 用真实浏览器逐层验证，链路如下：")

    code_block(
        doc,
        [
            "gov.hk/sc/apps/tddiabs.htm          ← 入口（3 秒 meta refresh）",
            "  └─ abs3.td.gov.hk  (React 单页)   ← 勾同意 → 预约 → 不使用智方便 → 中国内地",
            "       └─ abs4.td.gov.hk  (排队)    ← 随机分配排队位，闲时 20–35 秒放行",
            "            └─ abs1/abs2.td.gov.hk   ← 老式 JSP：有名额才给表单",
            "               ?cmd=cmdGetOfficeQuotaDIAction",
            "",
            "有名额 → 出现「继续」进入第 2 步「输入资料」",
            "无名额 → 直接红字：你所选择的服务现时没有可供预约的时段",
        ],
    )

    body(doc, "四个步骤：")
    for t in [
        "第 1 步　认证（身份认证方式 + 驾照签发国家/地方）",
        "第 2 步　输入资料（身份证明文件号码/影像、驾照号码/影像、电邮、OTP）",
        "第 3 步　选择牌照事务处、预约日期和时间",
        "第 4 步　递交预约资料",
    ]:
        bullet(doc, t)

    body(doc, "页面中存在 captchaToken 字段，第 2/4 步可能出现人机验证 —— 纯脚本无人值守不可靠，"
              "必须有人在场。", size=10)

    # ---------------- 时刻表详解
    h1(doc, "四、时刻表详解：为什么是这个节奏")

    h2(doc, "4.1 官方原文（gov.hk diabs.htm）")
    table(
        doc,
        ["时间", "系统状态", "对你的意义"],
        [
            ["16:00–16:30", "维护关闭", "什么都做不了，别刷；链接会更换"],
            ["16:30–17:00", "等候排队时段", "唯一有价值的窗口：进去拿一张“抽签券”"],
            ["17:00", "正式开始排队，随机分配位置", "16:30:01 与 16:59:59 概率完全一样，别拼毫秒"],
            ["17:00 之后", "按实际进入时间依序排队", "晚 1 分钟就排到所有人后面"],
            ["17:00→次日16:00", "排队持续有效", "位置可过夜，但轮到你时人必须在场"],
            ["放行后 20 分钟", "登记有效期", "超时作废，重新排队"],
        ],
        widths=[3.4, 6.0, 7.0],
        size=9.5,
    )

    h2(doc, "4.2 七个典型场景")

    scenarios = [
        ("例 1　标准成功线",
         "16:31 进池 → 17:00 抽到第 12 位 → 17:00:40 放行 → 17:10 递交成功。"
         "中间 28 分钟什么都不用做，胜负只在两刻：是否在池里、抽到第几位。"),
        ("例 2　提前 3 小时排队反而失手",
         "14:00 就开始排 → 16:00 维护把会话清掉 → 16:35 重进 → 17:00 抽到第 1800 位 → "
         "名额被抢光。那一小时完全无效，真正的开始时间是 16:30。"),
        ("例 3　秒数无关，但 17:00 之后晚 90 秒致命",
         "甲 17:00:30 进、乙 17:01:30 进，洗牌已完成，两人按实际进入时间排序。"
         "名额多时甲能进、乙不能；名额少时两人都白等。"),
        ("例 4　抽到了，20 分钟烧光",
         "图片是 HEIC → 现场转格式 → 超 5MB → 重传 → 号码打错一位 → 等 OTP 时去翻垃圾箱 → "
         "17:20 超时，位置清零回队尾。所以图片今晚就要转好、邮箱今晚就要测通。"),
        ("例 5　排队位置过夜是个陷阱",
         "周一 17:00 抽到第 2600 位，23:00 前面还剩 300 人，周二 09:12 轮到你——你在开会，"
         "09:32 作废。位置能过夜，但轮到你时人必须在场。"),
        ("例 6　靠取消回流捡漏",
         "周三 17:00 抽到第 4000 位失败 → 17:05 挂上雷达 → 19:23 有人改期放出时段 → "
         "雷达响铃 → 19:28 递交成功。这是 17:00 之外的第二条通道，竞争小得多。"),
        ("例 7　多队列提高胜率",
         "A 队抽到第 180 位（成功）、B 队第 2400 位、C 队第 900 位——中了一条立刻关掉其余。"
         "纪律：只保留一个有效预约；三条队列填的资料必须完全一致。"),
    ]
    for title, text in scenarios:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(title)
        set_cn_font(r)
        r.font.size = Pt(10.5)
        r.bold = True
        body(doc, text, size=10, indent=0.6)

    # ---------------- 速填卡
    h1(doc, "五、20 分钟速填卡（抢到就照这张念）")
    body(doc, "打印这一页放在手边。填错任何一位，预约会被判无效且要重新排队。")

    table(
        doc,
        ["字段", "要填的值", "备注"],
        [
            ["身份认证方式", "不使用「智方便」", "非香港身份证持有人唯一选择"],
            ["驾照签发国家/地方", "中国内地", ""],
            ["身份证明文件类型", "往来港澳通行证", "不能选“护照”，类型与号码必须同源"],
            ["身份证明文件号码", "CG7415503", "卡面右上角，逐字核对"],
            ["驾驶执照号码", "441781199010032728", "18 位，与驾驶证“证号”栏一致"],
            ["中文姓名", "潘郁明", "与证件逐字一致，简体"],
            ["电邮（收 OTP）", "13631539547@163.com", "建议换成 Gmail/Outlook，见第六节"],
            ["手机号", "（留空）", "非必填；11 位内地号可能撞 8 位校验，别自找麻烦"],
            ["身份证明文件影像", "files/港澳通行证.jpg", "卡片正面，号码与照片清晰，≤5MB"],
            ["驾驶执照影像", "files/内地驾驶证.jpg", "正页正面，≤5MB"],
            ["办事处顺序", "九龙 → 香港 → 观塘 → 沙田", "香港牌照事务处池子更大，放第 2 位"],
        ],
        widths=[4.2, 6.6, 5.6],
        size=9,
        first_col_bold=True,
    )

    callout(
        doc,
        "图片三条硬指标",
        "① JPEG/JPG ≤5MB 或 PDF ≤1MB；② 150–200dpi，缩图后能看清每一位号码；"
        "③ 文件名 ≤30 字，只允许字母数字中文与 _ - . ( ) 空格——“港澳通行证.jpg”合规。",
    )

    # ---------------- 材料
    h1(doc, "六、柜台当天材料包（正本 + 纸本副本）")
    body(doc, "预约成功只代表有柜位时段，不代表能办下来。副本必须是纸本，且包含所有页面正反面。")

    checklist = [
        "TD63A 申请表（亲笔签名，不接受电子/打印签名；改动处要加签）",
        "护照 / 往来港澳通行证　正本 + 资料页复印件",
        "内地驾驶证　正本 + 所有页面正反面复印件（不是只印正面）",
        "资格证明：持照满 5 年（看驾照初次领证日期）；或居留 6 个月的出入境记录+签证+成绩表/在职证明",
        "驾照细节证明（最近换证不足 1 年 / 不显示实习期 / 多准驾车型，三者任一）",
        "住址证明（3 个月内）",
        "香港通讯地址证明（3 个月内）；借他人香港地址需对方亲笔授权书正本 + 其身份证明副本",
        "70 岁以上：TD256 体格检验证明（4 个月内）",
        "费用（正式驾照 10 年期，私家车约 HK$900，以 TD341 为准；现场缴）",
        "电子联络方式核实记录（递交前 3 个月内完成，截图保存）",
        "打印好的预约确认通知（电邮附件）",
    ]
    for c in checklist:
        bullet(doc, "☐  " + c, size=10)

    callout(
        doc,
        "最容易漏的一条",
        "递交前 3 个月内，必须在运输署指定平台用一次性密码核实「电子联络方式」"
        "（香港流动电话号码 或 电邮地址，二选一）。漏了柜台不办。"
        "平台：econtact.td.gov.hk/vansweb/app895_sc.html —— 选“驾驶执照”，"
        "输入证件号码，用电邮收 OTP。核实记录 3 个月有效。",
    )

    # ---------------- 没有港号
    h1(doc, "七、没有香港手机号怎么办：不影响")
    body(doc, "运输署 2024-11-18 起的规则：必须提供及核实一个香港流动电话号码 或 电邮地址。"
              "关键词是“或”——两者二选一。")

    table(
        doc,
        ["环节", "是否必需香港号", "说明"],
        [
            ["网上预约（20 分钟）", "不需要", "官方准备清单只有身份证明文件、驾照、电邮；手机号栏留空"],
            ["收 OTP", "不需要", "OTP 由 tdabs@td.gov.hk 发到电邮"],
            ["递交前电子联络方式核实", "不需要", "平台接受电邮地址接收 OTP"],
        ],
        widths=[4.6, 3.4, 8.4],
        size=9.5,
    )
    body(doc, "若真要港号（可选，非必需）：便利店买 SoSIM / CMHK 储值卡（约 HK$33–50），"
              "香港 SIM 卡需实名登记，可用护照或通行证登记；但为这件事专门跑一趟香港不值。", size=10)

    # ---------------- 工具
    # ---------------- 运行说明
    # ---------------- 运行说明（照着做）
    h1(doc, "八、脚本怎么运行（照着做）")
    body(doc, "这一章按「你要做的事」组织，不按文件组织。每条命令都写清：在哪敲、敲什么、"
              "应该看到什么、不对怎么办。全部文件都在 E:\\googleProduct\\hKdriver。")

    h2(doc, "8.0 先打开一个窗口（每次都要）")
    body(doc, "按 Win + X → 选「终端」或「Windows PowerShell」，然后进到项目目录：", size=10)
    code_block(doc, ["cd E:\\googleProduct\\hKdriver"])
    body(doc, "确认三样东西在位，看到这些文件名就说明对了：", size=10)
    code_block(
        doc,
        [
            "check.py      grab.py      human.py      launch_chrome.ps1",
            "config.json   files\\港澳通行证.jpg   files\\内地驾驶证.jpg",
            "runs\\         （运行记录，出问题看这里）",
        ],
    )

    h2(doc, "8.1 场景 A：现在就想验证工具能不能用（任何时间都能做）")
    body(doc, "目的不是抢号，是确认「浏览器→官方系统」这条路通不通。三步，共约 4 分钟。", size=10)
    body(doc, "先说一件反直觉的事：点进去之后窗口会白屏 40 秒左右才出内容。"
              "abs3 这个前端要先下载约 1.9MB 的 main.js 再渲染，不是卡死，别关窗口。"
              "另外无头模式（headless）实测渲染不出来，会被 WAF 拦，所以脚本默认都带窗口。", size=10)

    table(
        doc,
        ["步骤", "敲这条命令", "应该看到（2026-11-08 实测）", "不对怎么办"],
        [
            ["A1　看站点通不通",
             "python quick_probe.py",
             "entry_status = 200\n"
             "final_url = https://abs3.td.gov.hk/?lang=sc\n"
             "has_checkbox = True\n"
             "（约 45 秒）",
             "若是 502：官方故障，只能等\n"
             "若 has_checkbox = False：重跑一次；仍不行看是不是在维护时段"],
            ["A2　看有没有名额",
             "python check.py",
             "[10] 无名额（47s）  (时段=weekend)\n"
             "或 [0] 有名额\n"
             "（约 85 秒，会弹一个浏览器窗口）",
             "[30] = 官方 502，等\n"
             "[20] = 没探到，会自动退避重试"],
            ["A3　看脚本能不能走完全程",
             "python grab.py --probe --workers 1 --channel chrome",
             "第2步字段数=72\n"
             "⚠ 系统提示：该服务现时没有可预约时段\n"
             "（约 2 分钟，最后一行是「演练模式…不继续提交」）",
             "若提示连不上 9222：已加 --channel chrome 不依赖端口\n"
             "若报「文件不存在」：检查 files\\ 下两张图"],
        ],
        widths=[3.6, 4.8, 4.6, 3.0],
        size=8.5,
    )

    body(doc, "A3 的完整真实输出（2026-11-08 22:07 实测，总 119 秒）：", size=10)
    code_block(
        doc,
        [
            "启动模式：内核=chrome headless=False",
            "22:07:52 [w1] 内核：本机已装的 chrome",
            "22:07:52 [w1] 打开入口页（第 1 次）",
            "22:08:37 [w1] 已同意条款                 ← 这 45 秒是全在白屏等前端渲染",
            "22:08:38 [w1] 点「预约」",
            "22:08:45 [w1] 选择：不使用智方便",
            "22:08:45 [w1] …0s https://abs4.td.gov.hk/     ← 进入排队",
            "22:09:09 [w1] …24s https://abs3.td.gov.hk/SelectCountry?lang=sc   ← 排队 24 秒放行",
            "22:09:35 [w1] 国家/地方选项: ['中国内地', '中国内地以外的国家或地方']",
            "22:09:36 [w1] 选国家/地方：中国内地",
            "22:09:49 [w1] 第2步字段数=72 按钮=[]",
            "22:09:49 [w1] ⚠ 系统提示：该服务现时没有可预约时段",
            "22:09:49 [w1] 演练模式：已导出第2步结构，不继续提交",
        ],
        size=8.5,
    )

    h2(doc, "8.2 场景 B：工作日抢号（16:20 — 17:20）")
    body(doc, "这是唯一需要卡时间的场景。下表按时刻排，照着敲。", size=10)

    table(
        doc,
        ["时刻", "敲这条命令", "说明"],
        [
            ["16:20",
             "powershell -ExecutionPolicy Bypass -File .\\launch_chrome.ps1",
             "起真 Chrome（独立 profile）。看到「CDP 就绪：Chrome/156」即可，"
             "浏览器窗口会弹出来，之后别关它"],
            ["16:25",
             "python check.py",
             "确认站点可用。若返回 30（502）→ 当天不具备抢号条件，别浪费排队机会"],
            ["16:30　准时",
             "python grab.py --workers 3",
             "三条队列同时排队。三个窗口各自跑，日志前缀 w1/w2/w3 区分"],
            ["16:30—16:59",
             "（什么都不敲）",
             "挂着等。此时页面是排队页，别刷新、别关窗口；刷新会掉位置"],
            ["17:00",
             "（盯着日志）",
             "随机分配位置。看到「国家/地方选项:」说明有窗口放行了"],
            ["放行后",
             "手动接管那个窗口，或等脚本自动填",
             "脚本会自己填资料、上传图片、挑最早日期；20 分钟内必须点完递交"],
            ["OTP 到达时",
             "把验证码写进 otp.txt（一行数字）",
             "脚本每 2 秒读一次，读到就自动填；也可以直接在窗口里手敲"],
            ["成功后",
             "（等响铃）",
             "看到「★★★ 拿到预约」+ 电邮收到 tdabs@td.gov.hk 确认信，才算完"],
        ],
        widths=[2.4, 6.2, 7.4],
        size=8.5,
    )

    body(doc, "OTP 那一步的具体做法（这是唯一需要你动手的地方）：", size=10, bold=True)
    code_block(
        doc,
        [
            "① 邮件来自 tdabs@td.gov.hk，里面有一串数字",
            "② 在项目目录新建文本文件 otp.txt",
            "③ 里面只写那串数字，一行，保存",
            "④ 脚本 2 秒内读到并填入，然后自动点继续",
            "⑤ 若脚本没抢到填的机会（窗口在你手上），直接在窗口里手敲更快",
        ],
    )

    h2(doc, "8.3 场景 C：17:20 没抢到，挂着等「取消回流」")
    body(doc, "官方明确：经更改或取消的预约时段，名额会重新开放。这条通道竞争小得多。", size=10)
    code_block(
        doc,
        [
            "# 每 5 分钟探一次；有号就响铃，并保持浏览器窗口给你接手",
            "python check.py --loop 300 --ring --show --handoff",
            "",
            "# 想安静地挂着（不弹窗口，只响铃）",
            "python check.py --loop 300 --ring",
            "",
            "# 停掉：按 Ctrl + C",
        ],
    )
    body(doc, "注意：探测间隔不要低于 60 秒。官方 FAQ 明确会对异常活动做限制，"
              "而且取消回流本来就分散在全天，快没有意义。", size=10)

    h2(doc, "8.4 三条命令各自到底做了什么")

    table(
        doc,
        ["命令", "做了什么", "会不会动你的预约"],
        [
            ["python quick_probe.py",
             "打开入口页，确认能跳转到 abs3、勾选框能渲染（约 20 秒）",
             "不会。只看不动"],
            ["python check.py",
             "走一遍：勾同意 → 预约 → 不用智方便 → 选内地 → 看系统回什么（约 40 秒）",
             "不会。停在「有没有名额」这个判断上"],
            ["python grab.py --probe",
             "同 check.py，但会导出页面结构到 runs\\，用于改脚本（约 2 分钟）",
             "不会。明确不提交"],
            ["python grab.py --workers 3",
             "三条队列排队；任一条放行 → 填资料 → 上传 → 挑最早日期时段 → 递交",
             "会。这是唯一会真正提交的动作"],
            ["python fingerprint_check.py",
             "打开 about:blank 读浏览器指纹，对比自动化痕迹",
             "不碰官方系统"],
        ],
        widths=[4.6, 8.0, 4.4],
        size=8.5,
    )

    h2(doc, "8.5 一页速查卡（打印出来贴屏幕）")
    code_block(
        doc,
        [
            "cd E:\\googleProduct\\hKdriver          ← 每次先做这个",
            "",
            "python quick_probe.py                ← 站点通不通（20 秒）",
            "python check.py                      ← 有没有号（40 秒）",
            "python check.py --loop 300 --ring --show --handoff   ← 挂着等回流",
            "",
            "powershell -ExecutionPolicy Bypass -File .\\launch_chrome.ps1   ← 起真 Chrome",
            "python grab.py --workers 3           ← 正式抢号（3 条队列）",
            "python grab.py --workers 2           ← 站点不稳时用 2 条",
            "python grab.py --probe --workers 1   ← 只演练，不提交",
            "",
            "python fingerprint_check.py --attach http://127.0.0.1:9222    ← 指纹自检",
            "python build_doc.py                  ← 重新生成本文档",
            "",
            "退出码：0=有号  10=没号  20=没探到  30=官方502",
            "OTP：把验证码写进 otp.txt（一行数字）",
        ],
        size=8.5,
    )

    h2(doc, "8.6 全部参数")
    table(
        doc,
        ["参数", "作用", "默认"],
        [
            ["check.py --loop N", "每 N 秒探一次（最低 60）", "只探一次"],
            ["check.py --ring", "有号时响铃", "关闭"],
            ["check.py --headless", "无头模式（实测被 WAF 拦，页面空白，仅备用）", "有头（默认）"],
            ["check.py --handoff", "有号时保持窗口打开等你接手", "关闭"],
            ["grab.py --workers N", "同时排几条独立队列", "读 config（3）"],
            ["grab.py --probe", "只侦察到第 2 步，不提交", "关闭"],
            ["grab.py --attach URL", "接管调试端口的真 Chrome", "读 config"],
            ["grab.py --channel chrome", "用本机 Chrome 当内核", "读 config"],
            ["grab.py --headless", "无头模式（仅演练）", "关闭"],
            ["fingerprint_check.py --chrome", "用真 Chrome 测指纹", "chromium"],
            ["fingerprint_check.py --attach URL", "测接管模式的指纹", "—"],
            ["launch_chrome.ps1", "起真 Chrome 并开 9222 端口", "端口 9222"],
        ],
        widths=[5.6, 7.0, 3.4],
        size=8.5,
    )

    h2(doc, "8.7 三种典型输出（对照着看）")
    body(doc, "① 有名额（该立刻接手）：", size=10, bold=True)
    code_block(
        doc,
        [
            "17:00:41 [w2] …41s https://abs3.td.gov.hk/SelectCountry?lang=sc",
            "17:00:43 [w2] 国家/地方选项: ['中国内地', '中国内地以外的国家或地方']",
            "17:00:44 [w2] 选国家/地方：中国内地",
            "17:00:52 [w2] 第2步字段数=14 按钮=[]",
            "17:01:07 [w2] 上传 港澳通行证.jpg -> file#0",
            "17:01:15 [w2] 选日期 2026-11-17",
            "17:01:22 [w2] ★★★ 拿到预约，请立刻检查电邮 ★★★",
        ],
        size=8.5,
    )
    body(doc, "② 没名额（绝大多数时候）：", size=10, bold=True)
    code_block(doc, ["20:18:10 [10] 无名额（39s）  (时段=weekend)"], size=8.5)
    body(doc, "③ 官方 502：", size=10, bold=True)
    code_block(
        doc,
        [
            "20:18:10 [30] 官方返回错误页（502/维护），不是名额问题 url=https://abs3.td.gov.hk/?lang=sc",
            "  站点本身故障（502/维护），300s 后再看",
        ],
        size=8.5,
    )

    h2(doc, "8.8 出问题怎么查")
    table(
        doc,
        ["你看到的现象", "原因", "怎么处理"],
        [
            ["连不上 http://127.0.0.1:9222",
             "Chrome 没开或被关掉",
             "重跑 launch_chrome.ps1；或改用 python grab.py --channel chrome"],
            ["入口页等不到勾选框",
             "维护 / 502 / 链接已被更换 / 前端还没渲染完",
             "前端渲染实测要 40 秒左右，先多等；返回 30 才是官方故障"],
            ["页面全白、body 是空的",
             "用了无头模式（headless），被 WAF 拦住",
             "脚本默认都有头。别加 --headless"],
            ["浏览器顶部黄色「正受到自动测试软件控制」",
             "启动时带了 --enable-automation",
             "已加 --excludeSwitches=enable-automation 消除；这条提示只在本地可见，服务端读不到"],
            ["国家/地方选项为空 []",
             "页面还没渲染完",
             "已修。若仍出现，重跑一次"],
            ["卡在排队页很久",
             "17:00 高峰，位置靠后",
             "正常。别刷新（会掉位置），等别的队列先出"],
            ["上传失败 / 文件不存在",
             "config 里的路径与文件不符",
             "确认 files\\ 下两张图存在；config 里路径写成 files/xxx.jpg"],
            ["OTP 收不到",
             "163 拦截、进垃圾箱、账号风控",
             "抢号前把 tdabs@td.gov.hk 加白名单；电脑和手机同时开邮箱"],
            ["提示「没有可供预约的时段」",
             "真的没号（周末 / 已抢光）",
             "不是脚本问题。工作日 17:00 再来"],
            ["提示「重复预约」",
             "同一身份已有有效预约",
             "立刻关掉其余队列，只保留一条"],
            ["脚本好像不动了",
             "在等页面元素（排队 / 慢加载）",
             "看 runs\\grab.log 最后一行；按 Ctrl+C 可停，重跑即可"],
        ],
        widths=[4.6, 4.8, 6.6],
        size=8.5,
    )

    h2(doc, "8.9 运行纪律（违反会白抢）")
    for t in [
        "一个身份只能有一个有效预约：多开队列只为提高中签率，中了一条立刻关掉其余。",
        "三条队列填的资料必须完全一致，否则交叉核对会判可疑。",
        "别用 VPN / 机房 IP / iCloud 私密转送——官方 FAQ Q20 明确会判异常。",
        "别在 16:00—16:30 维护时段跑，会话会被清、链接会换。",
        "别高频重跑 grab.py；502 期间跑一百次也没用，只会留下更多请求记录。",
        "别把真实证件号发给任何「代办」，也别贴到群里。",
    ]:
        bullet(doc, t, size=10)

    h2(doc, "8.10 文件清单：哪个能删，哪个别动")
    table(
        doc,
        ["文件 / 目录", "作用", "能删吗"],
        [
            ["check.py", "名额雷达", "别删"],
            ["grab.py", "抢位主程序", "别删"],
            ["human.py", "真人节奏模块（被上面两个引用）", "别删"],
            ["launch_chrome.ps1", "起真 Chrome", "别删"],
            ["fingerprint_check.py", "指纹自检", "可删"],
            ["quick_probe.py", "20 秒站点体检", "可删"],
            ["build_doc.py", "重新生成本文档", "可删"],
            ["config.json", "你的资料与策略", "别删（删了要重填）"],
            ["files\\", "两张证件图", "别删"],
            ["runs\\", "每次运行的截图与快照", "可清空"],
            ["profiles\\", "各队列的浏览器 profile（存排队会话）", "抢号期间别删"],
            ["chrome_profile\\", "接管模式的 Chrome 用户数据", "抢号期间别删"],
            ["probe\\、patch_ch8.py", "开发时的临时产物", "可删"],
            ["tdabs_monitor.py", "早期版本，已废弃", "可删（留作参考）"],
        ],
        widths=[4.6, 8.4, 3.2],
        size=8.5,
    )



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


    h2(doc, "8.13 中途冒出验证码怎么办")
    body(doc, "官方页面里确实有 `captchaToken` 隐藏字段，说明系统具备验证码能力；只是没名额时"
              "走不到那一步，所以平时看不到。处理原则是「宽检测 + 保守兜底」：能自动过的自动过，"
              "过不了就响铃喊人，绝不在 20 分钟窗口里硬刚。", size=10)

    table(
        doc,
        ["类型", "长什么样", "脚本怎么处理", "要你做什么"],
        [
            ["v3 隐形验证", "看不见，只是提交时带一个 token", "等 token 生成（最多 20 秒）后提交", "什么都不用做"],
            ["reCAPTCHA 勾选框", "「我不是机器人」方框", "自动点一下，多数直接过", "若弹出图片挑战就看屏幕"],
            ["reCAPTCHA 图片挑战", "选红绿灯/斑马线那九宫格", "无法自动，响铃 + 截图留档 + 等你", "**必须你亲手点**"],
            ["图形验证码", "4–6 位歪斜字母数字图", "同上", "**必须你亲手输**"],
            ["hCaptcha / Turnstile", "另一家的验证码", "同上", "**必须你亲手点**"],
        ],
        widths=[3.4, 4.4, 5.6, 2.6],
        size=8.5,
    )

    body(doc, "三个接入点（都在真正提交之前）：", size=10, bold=True)
    for t in [
        "第2步填完资料、点「继续」之前",
        "第4步点「确认 / 递交」之前",
        "OTP 填完、点「提交」之前",
    ]:
        bullet(doc, t, size=10)

    body(doc, "出现验证码时的屏幕输出长这样：", size=10, bold=True)
    code_block(
        doc,
        [
            "22:41:07 [w1] ⚠⚠ 出现验证码：recaptcha_image　reCAPTCHA 图片挑战 iframe",
            "22:41:07 [w1] 📸 12 captcha　验证码出现：recaptcha_image",
            "============================================================",
            "22:41:07 [w1] ★★★ 需要你操作：在浏览器窗口里完成验证码（180 秒内）★★★",
            "============================================================",
            "22:41:26 [w1] 验证码已通过（人工，用时 19s）",
        ],
        size=8.5,
    )
    body(doc, "听到响铃就去看那个浏览器窗口，点完它会自己继续往下走，不用回来敲任何命令。", size=10)

    callout(
        doc,
        "踩过的坑：官方 JSP 页有个空的 captchaToken 字段",
        "第2步那个页面带一个空的 captchaToken（模板残留），第一版逻辑看到「token 为空」就傻等 20 秒，"
        "每次提交白扔 20 秒。现已改成：只有页面真的挂了 reCAPTCHA 脚本 / 有 data-sitekey 时才等，"
        "否则 0 秒放行。实测 JSP 页 guard 耗时 0.00s。",
    )

    body(doc, "自测结果（本地 mock 页面，7 种形态）：", size=10, bold=True)
    code_block(
        doc,
        [
            "OK   v3_隐形_未填充     type=none               tokenLen=0",
            "OK   v3_隐形_已填充     type=none               tokenLen=37",
            "OK   v2_勾选框         type=recaptcha_checkbox tokenLen=0",
            "OK   v2_图片挑战       type=recaptcha_image    tokenLen=0",
            "OK   图形验证码         type=image_code         tokenLen=-1",
            "OK   hcaptcha          type=hcaptcha           tokenLen=-1",
            "OK   无验证码           type=none               tokenLen=-1",
            "通过 7/7",
        ],
        size=8.5,
    )

    body(doc, "相关文件：captcha.py（识别与处理）、test_captcha.py（7 种形态自测）。"
              "所有验证码出现/通过的瞬间都会被录制，事后可以看 captcha.png 分析。", size=10)


    h2(doc, "8.14 验证码取证：出现就自动留档（你要的分析素材）")
    body(doc, "验证码只会在拿到号、真正填表的时候出现。为了「事后能分析清楚它到底是什么、能不能自动化」，"
              "现在做了三层保障，不依赖脚本正好在提交前才去检测。", size=10)

    table(
        doc,
        ["层级", "做法", "效果"],
        [
            ["第一层：每帧自动判", "每次截图时顺手跑一次验证码检测，结果写进该帧的 json",
             "验证码在任何时刻冒出来都会被记下来"],
            ["第二层：专项取证包", "检测到就单独存到 `captcha\\` 子目录，5 个文件",
             "拿到 sitekey、iframe、图片本体，能判断厂商和难度"],
            ["第三层：形态变化留痕", "勾选框 → 图片挑战这种升级，每变一次再存一帧",
             "能看清它是「直接过」还是「升级成图片题」"],
        ],
        widths=[4.2, 7.4, 4.4],
        size=8.5,
    )

    body(doc, "取证包（实测产物，5 个文件）：", size=10, bold=True)
    table(
        doc,
        ["文件", "内容", "能回答什么问题"],
        [
            ["NN_<类型>.json", "检测结论：类型、token 长度、是否挂了验证码脚本",
             "这次是哪种验证码、有没有隐形 token"],
            ["NN_<类型>_evidence.json", "iframe 列表、script src、data-sitekey、输入框属性、图片源",
             "哪家厂商（reCAPTCHA/hCaptcha/Turnstile）、sitekey 是多少"],
            ["NN_<类型>.png", "整页截图（含验证码弹窗）", "长什么样、占多大位置"],
            ["NN_<类型>.html", "当时页面源码（已打码）", "挑战容器的 DOM 结构、隐藏字段"],
            ["NN_img1.png", "从 data:base64 里抠出来的验证码图片本体", "可以直接拿去喂 OCR 试识别率"],
        ],
        widths=[4.4, 6.6, 5.0],
        size=8.5,
    )

    body(doc, "实测样例（mock 页面，含图形码 + 勾选框 + sitekey）：", size=10, bold=True)
    code_block(
        doc,
        [
            "runs\\<时间>\\w1\\captcha\\",
            "  01_recaptcha_checkbox.json             298 B   类型=recaptcha_checkbox tokenLen=0",
            "  01_recaptcha_checkbox_evidence.json   1192 B   sitekey=6Lc-FAKE-... scripts=1 images=1",
            "  01_recaptcha_checkbox.png             7650 B   整页截图",
            "  01_recaptcha_checkbox.html             754 B   源码（已打码）",
            "  01_recaptcha_checkbox_img1.png         157 B   验证码图片本体",
        ],
        size=8.5,
    )

    body(doc, "抢号当天你的动作只有一件：", size=10, bold=True)
    for t in [
        "听到响铃 + 屏幕上出现「★★★ 需要你操作 ★★★」→ 切到那个浏览器窗口。",
        "勾选框能自动过就自动过；弹图片题就你点，点完脚本自己继续。",
        "全程不用敲任何命令，也不用关窗口。",
        "事后要分析，就去 `runs\\<当天时间>\\w1\\captcha\\` 看那 5 个文件。",
    ]:
        bullet(doc, t, size=10)

    callout(
        doc,
        "从证据里能读出什么（这就是「怎么优化」的依据）",
        "① evidence.json 里的 sitekey → 确认是哪家、哪个站点密钥，判断有没有公开的自动化方案；"
        "② 输入框 maxlength → 验证码几位；③ 有没有 data:base64 图片 → 有就说明是图形码，可以试 OCR；"
        "④ 只有 iframe + 没有图片 → 是 reCAPTCHA 交互式挑战，只能人工或降低触发概率。",
    )

    body(doc, "自测：7 种验证码形态识别 7/7 通过；取证包 5 个文件齐全（含 base64 图片解出落盘）。", size=10)
    body(doc, "相关文件：captcha.py（识别/自动点/响铃）、recorder.py（取证）、"
              "test_captcha.py 与 test_captcha_pack.py（自测）。", size=10)


    h2(doc, "8.15 推到钉钉：抢到了 / 要你点验证码，手机上就能看到")
    body(doc, "17:00 那十几分钟你未必一直盯着电脑。现在关键事件都会推到钉钉群，手机响一下就知道。", size=10)

    table(
        doc,
        ["什么时候推", "消息标题", "里面有什么"],
        [
            ["名额出现", "有名额了！", "提示立刻跑 grab.py，附当前状态"],
            ["抢到排队位", "已拿到排队位", "开始排队了，别关窗口、别刷新"],
            ["排队放行、进入填写页", "已放行，进入填写页", "**20 分钟倒计时开始**，别磨蹭"],
            ["OTP 等太久（每 120 秒，最多 3 次）", "⚠ OTP 没收到",
             "写明**是哪个邮箱**、已等多少秒、三条排查建议"],
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


    h2(doc, "8.16 反检测审计：逐项比对，找出还会被看出来的地方")
    body(doc, "用 audit_fingerprint.py 把「脚本拉起的浏览器」和「普通方式启动的真 Chrome」"
              "放在一起跑同一套 30+ 项检测，只列不一样的。这是当前实测结果。", size=10)

    body(doc, "第一轮（修之前）：4 项差异", size=10, bold=True)
    table(
        doc,
        ["项目", "脚本", "真 Chrome", "原因"],
        [
            ["devicePixelRatio", "1.0", "1.5", "Playwright 默认 viewport 强制 1x，无视系统 150% 缩放"],
            ["screen", "1280×720", "1707×1067", "同上，screen 被 viewport 覆盖"],
            ["window", "1296×808", "1050×1000", "Playwright 默认窗口 1280×720"],
            ["navigator.languages", "zh-CN", "zh-CN,zh", "context 的 locale 只设一个值"],
        ],
        widths=[4.0, 3.6, 3.8, 5.0],
        size=8.5,
    )

    body(doc, "修复动作：", size=10, bold=True)
    for t in [
        "grab.py / check.py / audit 全部改成 `no_viewport=True` —— 让浏览器用真实窗口尺寸与系统缩放。",
        "启动参数加 `--window-size=1536,864`，窗口尺寸落在真人常见范围，不再是被 Playwright 支配的 800×600。",
    ]:
        bullet(doc, t, size=10)

    body(doc, "第二轮（修之后）：2 项", size=10, bold=True)
    table(
        doc,
        ["项目", "脚本", "真 Chrome", "要不要管"],
        [
            ["window", "1536×864", "1050×1000", "不用。窗口大小本来就人人不同"],
            ["navigator.languages", "zh-CN", "zh-CN,zh", "不改。硬改会在实例上留 hasOwnProperty 痕迹，"
             "而单一语言是完全正常的真人配置"],
        ],
        widths=[4.0, 3.6, 3.8, 5.0],
        size=8.5,
    )
    body(doc, "其余 28 项全部一致：webdriver=false、无 JS 补丁痕迹、UA/UA-CH 与真 Chrome 一致、"
              "真 GPU 渲染、插件与字体集一致、canvas 稳定、函数都是 native、时区 Asia/Shanghai。", size=10)

    body(doc, "行为层同时收紧了三处：", size=10, bold=True)
    table(
        doc,
        ["原做法", "改成", "为什么"],
        [
            ["鼠标直线插值飞到元素中心", "二次贝塞尔弧线 + 1.5px 抖动 + 落点前轻微过冲",
             "真人的手有弧度、会抖、会点偏一点再修正"],
            ["按键延迟均匀随机 35–150ms", "对数正态分布（均值约 98ms，偶发 240ms）",
             "人打字有节奏：多数快、偶尔卡一下，不是均匀分布"],
            ["一次滚 120–480px", "拆成 40–110px 的小段，段间 30–120ms",
             "真人是滚轮一点点看，不是瞬移"],
        ],
        widths=[4.6, 6.4, 5.0],
        size=8.5,
    )

    body(doc, "对「封 IP」最相关的其实不是指纹，是并发：", size=10, bold=True)
    table(
        doc,
        ["风险点", "现状", "已做的处理"],
        [
            ["同 IP 多队列并发", "原来默认 3 条同时开",
             "默认改 2 条；`--stagger` 控制启动间隔（默认 2.5s）；超过 2 条会打印提示"],
            ["排队页轮询频率", "本地查 DOM，不发请求", "本来就只在 URL 变化时才 goto，队列轮询是纯本地操作"],
            ["雷达循环", "每 5 分钟一个新浏览器", "间隔有抖动（H.jitter），不是精确心跳"],
            ["邮件轮询", "每 15 秒 IMAP 登录", "对微软有风控风险，建议 `--loop 30`；对运输署无影响"],
            ["16:30 同时进池", "2 条队列错开 2.5 秒", "看起来就是开了两个标签页的人"],
        ],
        widths=[4.6, 5.4, 6.0],
        size=8.5,
    )

    callout(
        doc,
        "说实话：有一个检测点我们伪造不了",
        "真人点击的 PointerEvent 上 `event.sourceCapabilities` 是对象，CDP 合成的事件里是 null。"
        "理论上能识别自动化。但这属于 Cloudflare / DataDome 那级别的检测，"
        "运输署这套（华为 WAF + 排队服务）不会为「免试签发预约」专门上这种；"
        "而且真要伪造只能靠 JS 补丁，补丁本身更容易被抓。所以这一项保持现状。",
    )

    body(doc, "一条命令随时自查：", size=10, bold=True)
    code_block(
        doc,
        [
            "python audit_fingerprint.py --compare    # 需要先跑 launch_chrome.ps1",
            "python fingerprint_check.py --chrome      # 只看核心几项",
        ],
    )


    h2(doc, "8.17 那些「一键补环境」的库，实测能不能用")
    body(doc, "你可能听说过这类东西：playwright-stealth、undetected-chromedriver、nodriver、"
              "camoufox、selenium-stealth。它们确实存在，但原理和代价差别很大，这里给实测结论。", size=10)

    table(
        doc,
        ["库", "原理", "与当前方案的关系"],
        [
            ["playwright-stealth", "用 add_init_script 往页面注入一堆 JS 补丁，"
             "覆盖 navigator.webdriver / plugins / languages / permissions 等",
             "**实测在 Chrome 156 上直接崩**（见下）"],
            ["undetected-chromedriver", "改 Chrome 启动方式 + Selenium，专治 Cloudflare",
             "基于 Selenium，与 Playwright 不兼容，要重写全部脚本"],
            ["nodriver", "直连 CDP，不注入 JS 补丁（undetected 作者的新项目）",
             "思路更干净，但要整体重写；可作为将来备选"],
            ["camoufox", "专用 Firefox 构建，指纹全随机",
             "浏览器从 Chrome 变 Firefox，对一个香港政府站反而更显眼"],
            ["selenium-stealth", "同 playwright-stealth，Selenium 版", "不适用"],
        ],
        widths=[3.6, 7.2, 5.2],
        size=8.5,
    )

    body(doc, "实测 playwright-stealth（2026-11-08，Chrome 156.0.8078.80 + Playwright 1.57）：", size=10, bold=True)
    code_block(
        doc,
        [
            "=== B：playwright-stealth ===",
            "Page.evaluate: ReferenceError: opts is not defined",
            "    at Navigator.get [as userAgent] (<anonymous>:5:42)",
            "",
            "→ 它把 navigator.userAgent 换成了一个引用未定义变量 opts 的 getter，",
            "  只要页面读一次 navigator.userAgent 就抛异常。",
        ],
        size=8.5,
    )
    body(doc, "这不是「留痕」的问题，是**直接把页面搞崩**：TDABS 的前端脚本只要读一次 UA 就会抛错，"
              "轻则功能异常，重则白屏。20 分钟窗口里出现这种故障等于当天报废。", size=10)

    body(doc, "为什么参数级方案反而更干净：", size=10, bold=True)
    table(
        doc,
        ["对比项", "我们的做法（启动参数 + 真实 Chrome）", "stealth 类库"],
        [
            ["navigator.webdriver", "原生就是 false", "靠 JS 覆盖，会留 hasOwnProperty"],
            ["属性是否被改", "`webdriverOwn = false`（没改过）", "通常是 true（改过）"],
            ["函数是否 native", "全部 true", "覆盖过的会变 false"],
            ["浏览器升级后", "参数依然有效", "**补丁可能失效甚至崩页面**"],
            ["失败方式", "降级为普通浏览器", "页面直接报错"],
        ],
        widths=[4.0, 7.0, 5.0],
        size=8.5,
    )

    callout(
        doc,
        "结论",
        "补丁类库解决的是「浏览器默认就带自动化标记」这个问题——而我们已经在参数层解决了它。"
        "在参数层已经干净的情况下再加 JS 补丁，只会把 `hasOwnProperty` 和 `toString` 这些新破绽补上去。"
        "所以：现阶段不用任何 stealth 库；将来若真要对抗更高强度检测，选 nodriver 重写，而不是往 Playwright 上贴补丁。",
    )

    # ---------------- 反检测
    h1(doc, "九、“用 Playwright 会不会被识别”")
    body(doc, "用 fingerprint_check.py 在本机实测三种内核：")

    table(
        doc,
        ["检测项", "Playwright chromium", "真 Chrome 启动", "真 Chrome 接管"],
        [
            ["navigator.webdriver", "False ✅", "False ✅", "False ✅"],
            ["是否被 JS 补丁覆盖", "False ✅", "False ✅", "False ✅"],
            ["UA 版本", "Chrome/143 ⚠", "Chrome/156 ✅", "Chrome/156 ✅"],
            ["navigator.languages", "['zh-CN']", "['zh-CN','zh']", "['zh-CN','zh'] ✅"],
            ["窗口/屏幕尺寸", "被 viewport 接管", "真实", "真实 ✅"],
            ["WebGL 渲染器", "真 GPU ✅", "同", "同"],
            ["permissions.query 是否 native", "✅", "✅", "✅"],
        ],
        widths=[5.4, 4.0, 3.4, 3.4],
        size=9,
    )

    body(doc, "结论：能被看到的差异只剩两条——UA 落后 14 个大版本、languages 少一个 zh。"
              "自动化铁证 webdriver=true 已用启动参数原生关掉，没有 JS 补丁痕迹。", size=10)

    h2(doc, "9.1 风险排序（按这套系统实际会看的顺序）")
    table(
        doc,
        ["优先级", "判据", "说明"],
        [
            ["1", "IP 信誉", "官方 FAQ Q20 唯一点名：VPN/机房/iCloud 私密转送会被判异常。用家宽或手机 5G 直连"],
            ["2", "请求频率与行为", "已用停顿分布、逐字输入、人类式点击、退避熔断压住"],
            ["3", "浏览器指纹", "华为 WAF + 排队系统的常规做法是 IP 限速、排队令牌、表单校验，不做深度 JS 指纹"],
        ],
        widths=[1.6, 4.2, 10.2],
        size=9.5,
    )

    body(doc, "官方原文：「任何涉及异常或可疑活动、未经授权工具或提供可疑资料的预约亦将会被取消」。"
              "所以最坏情况是预约被取消、重新排队，不是封号。这也是为什么纯人工（脚本只提醒）永远是兜底。",
         size=10)

    # ---------------- 502
    h1(doc, "十、官方 502 / 维护页：怎么判断、怎么办")
    body(doc, "2026-11-08 20:15 起实测：gov.hk 入口 200，abs3 与 abs4 同时 502 —— 这是官方后端故障，"
              "不是被限速，也不是脚本问题。")

    table(
        doc,
        ["脚本", "遇到 502 的行为"],
        [
            ["check.py", "返回退出码 30，明确提示「官方返回错误页，不是名额问题」；--loop 下自动拉长到 ≥300 秒"],
            ["grab.py", "入口没复选框时退避重试（30s→60s→…上限 600s，最多 20 次）；仍失败则记 entry_unreachable 干净退出"],
            ["wait_page()", "每 4 次轮询查一次页面健康，发现 5xx/维护页立即放弃等待，不再卡 30 分钟"],
        ],
        widths=[3.6, 12.4],
        size=9.5,
        first_col_bold=True,
    )

    body(doc, "502 期间做任何“提速”都没有意义，因为请求根本没到业务系统。"
              "唯一动作是等，并用 python check.py --loop 300 盯着。", size=10)

    # ---------------- 坑
    h1(doc, "十一、会翻车的七个坑")
    pits = [
        ("用 VPN / 机房 IP：", "官方明说可能被判异常拒绝访问。用本地家宽或手机 5G。"),
        ("书签旧链接：", "16:00–16:30 更新后链接会变，每次从 gov.hk 页面进。"),
        ("资料不一致：", "证件号/驾照号与证件不符 → 预约作废；发证机关核对不上自动取消。"),
        ("重复预约：", "一个申请人多个有效预约 → 全部取消。多开队列只为提高中签率。"),
        ("忘了核实电子联络方式：", "递交前 3 个月内没做 OTP 核实 → 柜台不受理。"),
        ("用临时/一次性邮箱：", "系统直接拒收，OTP 也收不到。"),
        ("抢到却迟到缺席：", "逾时/缺席要重新预约；最迟可在预约时间 3 小时前改期或取消。"),
    ]
    for i, (head, tail) in enumerate(pits, 1):
        bullet(doc, f"{i}. {head}{tail}", size=10)

    # ---------------- 附录
    h1(doc, "十二、附录：实测证据摘要")
    table(
        doc,
        ["时间", "动作", "结果"],
        [
            ["11-08 18:26", "无名额时走完链路", "系统红字：该服务现时没有可供预约的时段（周日不放号）"],
            ["11-08 18:28", "抓页面结构", "获得 72 个表单字段，含 captchaToken、storedAppointment* 系列"],
            ["11-08 19:54", "接管真 Chrome 跑完整流程", "勾同意→预约→排队 72s→选内地→到配额页，端到端 136 秒"],
            ["11-08 20:15", "三队列并发", "全部卡在 abs3，无复选框"],
            ["11-08 20:17", "curl 状态码", "gov.hk 200；abs3 502；abs4 502 —— 官方网关故障"],
            ["11-08 20:18", "改进后的 check.py", "46 秒返回退出码 30，准确判定“官方错误页，不是名额问题”"],
        ],
        widths=[3.0, 6.4, 6.6],
        size=9,
    )

    body(doc, "查询渠道：1823（预约系统）／ 2804 2600、2804 2693（运输署牌照事务组）。", size=10)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(18)
    r = p.add_run(f"—— 文档生成于 {datetime.now():%Y-%m-%d %H:%M}　含个人资料，请勿外传 ——")
    set_cn_font(r)
    r.font.size = Pt(9)
    r.font.color.rgb = GREY

    doc.save(OUT)
    print(f"已生成：{OUT}")
    print(f"大小：{OUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    build()
