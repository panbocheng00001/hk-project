# -*- coding: utf-8 -*-
"""edge_challenge.py —— 华为云 WAF 挑战识别（今晚踩过的最大坑，单独成模块）
=====================================================================
实测（2026-11-09）：

  · abs3/abs4 在「维护结束但业务未就绪」时，会对**所有**客户端返回
    HTTP 412 + 一段 5~6KB 的混淆 JS（`$_ts` / `nsd` / `cd` 是华为动态防护的
    标志变量），浏览器执行完才会拿到 `HIK0kat3rt9xO` 这类 token cookie 并被放行。

  · 这段 JS 会采集浏览器环境。CDP（Playwright attach）留下的痕迹会让它
    **不发放 token**，表现为页面永远空白（body 只有几十字节）。

  · 因此正确策略不是「重试」，而是：
        1) 识别到挑战 → 立刻停手，绝不反复请求（越试越进不去）
        2) 请人在普通窗口里过一次验证
        3) 用 resume.py 从那个窗口接管，之后一个 goto 都不要发

对外只暴露两个函数：
    detect(page) -> "challenge" / "ok" / "unknown"
    is_challenge(status, text) -> bool
"""
from __future__ import annotations

# 华为 WAF 挑战页的特征串（抓包实测）
CHALLENGE_SIGNS = (
    "$_ts",            # 挑战脚本核心变量
    "nsd=",            # $_ts.nsd
    "HIK0kat3rt9xO",   # 挑战通过后下发的 cookie 名
    "HWWAFSESID",      # WAF 会话 cookie
)

# 这些状态码一律视为「被挡在门外」，不要重试
BLOCK_STATUS = (403, 429, 412)

EMPTY_BODY_HINT = 60   # body 短于这个长度且状态异常 → 基本可判定为挑战空白页


def is_challenge(status: int | None, text: str = "") -> bool:
    """按「状态码 + 响应体特征」判断是否 WAF 挑战页。"""
    if status in BLOCK_STATUS:
        return True
    head = (text or "")[:6000]
    if any(s in head for s in ("$_ts", "nsd=")):
        return True
    return False


def detect(page) -> str:
    """用当前页面的状态判断。

    返回：
        challenge —— 被 WAF 挑战页挡住（需要人工验证交互）
        ok        —— 页面正常
        unknown   —— 判断不出（网络抖动/还在加载）
    """
    try:
        body = page.inner_text("body") or ""
    except Exception:
        return "unknown"
    if any(s in body for s in CHALLENGE_SIGNS):
        return "challenge"
    # 空白页 + 不在排队域名 → 多半是挑战未通过
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    if "abs4.example.test" not in url and len(body.strip()) < EMPTY_BODY_HINT:
        html = ""
        try:
            html = page.content() or ""
        except Exception:
            pass
        if is_challenge(None, html) or len(html) < 200:
            return "challenge"
    return "ok"


def explain() -> str:
    """给终端用户看的处理说明（打印/推钉钉都用它）。"""
    return (
        "华为 WAF 动态防护拦在门口（HTTP 412 / JS 挑战）。\n"
        "  这不是名额问题，也不是网络断了。\n"
        "  处理：\n"
        "   1) 在 Chrome 窗口里手动打开 https://portal.example.test/apps/booking.htm\n"
        "   2) 手动过掉验证（有的话），一路点到「中国内地」排队页\n"
        "   3) 别关窗口，然后跑：python resume.py --attach http://127.0.0.1:9223\n"
        "  注意：不要清 cookie、不要反复刷新——WAF token 是资产。"
    )
