# -*- coding: utf-8 -*-
"""在 8.8 故障表补一条「浏览器顶部黄色提示条」，并在 6.6 补实测数据。"""
from pathlib import Path

P = Path("build_doc.py")
t = P.read_text(encoding="utf-8")

# 1) 8.8 故障表
old = '''            ["页面全白、body 是空的",
             "用了无头模式（headless），被 WAF 拦",
             "脚本默认已有头。别加 --headless"],'''
new = '''            ["页面全白、body 是空的",
             "用了无头模式（headless），被 WAF 拦",
             "脚本默认已有头。别加 --headless"],
            ["浏览器顶部黄色「正受到自动测试软件控制」",
             "启动时带了 --enable-automation",
             "已加 --excludeSwitches=enable-automation 消除；"
             "这条提示只在本地可见，网页和服务端都读不到"],'''
assert t.count(old) == 1, f"anchor1={t.count(old)}"
t = t.replace(old, new)

# 2) 6.6 指纹表下补一段实测
old2 = '''**结论：能被"看到"的差异只剩两条**'''
new2 = '''**关于浏览器顶部那条「Chrome 正受到自动测试软件的控制」**：它是 Chrome 自己的 UI 提示，
不在页面 DOM 里、也不会随请求发出去，服务端读不到。但它的成因 `--enable-automation`
同时会让 `navigator.webdriver = true`，所以两个一起处理。现已统一加
`--excludeSwitches=enable-automation` + `--disable-infobars`，实测：

| 项目 | 加参数后实测 |
|---|---|
| 顶部黄色提示条 | **不再出现**（桌面截图确认） |
| `navigator.webdriver` | `false` |
| `hasOwnProperty('webdriver')` | `false`（没有 JS 补丁痕迹） |
| UA / Browser.getVersion | `Chrome/156.0.8078.80`（与本机一致） |

**结论：能被"看到"的差异只剩两条**'''
assert t.count(old2) == 1, f"anchor2={t.count(old2)}"
t = t.replace(old2, new2)

P.write_text(t, encoding="utf-8")
print("已补充说明")
