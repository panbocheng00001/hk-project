# -*- coding: utf-8 -*-
"""插入 8.14「验证码取证」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
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

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.14 节")
