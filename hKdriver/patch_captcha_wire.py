# -*- coding: utf-8 -*-
"""把 grab.py 里的 captcha.guard 调用接上取证回调，并修掉 docstring 里的零宽字符。"""
from pathlib import Path

G = Path("grab.py")
t = G.read_text(encoding="utf-8")

old = ("cst = captcha.guard(page, self.who, shot=self.shot, "
       "say=lambda m: log(self.who, m))")
new = ("cst = captcha.guard(page, self.who, shot=self.shot, "
       "say=lambda m: log(self.who, m),\n"
       "                            on_captcha=self.captcha_pack)")
n = t.count(old)
t = t.replace(old, new)

# 在 mark() 后面插入 captcha_pack 包装方法
anchor = """    def mark(self, note: str, extra: dict | None = None) -> None:
        if self.rec is not None:
            try:
                self.rec.mark(note, extra)
            except Exception:
                pass
"""
addition = anchor + '''
    def captcha_pack(self, page, info: dict, note: str = "") -> None:
        """验证码专项取证（截图 + 源码 + iframe/sitekey/图片本体），存到 captcha\\\\ 子目录。"""
        if self.rec is None:
            return
        try:
            p = self.rec.captcha_pack(page, info, note)
            log(self.who, f"🧩 验证码取证已存：{p}")
        except Exception as exc:
            log(self.who, f"验证码取证失败：{type(exc).__name__} {str(exc)[:60]}")
'''
assert t.count(anchor) == 1, f"anchor count={t.count(anchor)}"
t = t.replace(anchor, addition)

G.write_text(t, encoding="utf-8")
print(f"已替换 {n} 处 guard 调用，并插入 captcha_pack 方法")

# 修 captcha.py 注释里的零宽字符
C = Path("captcha.py")
c = C.read_text(encoding="utf-8")
before = c
c = c.replace("r\u200becorder", "recorder")
if c != before:
    C.write_text(c, encoding="utf-8")
    print("已清理 captcha.py 里的零宽字符")
else:
    print("captcha.py 无需清理")
