# -*- coding: utf-8 -*-
"""在 8.16 后追加「为什么没用 stealth 类库」的实测结论。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''
    h2(doc, "8.17 那些「一键补环境」的库，实测能不能用")
    body(doc, "你可能听说过这类东西：playwright-stealth、undetected-chromedriver、nodriver、"
              "camoufox、selenium-stealth。它们确实存在，但原理和代价差别很大，这里给实测结论。", size=10)

    table(
        doc,
        ["库", "原理", "与当前方案的关系"],
        [
            ["playwright-stealth", "用 add_init_script 往页面注入一堆 JS 补丁，"
             "覆盖 navigator.webdriver / plugins / languages / permissions 等",
             "**实测在 Chrome 156 上直接崩**（见下）"],
            ["undetected-chromedriver", "改 Chrome 启动方式 + Selenium，专治 Cloudflare",
             "基于 Selenium，与 Playwright 不兼容，要重写全部脚本"],
            ["nodriver", "直连 CDP，不注入 JS 补丁（undetected 作者的新项目）",
             "思路更干净，但要整体重写；可作为将来备选"],
            ["camoufox", "专用 Firefox 构建，指纹全随机",
             "浏览器从 Chrome 变 Firefox，对一个香港政府站反而更显眼"],
            ["selenium-stealth", "同 playwright-stealth，Selenium 版", "不适用"],
        ],
        widths=[3.6, 7.2, 5.2],
        size=8.5,
    )

    body(doc, "实测 playwright-stealth（2026-11-08，Chrome 156.0.8078.80 + Playwright 1.57）：", size=10, bold=True)
    code_block(
        doc,
        [
            "=== B：playwright-stealth ===",
            "Page.evaluate: ReferenceError: opts is not defined",
            "    at Navigator.get [as userAgent] (<anonymous>:5:42)",
            "",
            "→ 它把 navigator.userAgent 换成了一个引用未定义变量 opts 的 getter，",
            "  只要页面读一次 navigator.userAgent 就抛异常。",
        ],
        size=8.5,
    )
    body(doc, "这不是「留痕」的问题，是**直接把页面搞崩**：TDABS 的前端脚本只要读一次 UA 就会抛错，"
              "轻则功能异常，重则白屏。20 分钟窗口里出现这种故障等于当天报废。", size=10)

    body(doc, "为什么参数级方案反而更干净：", size=10, bold=True)
    table(
        doc,
        ["对比项", "我们的做法（启动参数 + 真实 Chrome）", "stealth 类库"],
        [
            ["navigator.webdriver", "原生就是 false", "靠 JS 覆盖，会留 hasOwnProperty"],
            ["属性是否被改", "`webdriverOwn = false`（没改过）", "通常是 true（改过）"],
            ["函数是否 native", "全部 true", "覆盖过的会变 false"],
            ["浏览器升级后", "参数依然有效", "**补丁可能失效甚至崩页面**"],
            ["失败方式", "降级为普通浏览器", "页面直接报错"],
        ],
        widths=[4.0, 7.0, 5.0],
        size=8.5,
    )

    callout(
        doc,
        "结论",
        "补丁类库解决的是「浏览器默认就带自动化标记」这个问题——而我们已经在参数层解决了它。"
        "在参数层已经干净的情况下再加 JS 补丁，只会把 `hasOwnProperty` 和 `toString` 这些新破绽补上去。"
        "所以：现阶段不用任何 stealth 库；将来若真要对抗更高强度检测，选 nodriver 重写，而不是往 Playwright 上贴补丁。",
    )

'''

text = P.read_text(encoding="utf-8")
marker = "    # ---------------- 反检测"
assert text.count(marker) == 1
text = text.replace(marker, NEW + marker)
P.write_text(text, encoding="utf-8")
print("已插入 8.17 节")
