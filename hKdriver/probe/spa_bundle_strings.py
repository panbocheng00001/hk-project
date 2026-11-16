import re, pathlib
s = pathlib.Path("probe/abs3_main.js").read_text("utf-8", "ignore")
for kw in ["AdminServlet", "tdab2", "cmd", "captchaToken", "challengeToken"]:
    idx=[m.start() for m in re.finditer(re.escape(kw), s)]
    print(f"== {kw}: {len(idx)}")
    for i in idx[:6]:
        print(repr(s[max(0,i-200):i+240]))
        print("-")
