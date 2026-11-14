# -*- coding: utf-8 -*-
"""pagekit —— 页面抓取、结构摘要与对外脱敏

定位
----
入口站是 JS 渲染的 SPA，纯 HTTP 抓到的只有一份壳。本包把「用浏览器把页面渲染出来」
（browser.py）、「把页面压成可读结构」（digest.py）、「把个人资料与口径词换成占位」
（redact.py）串成一条可复现的链路，并给出三种调用面：

    python -m pagekit open   --url URL [--headed]     # 起浏览器、抓一页
    python -m pagekit walk   --url URL --steps S.json  # 按步骤走，每步落一份摘要
    python -m pagekit.mcp_server                       # 以 MCP 工具面暴露同一能力

不变量
------
· 原始快照（含 DOM 文本、截图、请求）只写 `runs/pagekit/`，不进仓库、不外发
· 外发物只从 `digest.build_digest()` 出来，出来之前必过 `Redactor`
· 词表与站点地址只在操作者本机文件里；仓内示例一律 `example.test` 占位

失败路径
--------
· Playwright 缺失或浏览器起不来 → `BrowserUnavailable`，命令行退出码 4
· 词表读不到 → 脱敏继续，`notes` 里写明跳过了哪一条
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
