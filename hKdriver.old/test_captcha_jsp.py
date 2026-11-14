# -*- coding: utf-8 -*-
"""验证：官方 JSP 页那种「有空的 captchaToken 字段、但没挂验证码脚本」的情况，
guard 必须立刻放行，不能白等 20 秒。"""
import time

from playwright.sync_api import sync_playwright

import captcha

HTML = ('<html><body><form>'
        '<input id="captchaToken" name="captchaToken" type="hidden" value="">'
        '<input name="licenseNo"><button type="button">继续</button>'
        '</form></body></html>')

PATH = "runs/_captcha_mock/jsp_like_official.html"
open(PATH, "w", encoding="utf-8").write(HTML)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto("file:///E:/googleProduct/hKdriver/" + PATH)

    d = captcha.detect(pg)
    print("detect:", {k: d.get(k) for k in ("type", "tokenLen", "v3")})

    t0 = time.time()
    r = captcha.guard(pg, "w1", say=lambda m: print("   ", m))
    print(f"guard -> {r}，耗时 {time.time()-t0:.2f}s（修复前会白等 20 秒）")

    # 再测一个真的挂了 recaptcha 脚本的空 token 页：应该会等
    open("runs/_captcha_mock/v3_real.html", "w", encoding="utf-8").write(
        '<html><body><script src="https://www.gstatic.com/recaptcha/releases/x/recaptcha__zh.js">'
        '</script><input id="captchaToken" type="hidden" value=""></body></html>')
    pg.goto("file:///E:/googleProduct/hKdriver/runs/_captcha_mock/v3_real.html")
    d2 = captcha.detect(pg)
    print("detect(v3 真实脚本):", {k: d2.get(k) for k in ("type", "tokenLen", "v3")})
    b.close()
