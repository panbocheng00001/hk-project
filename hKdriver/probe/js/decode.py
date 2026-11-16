import re
src = open("main.js", encoding="utf-8").read()
def dec(m):
    try:
        c = int(m.group(1), 16)
        return chr(c) if not (0xD800 <= c <= 0xDFFF) else m.group(0)
    except Exception:
        return m.group(0)
plain = re.sub(r"\\u([0-9a-fA-F]{4})", dec, src)
open("main.decoded.js", "w", encoding="utf-8", errors="replace").write(plain)
print("decoded len", len(plain))
for kw in ["身份证明文件号码","驾驶执照号码","电邮","牌照事务处","预约日期","SelectAuth","SelectCountry","身份证明文件影像","一次性密码","上传"]:
    print(f"{kw:>10} : {plain.count(kw)}")
