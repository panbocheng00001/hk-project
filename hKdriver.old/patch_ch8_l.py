# -*- coding: utf-8 -*-
"""插入 8.16「反检测审计」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
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

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.16 节")
