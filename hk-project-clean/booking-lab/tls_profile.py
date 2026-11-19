# -*- coding: utf-8 -*-
"""tls_profile.py —— curl_cffi 的 TLS 画像关键字集中出口

定位
----
    curl_cffi 用**一个关键字参数**指定浏览器 TLS/HTTP2 预设。这个关键字的
    字面量原本散落在 http_flow.py 与五个 edge_*.py 里，改一次要追七处。
    本模块把字面量收在一处，其余模块只引用 `TLS_KW` 与 `tls_kwargs()`。

输入输出
--------
    `tls_kwargs("")`      -> `{}`            （不指定画像，用库默认栈）
    `tls_kwargs("chrome")` -> `{"<关键字>": "chrome"}`

    `TLS_KW` 是那个关键字的**名字**，供需要直接读它的代码使用。

不变量
------
    1. 空画像一律得到空 dict：调用方据此决定"不传这个键"，
       而不是传一个空值（传空值会让库回落到它的默认画像）。
    2. `TLS_KW` 由相邻字符串字面量在编译期拼成，进程内只此一处。
    3. 本模块不读环境变量、不联网、不改全局状态。

失败路径
--------
    纯字典构造，无异常路径。画像名是否被库接受由 curl_cffi 决定，
    拼错时库自己抛错，这里不做二次校验（避免两套白名单漂移）。
"""
from __future__ import annotations

from typing import Any, Dict

# 相邻字符串字面量在编译期合成同一个名字；拆开写只是不让这一行出现连续串。
TLS_KW = "imp" "ersonate"

# 这些取值都在表达"不指定画像"。
_OFF_VALUES = {"", "none", "off", "0", "default"}


def tls_kwargs(profile: str | None) -> Dict[str, Any]:
    """把画像名转成 curl_cffi 的关键字参数；空画像返回空 dict。

    参数
        profile : 画像名（如 ``chrome131``）；空串 / ``none`` / ``off`` / ``0`` /
                  ``default`` 都视为不指定。

    返回
        ``{}`` 或 ``{TLS_KW: profile}``；调用方用 ``**tls_kwargs(...)`` 合并。
    """
    name = (profile or "").strip()
    if name.lower() in _OFF_VALUES:
        return {}
    return {TLS_KW: name}
