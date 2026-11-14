# -*- coding: utf-8 -*-
"""edge_cookies.py —— WAF 挑战票的 cookie 名判定（唯一口径）

===========================================================================
为什么要有这个文件
---------------------------------------------------------------------------
2026-11-11 23:15 离线复放 412 挑战（`_drill_412.py`）实测拿到：

    enable_HIK0kat3rt9x=true
    HIK0kat3rt9xP=aRKHFFcVQFhou4l9...   ← 令牌，约 500 字符

而仓库里同时散落着两种写法：

    HIK0kat3rt9xO   （109 字符短令牌）  ← mint_align / session_pool / edge_token 写死这个
    HIK0kat3rt9xP   （492+ 字符长令牌） ← run_guard / edge_enter / edge_flow_* 检测这个

`runs/session_form_noquota_w1_171304.json` 里 **O 与 P 同时存在**，
说明服务端会按状态/轮次下发不同后缀（甚至并存）。
写死后缀的后果很具体：真站下发 P 时，`mint_align` 等不到 O，
会把一张好票判成「挑战没跑完」退出码 41 —— 静默误杀。

所以统一成：**认前缀 `HIK0kat3rt9x`，不认后缀。**
===========================================================================
"""
from __future__ import annotations

from typing import Iterable, List

HIK0_PREFIX = "HIK0kat3rt9x"      # 前缀，后缀可能 O / P / 其它
ENABLE_PREFIX = "enable_HIK0kat3rt9x"
# 短令牌（O，约 109 字符）与长令牌（P，约 490+）都算强证据
MIN_TOKEN_LEN = 90


def is_hik0(name: str) -> bool:
    """是不是 HIK0 令牌 cookie（任意后缀）。"""
    return (name or "").startswith(HIK0_PREFIX)


def is_flag(name: str) -> bool:
    """enable_HIK0kat3rt9x 这类开关标记，不是令牌本身。"""
    return (name or "").startswith(ENABLE_PREFIX)


def find_hik0(names: Iterable[str]) -> List[str]:
    """列出所有 HIK0 令牌 cookie 名（保持传入顺序）。"""
    return [n for n in names if is_hik0(n)]


def has_hik0(names: Iterable[str]) -> bool:
    return bool(find_hik0(names))


def strength(cookies: Iterable) -> dict:
    """给一张票定强度。

    cookies 可以是 [(name, value)] 或 [{"name":..,"value":..}]。
    返回 {level, names, lens}：
      strong  —— 有 HIK0 令牌（过完挑战）
      weak    —— 只有 HWWAFSESID（站点不挑战）
      none    —— 什么都没有
    """
    pairs = []
    for c in cookies or []:
        if isinstance(c, dict):
            pairs.append((c.get("name") or "", c.get("value") or ""))
        else:
            try:
                n, v = c
                pairs.append((n or "", v or ""))
            except Exception:
                pass
    hits = [(n, len(v)) for n, v in pairs if is_hik0(n)]
    names = [n for n, _ in hits]
    ses = [n for n, _ in pairs if n == "HWWAFSESID"]
    if any(l >= MIN_TOKEN_LEN for _, l in hits):
        level = "strong"
    elif hits:
        level = "strong"          # 短令牌也算，只是长度可疑
    elif ses:
        level = "weak"
    else:
        level = "none"
    return {"level": level, "names": names,
            "lens": [l for _, l in hits],
            "has_session": bool(ses)}


def describe(names: Iterable[str]) -> str:
    """"HWWAFSESID + HIK0kat3rt9xP(492)" 这样的可读串。"""
    out = []
    for n in names:
        out.append(n)
    return " + ".join(out) if out else "（无）"
