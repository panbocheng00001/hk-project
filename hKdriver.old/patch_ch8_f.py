# -*- coding: utf-8 -*-
"""插入 8.13「验证码怎么处理」一节。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
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

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.13 节")
