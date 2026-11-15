# -*- coding: utf-8 -*-
"""handoff.py —— 人工交接登记簿：脚本解决不了的，停下来让人处理，证据先落盘

定位
----
纯 HTTP 主干在三类点上会「解决不了」：
  · 验证码 / 滑块 / OTP —— 按设计就是人判
  · WAF 412 挑战窗口 —— 不硬撞，等窗口或浏览器接力
  · 未知页面形态 / 接口变化 —— 现有解析对不上
这些点统一走本模块：把当时的**完整证据包**（请求头体、响应头体、cookie、
页面 HTML、堆栈）落盘到 runs/handoff/<时间戳>-<标签>/，写 TODO.md 说明
「缺什么、怎么人工过、过了之后从哪续跑」，并把续跑所需的会话 cookie
存成 resume_cookies.json —— 人处理完，脚本可以直接从断点继续。

输入输出
--------
    Handoff.save(tag, sniper, html="", todo="...", extra={...}) -> Path
        落一整套证据，返回目录；不抛异常（记录器坏了不拖垮主流程）

不变量
------
· 证据目录只落 runs/handoff/（gitignore 已含 runs/），不外发
· TODO.md 第一行永远是「要人做什么」，一眼可见
· resume_cookies.json 与 http_flow --cookies 的格式一致，可直接回灌
· 每次交接都在 journal 记一条 ev=handoff 事件

失败路径
--------
· 落盘失败 → 打 stderr，返回 None，主流程继续（告警丢失好过流程死掉）
"""
from __future__ import annotations

import json
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

BASE = Path(__file__).resolve().parent
HANDOFF_DIR = BASE / "runs" / "handoff"


def save(tag: str, sniper: Any, html: str = "", todo: str = "",
         extra: Optional[dict] = None) -> Optional[Path]:
    """落一份人工交接证据包，返回目录路径。

    tag      : 交接点标签（captcha / waf412 / unknown_page / step1_form …）
    sniper   : http_flow.Sniper 实例，取当前会话 cookie 与排队上下文
    html     : 当时的页面 HTML（可空）
    todo     : 要人做什么（一句话，写进 TODO.md 第一行）
    extra    : 附加键值（如 form_fields / last_step / proxy）
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag) or "point"
        d = HANDOFF_DIR / f"{stamp}-{safe_tag}"
        d.mkdir(parents=True, exist_ok=True)

        # 1) 会话 cookie：人工处理完可直接 --cookies 回灌续跑
        try:
            cookies = sniper.dump_cookies()
        except Exception:
            cookies = []
        (d / "resume_cookies.json").write_text(
            json.dumps(cookies, ensure_ascii=False, indent=1), encoding="utf-8")

        # 2) 排队上下文：续跑时 --reuse-event / --reuse-target 的参数
        try:
            ctx = {"customer": sniper.ctx.customer, "event": sniper.ctx.event,
                   "queue_id": sniper.ctx.queue_id, "target": sniper.ctx.target}
        except Exception:
            ctx = {}
        (d / "queue_ctx.json").write_text(
            json.dumps(ctx, ensure_ascii=False, indent=1), encoding="utf-8")

        # 3) 页面证据
        if html:
            (d / "page.html").write_text(html, encoding="utf-8", errors="ignore")

        # 4) 附加数据（表单字段、最近请求等）
        if extra:
            (d / "extra.json").write_text(
                json.dumps(extra, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")

        # 5) TODO：第一行永远是要人做的事
        resume_cmd = f"python http_flow.py --all --cookies {d.name}/resume_cookies.json"
        (d / "TODO.md").write_text(
            f"# 需要人工处理：{todo}\n\n"
            f"- 时间：{datetime.now().isoformat(timespec='seconds')}\n"
            f"- 交接点：{tag}\n"
            f"- 会话票：{d.name}/resume_cookies.json（{len(cookies)} 条 cookie）\n"
            f"- 人工处理完后从这里续跑：`{resume_cmd}`\n"
            f"- 证据：page.html / extra.json / queue_ctx.json\n",
            encoding="utf-8")

        # 6) journal 记一条事件
        try:
            from journal import J
            J().log("handoff", tag, ok=True, note=todo[:80], dir=str(d.name))
        except Exception:
            pass

        print(f"[handoff] 已落证据包：{d}（{todo[:60]}）", file=sys.stderr, flush=True)
        return d
    except Exception as exc:
        print(f"[handoff] 落盘失败：{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return None
