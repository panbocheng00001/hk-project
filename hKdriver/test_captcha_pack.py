# -*- coding: utf-8 -*-
"""自测验证码取证：用带 base64 图片的 mock 页面，验证 captcha_pack 产物齐全。"""
import base64
import struct
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

import captcha
from recorder import Recorder


def tiny_png(w=120, h=40):
    """手搓一张纯色 PNG，当验证码图片用。"""
    raw = b""
    for _ in range(h):
        raw += b"\x00" + bytes([30, 60, 120] * w)
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return png


def main():
    png_b64 = base64.b64encode(tiny_png()).decode()
    html = f"""<html><head><title>mock captcha</title></head><body>
      <img id="captchaImg" src="data:image/png;base64,{png_b64}" width="120" height="40">
      <input name="verifyCode" id="verifyCode" type="text" maxlength="6" placeholder="请输入验证码">
      <input id="captchaToken" type="hidden" value="">
      <script src="https://www.gstatic.com/recaptcha/releases/x/recaptcha__zh.js"></script>
      <div class="g-recaptcha" data-sitekey="6Lc-FAKE-SITEKEY-1234567890"></div>
      <button>继续</button>
    </body></html>"""
    mock = Path("runs/_captcha_mock/pack.html")
    mock.parent.mkdir(parents=True, exist_ok=True)
    mock.write_text(html, encoding="utf-8")

    run_dir = Path("runs/_captcha_pack_test")
    run_dir.mkdir(parents=True, exist_ok=True)
    rec = Recorder(run_dir, "w1", redact=["440000199001011234"])

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_page()
        pg.goto(mock.resolve().as_uri())

        # 1) 常规帧应当自动带上验证码状态
        r = rec.shot(pg, "step2_blank", "本帧应自动识别到验证码")
        print("shot.captcha =", r.get("captcha"))

        # 2) 专项取证
        p = rec.captcha_pack(pg, captcha.detect(pg), "测试取证")
        print("captcha_pack ->", p)
        b.close()

    files = sorted((run_dir / "w1" / "captcha").iterdir())
    print("\n产物：")
    for f in files:
        print(f"  {f.name:34} {f.stat().st_size:>8} B")
    rec.finish()
    print("\nindex.md 存在:", (run_dir / "w1" / "index.md").exists())

    need = [".json", "_evidence.json", ".png", ".html"]
    names = [f.name for f in files]
    ok = all(any(n.endswith(suf) for n in names) for suf in need) and any("img1" in n for n in names)
    print("取证包齐全:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
