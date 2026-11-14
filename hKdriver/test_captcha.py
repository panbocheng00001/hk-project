# -*- coding: utf-8 -*-
"""自测 captcha.detect：用本地 mock 页面覆盖四种验证码形态。
运行：python test_captcha.py
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import captcha

MOCKS = {
    "v3_隐形_未填充": """<html><body>
        <form><input id="captchaToken" name="captchaToken" type="hidden" value="">
        <button>继续</button></form></body></html>""",

    "v3_隐形_已填充": """<html><body>
        <form><input id="captchaToken" name="captchaToken" type="hidden"
          value="03AFcWeA7xxxxxxxxxxxxxxxxxxxxxxxxxxxx"><button>继续</button></form></body></html>""",

    "v2_勾选框": """<html><body>
        <div class="g-recaptcha" data-sitekey="fake"></div>
        <input id="captchaToken" type="hidden" value="">
        </body></html>""",

    "v2_图片挑战": """<html><body>
        <iframe src="/recaptcha/api2/bframe?k=fake" width="300" height="400"></iframe>
        <input id="captchaToken" type="hidden" value="">
        </body></html>""",

    "图形验证码": """<html><body>
        <img id="captchaImg" src="/captcha.jpg?t=1" width="120" height="40">
        <input name="verifyCode" type="text">
        </body></html>""",

    "hcaptcha": """<html><body><iframe src="https://hcaptcha.com/x" width="300" height="80"></iframe></body></html>""",

    "无验证码": """<html><body><input name="docNo"><button>继续</button></body></html>""",
}

EXPECT = {
    "v3_隐形_未填充": ("none", 0),
    "v3_隐形_已填充": ("none", ">10"),
    "v2_勾选框": ("recaptcha_checkbox", 0),
    "v2_图片挑战": ("recaptcha_image", 0),
    "图形验证码": ("image_code", -1),
    "hcaptcha": ("hcaptcha", -1),
    "无验证码": ("none", -1),
}


def main():
    tmp = Path("runs/_captcha_mock")
    tmp.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, html in MOCKS.items():
        p = tmp / f"{name}.html"
        p.write_text(html, encoding="utf-8")
        files[name] = p.resolve().as_uri()

    ok = 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_page()
        for name, uri in files.items():
            pg.goto(uri)
            time.sleep(0.2)
            d = captcha.detect(pg)
            want_type, want_tok = EXPECT[name]
            tok = d.get("tokenLen", -1)
            tok_ok = (tok > 10) if want_tok == ">10" else (tok == want_tok)
            good = (d["type"] == want_type) and tok_ok
            ok += good
            print(f"{'OK  ' if good else 'FAIL'} {name:16} type={d['type']:20} "
                  f"tokenLen={tok:3} 期望=({want_type},{want_tok})  {d['detail']}")
        b.close()
    print(f"\n通过 {ok}/{len(files)}")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
