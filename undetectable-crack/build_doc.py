#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成《Undetectable 补丁使用说明（跨电脑部署）》docx"""
import sys
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BODY_FONT = "宋体"
HEAD_FONT = "微软雅黑"
MONO_FONT = "Consolas"

doc = Document()

# 页面与默认样式
sec = doc.sections[0]
sec.left_margin = Cm(2.2); sec.right_margin = Cm(2.2)
sec.top_margin = Cm(2.0); sec.bottom_margin = Cm(2.0)

normal = doc.styles["Normal"]
normal.font.name = BODY_FONT
normal.font.size = Pt(10.5)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
normal.paragraph_format.space_after = Pt(4)
normal.paragraph_format.line_spacing = 1.25

for name, size in (("Title", 22), ("Heading 1", 15), ("Heading 2", 12.5)):
    st = doc.styles[name]
    st.font.name = HEAD_FONT
    st.font.size = Pt(size)
    st.font.color.rgb = RGBColor(0x1F, 0x28, 0x33)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), HEAD_FONT)


def shade(paragraph, color="F2F3F5"):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    pPr.append(shd)


def code(text, note=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.left_indent = Cm(0.2)
    shade(p)
    for i, line in enumerate(text.strip("\n").split("\n")):
        run = p.add_run(("" if i == 0 else "\n") + line)
        run.font.name = MONO_FONT
        run.font.size = Pt(9)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), MONO_FONT)
    if note:
        r = p.add_run("\n" + note)
        r.font.size = Pt(8.5)
        r.font.color.rgb = RGBColor(0x66, 0x70, 0x80)


def bullet(text, level=0):
    p = doc.add_paragraph(text, style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(2)
    return p


def kv_table(rows, widths, header=True):
    t = doc.add_table(rows=0, cols=len(widths))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_i, row in enumerate(rows):
        cells = t.add_row().cells
        for c_i, val in enumerate(row):
            cells[c_i].width = Cm(widths[c_i])
            para = cells[c_i].paragraphs[0]
            para.paragraph_format.space_after = Pt(1)
            run = para.add_run(str(val))
            run.font.size = Pt(9)
            run.font.name = BODY_FONT
            run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
            if header and r_i == 0:
                run.font.bold = True
    doc.add_paragraph()
    return t


# ------------------------------------------------------------------ 封面
doc.add_heading("Undetectable 补丁使用说明", level=0)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
r = p.add_run("适用对象：把已在 A 机做好的补丁，部署到 B 机（任意 Windows 10/11 x64 电脑）")
r.font.size = Pt(10.5)
r.font.color.rgb = RGBColor(0x44, 0x4C, 0x56)

kv_table([
    ["项目", "内容"],
    ["目标程序", "Undetectable.exe 1.0.0（Qt 6.11.2 + 自研 Chromium 内核）"],
    ["原始文件 sha256", "aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a"],
    ["原始文件大小", "18,703,728 字节"],
    ["补丁后 sha256", "98f892ddbbb8126a871efcff5c798cb788f07869994bc8c0dae9ddd9db8811e0"],
    ["改动量", "9 字节 / 3 处"],
    ["适用系统", "Windows 10 / 11 x64（与官方安装包一致）"],
], [4.0, 12.5])

# ------------------------------------------------------------------ 0
doc.add_heading("0. 一分钟版", level=1)
doc.add_paragraph("只想快点跑起来，按这四步：")
bullet("① 在 B 机正常安装官方 Undetectable，启动并登录一次，然后完全退出（托盘右键退出 + 任务管理器确认没有 Undetectable.exe）。")
bullet("② 打开安装目录，把原来的 Undetectable.exe 改名备份成 Undetectable.exe.bak。")
bullet("③ 把本说明附带的“补丁后 Undetectable.exe”复制进去，文件名保持 Undetectable.exe。")
bullet("④ 正常启动、登录。之后新建 profile / 调 API 都不再有每日额度限制。")
p = doc.add_paragraph()
r = p.add_run("如果要自己核验，别跳过第 3～5 章的校验步骤；升级过官方版本后再替换会失效，见第 8 章。")
r.font.size = Pt(9)
r.font.color.rgb = RGBColor(0x99, 0x4A, 0x00)

# ------------------------------------------------------------------ 1
doc.add_heading("1. 文件清单", level=1)
kv_table([
    ["文件", "作用", "是否必需"],
    ["out\\Undetectable.exe", "已打好 3 个补丁的程序本体，直接替换官方文件", "方案 A 必需"],
    ["patch_undetectable.py", "现场打补丁的脚本（自带版本/字节校验，输出到 out\\ 目录）", "方案 B 必需"],
    ["create20.py", "批量建 20 个不同指纹 profile 并开窗口（可选）", "可选"],
    ["FINGERPRINT_API.md", "本机 REST 接口与 /profile/create 字段字典", "可选参考"],
], [4.2, 9.3, 3.0])

doc.add_paragraph("所有文件放在同一个文件夹里即可，例如 D:\\undetectable-patch\\。")

# ------------------------------------------------------------------ 2
doc.add_heading("2. 前置条件", level=1)
bullet("系统：Windows 10/11 x64；B 机上先装好官方同一版本的 Undetectable（1.0.0）。")
bullet("权限：替换文件需要管理员权限（安装在 Program Files 时需要；装在用户目录一般不需要）。")
bullet("账号：补丁只改本地判断，登录仍走官方服务器，所以需要一个能正常登录的账号（Free 账号即可）。")
bullet("版本一致性：官方一旦升级，exe 的字节就变了，本补丁点会失效——升级后要用新版本重新做补丁。")

# ------------------------------------------------------------------ 3
doc.add_heading("3. 方案 A：直接替换 exe（推荐，最省事）", level=1)

doc.add_heading("3.1 安装并登录一次官方版本", level=2)
doc.add_paragraph("先让官方程序自己跑一次，生成 profiles 目录、settings.json 等运行环境；随后再替换本体，避免首次运行缺配置。")

doc.add_heading("3.2 完全退出程序", level=2)
doc.add_paragraph("托盘图标右键退出；再打开任务管理器，确认列表里没有 Undetectable.exe 以及它拉起的 chrome 子进程。文件被占用时无法覆盖。")

doc.add_heading("3.3 找到安装目录", level=2)
doc.add_paragraph("右键桌面快捷方式 →「打开文件所在的位置」。常见位置：")
code(r"""C:\Users\<用户名>\AppData\Local\Programs\Undetectable
C:\Program Files\Undetectable""")

doc.add_heading("3.4 备份原文件（必做）", level=2)
doc.add_paragraph("把目录里的 Undetectable.exe 改名为 Undetectable.exe.bak，或用命令行复制一份：")
code(r"""copy "Undetectable.exe" "Undetectable.exe.bak" """, "后续想回滚，把 .bak 改回原名即可（见第 9 章）。")

doc.add_heading("3.5 校验原文件（可选但强烈建议）", level=2)
doc.add_paragraph("确认 B 机上的官方版本与补丁针对的版本完全一致：")
code(r"""certutil -hashfile "Undetectable.exe" SHA256""",
     "输出应为 aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a。不一致就别替换，改用第 4 章现场打补丁并先跑 --check-only。")

doc.add_heading("3.6 替换并启动", level=2)
bullet("把 out\\Undetectable.exe 复制到安装目录，文件名必须是 Undetectable.exe（覆盖原件）。")
bullet("双击启动，正常登录。首次启动 Windows 可能提示「未知发布者」，选「仍要运行」——补丁改过字节，数字签名自然失效，属预期。")
bullet("登录后按第 6 章逐项验证。")

# ------------------------------------------------------------------ 4
doc.add_heading("4. 方案 B：在 B 机现场打补丁", level=1)
doc.add_paragraph("适用于：不想传输 18MB 的 exe、或 B 机版本需要重新确认的场景。")

doc.add_heading("4.1 准备 Python", level=2)
doc.add_paragraph("安装 Python 3.8+（勾选 Add to PATH）。本脚本只用标准库，无需 pip 装任何包。")

doc.add_heading("4.2 先只校验，不写文件", level=2)
code(r"""python patch_undetectable.py --src "C:\Program Files\Undetectable\Undetectable.exe" --check-only""",
     "三个补丁点都显示 [+] 且末尾「不匹配: 0」才继续；有任何一个不匹配说明版本不对，别硬打。")

doc.add_heading("4.3 生成补丁文件", level=2)
code(r"""python patch_undetectable.py --src "C:\Program Files\Undetectable\Undetectable.exe" --out "D:\undetectable-patch\out\Undetectable.exe" """,
     "脚本不会改源文件，只写 --out。成功时最后一行会打印补丁后 sha256，应为 98f892dd…11e0。")

doc.add_heading("4.4 替换", level=2)
doc.add_paragraph("按 3.2 退出程序 → 备份原件 → 用生成的 out\\Undetectable.exe 覆盖 → 启动。")

# ------------------------------------------------------------------ 5
doc.add_heading("5. 补丁到底改了什么", level=1)
doc.add_paragraph("三处都是客户端本地的判断，不碰网络、不碰账号数据：")
kv_table([
    ["编号", "位置（文件偏移）", "原始字节 → 补丁后", "作用"],
    ["P1", "0x212800", "80 79 10 00 74 0B… → 32 C0 C3", "isLimitReached() 恒为 false：每日建 profile 额度失效（界面与 API 都不再拦）"],
    ["P2", "0x1FF004", "E8 07 FE EC FF → 31 C0 90 90 90", "跳过 plan_name==\"free\" 比较，isFree=0：解除 Free 版功能锁、放行 Free 账号启动 profile 的分支"],
    ["P3", "0x23AB6A", "75 19 → EB 19", "REST /profile/create 的权限判断改为无条件通过，不再要求 profile_create_local 令牌"],
], [1.6, 3.2, 5.2, 6.5])

doc.add_paragraph("对应的虚拟地址（给需要自己核对的人）：P1 0x140213400、P2 0x1401FFC04、P3 0x14023B76A；换算关系 file_off = VA − 0x140001000 + 0x400。")

# ------------------------------------------------------------------ 6
doc.add_heading("6. 验证清单", level=1)
kv_table([
    ["#", "怎么验", "通过标准"],
    ["1", "登录后连续新建 profile，数量超过原 Free 额度", "不再弹「You reach daily limit and need to change plan」"],
    ["2", "设置里确认已开启本地 API 并记下端口，浏览器访问 http://127.0.0.1:<端口>/status", "返回 JSON（不是连接失败）"],
    ["3", "python create20.py --port <端口> --start", "20/20 创建成功、20/20 启动成功；不出现 Limit has been reached / You haven't got permissions"],
    ["4", "同时开多个 profile，看任务管理器", "多个 chrome 渲染进程并存，数量只受硬件限制"],
], [1.2, 8.6, 6.7])

# ------------------------------------------------------------------ 7
doc.add_heading("7. 本机 API 快速上手", level=1)
doc.add_paragraph("端口来自程序设置（settings.json 的 apiPort），不是固定值。常用几步：")
code(r"""# 探活
curl http://127.0.0.1:<端口>/status

# 建一个 profile（最小体）
curl -X POST http://127.0.0.1:<端口>/profile/create ^
  -H "Content-Type: application/json" ^
  -d "{\"unique_name\":true,\"name\":\"fp_01\",\"os\":\"windows\",\"browser\":\"chrome\",\"language\":\"en-US\",\"timezone\":\"America/New_York\",\"geolocation\":\"40.7128:-74.0060\",\"resolution\":\"1920x1080\",\"cpu\":8,\"memory\":16,\"type\":\"local\"}"

# 启动（开窗口） / 停止
curl http://127.0.0.1:<端口>/profile/start/<profile_id>
curl http://127.0.0.1:<端口>/profile/stop/<profile_id>""")
doc.add_paragraph("完整字段表（os / browser / language / timezone / geolocation / resolution / cpu / memory / webrtc / proxy / cookies / accounts / tags / folder / notes / configid / type / group）见 FINGERPRINT_API.md。批量建 20 个不同指纹直接用：")
code(r"""python create20.py --start --url https://ipinfo.io""")

# ------------------------------------------------------------------ 8
doc.add_heading("8. 别让自动更新把补丁冲掉", level=1)
doc.add_paragraph("程序自带 updater（会访问 cdn.undetectnews.com 拉版本与文件）。它升级时会用官方 exe 覆盖你的补丁，所以要么关掉更新，要么升级后重打。两种做法：")
doc.add_heading("做法一：hosts 屏蔽更新域名（推荐）", level=2)
doc.add_paragraph("以管理员身份编辑 C:\\Windows\\System32\\drivers\\etc\\hosts，追加：")
code(r"""127.0.0.1 cdn.undetectnews.com""",
     "改完 ipconfig /flushdns。副作用：浏览器内核/配置的在线更新也一起停了。")
doc.add_heading("做法二：升级后重打", level=2)
doc.add_paragraph("允许它更新，但每次升级完，用第 4 章的 --check-only 重新确认字节，再重新生成并替换。版本一变，旧补丁文件就别再用了。")
p = doc.add_paragraph()
r = p.add_run("建议：把“原始 exe + patch_undetectable.py + 本说明”留一份存档，换机器或升级后都能照做。")
r.font.size = Pt(9.5)

# ------------------------------------------------------------------ 9
doc.add_heading("9. 回滚", level=1)
bullet("把 Undetectable.exe.bak 改回 Undetectable.exe 覆盖即可，其它文件不动。")
bullet("如果当初没备份：卸载后重装官方版，或用 --check-only 确认过的原始 exe 恢复。")
bullet("回滚后记得清掉 hosts 里那条屏蔽（如果加过）。")

# ------------------------------------------------------------------ 10
doc.add_heading("10. 常见问题", level=1)
kv_table([
    ["现象", "原因 / 处理"],
    ["启动提示「未知发布者」/ 数字签名无效", "正常。补丁改了字节，签名必然失效，选「仍要运行」。"],
    ["杀软/EDR 报可疑或直接删文件", "修改过的未签名 exe 常被启发式命中。自行决定是否加白名单，或改用官方版本。"],
    ["仍然提示 daily limit / API 返回 Limit has been reached", "补丁没生效：①替换的不是补丁文件；②被自动更新覆盖；③B 机版本不同导致补丁点不匹配。用 certutil 核对 sha256，或跑 --check-only。"],
    ["API 建号返回 You haven't got permissions to create profiles!", "P3 未生效（权限令牌门没过）。核对 0x23AB6A 是否为 EB 19。"],
    ["启动 profile 返回 You haven't got permissions!", "P2 未生效（Free 分支没跳过）。核对 0x1FF004 起的 5 字节。"],
    ["API 连不上", "设置里没开本地 API、端口记错、或被防火墙拦。先用 /status 探活。"],
    ["能建号但窗口起不来", "看是否缺对应 Chromium 内核版本（程序会按需下载内核），或代理不可用。"],
    ["登录提示 session limit / Session quota exceeded", "服务端账号级并发登录会话数限制，本地补丁改不了，去 Cloud Dashboard 踢掉其它会话。"],
], [6.3, 10.2])

# ------------------------------------------------------------------ 11
doc.add_heading("11. 边界说明", level=1)
bullet("补丁只影响本机客户端判断；服务端仍会收到 profiles_count 与登录会话信息，账号层面的风控不受补丁控制。")
bullet("补丁不改变指纹质量本身——指纹差异来自配置（os/browser/language/timezone/geolocation/resolution/cpu/memory/webrtc 与 configid 指向的 config）。")
bullet("本说明仅覆盖 1.0.0 这一份样本（sha256 aa0797b3…）。换版本请重新分析后再打，不要套用偏移。")

doc.save(r"D:\tool-project\ads\undetectable-crack\Undetectable补丁使用说明.docx")
print("saved")
