# -*- coding: utf-8 -*-
"""notifier.py —— 把关键时刻推到钉钉（或静默降级）
=====================================================================
支持钉钉自定义机器人 Webhook：
    POST https://oapi.dingtalk.com/robot/send?access_token=xxx
    若机器人开了「加签」，再带 timestamp + sign

配置写在 config.json 的 notify 段：
    "notify": {
        "dingtalk_webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxxx",
        "dingtalk_secret": "SEC...",          // 没开加签就留空
        "at_mobiles": ["13631539547"],        // 要 @ 谁，可留空
        "min_interval": 5                     // 同样内容 5 秒内不重复发
    }

没配置时所有调用都是空操作，不会打断抢号流程。
所有发送都在 try/except 里，网络挂了也不影响主流程。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONF = BASE / "config.json"

_cache: dict[str, float] = {}
_warned = {"no_conf": False}


def _load() -> dict:
    try:
        cfg = json.loads(CONF.read_text(encoding="utf-8"))
        return cfg.get("notify") or {}
    except Exception:
        return {}


def enabled() -> bool:
    n = _load()
    return bool(n.get("dingtalk_webhook")) and n.get("enabled", True)


def _sign(webhook: str, secret: str) -> str:
    """钉钉加签：HmacSHA256(timestamp + '\\n' + secret) → base64 → urlencode"""
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def _diagnose(body: str) -> str:
    """把钉钉的错误码翻译成「你该做什么」。"""
    low = body.lower()
    if "310000" in low or "关键词" in body or "签名" in body:
        if "签名" in body:
            return ("钉钉要求加签：在群机器人「安全设置」里勾选『加签』，"
                    "把 SEC 开头的密钥填进 config.json → notify.dingtalk_secret")
        if "关键词" in body:
            return ("钉钉要求关键词：在群机器人「安全设置」里看『自定义关键词』，"
                    "把那个词原样填进 config.json → notify.dingtalk_keyword")
        return "钉钉安全校验未通过，检查加签密钥或自定义关键词。"
    if "660026" in body:
        return "钉钉限流（20 条/分钟），等一分钟再发；正常抢号不会触发。"
    if "300001" in body or "invalid token" in low:
        return "Webhook 的 access_token 无效，检查地址是否完整。"
    return ""


def send(title: str, text: str, level: str = "info", key: str | None = None) -> bool:
    """发一条 markdown 消息。level: info / warn / alert。
    返回是否发送成功；未配置或失败都返回 False，不抛异常。"""
    n = _load()
    webhook = n.get("dingtalk_webhook")
    if not webhook:
        if not _warned["no_conf"]:
            print("[通知] 未配置钉钉 Webhook，通知已跳过（config.json → notify.dingtalk_webhook）")
            _warned["no_conf"] = True
        return False

    # 去重：同一 key 在 min_interval 内只发一次
    gap = float(n.get("min_interval", 5))
    now = time.time()
    dedup = key or (title + "|" + text[:60])
    if now - _cache.get(dedup, 0) < gap:
        return False
    _cache[dedup] = now

    icon = {"info": "ℹ️", "warn": "⚠️", "alert": "🚨"}.get(level, "ℹ️")
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    # 钉钉机器人若用「自定义关键词」做安全设置，消息必须含该关键词，否则被拒
    kw = (n.get("dingtalk_keyword") or "").strip()
    head = f"{kw}\n\n" if kw else ""
    md = (f"{head}### {icon} {title}\n\n"
          f"{text}\n\n"
          f"> {ts}　hKdriver 自动通知")

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": md},
    }
    ats = n.get("at_mobiles") or []
    if ats:
        payload["at"] = {"atMobiles": ats, "isAtAll": False}

    url = _sign(webhook, n["dingtalk_secret"]) if n.get("dingtalk_secret") else webhook
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "ignore")
        ok = '"errcode":0' in body.replace(" ", "")
        if not ok:
            tip = _diagnose(body)
            print(f"[通知] 钉钉返回异常：{body[:160]}")
            if tip:
                print(f"[通知] 处理办法：{tip}")
        return ok
    except urllib.error.HTTPError as e:
        print(f"[通知] 发送失败 HTTP {e.code}: {e.read()[:120]}")
    except Exception as e:
        print(f"[通知] 发送失败 {type(e).__name__}: {str(e)[:120]}")
    return False


def send_async(title: str, text: str, level: str = "info", key: str | None = None) -> bool:
    """后台线程发送，绝不阻塞抢号主流程。网络慢/挂掉都不影响。"""
    if not enabled():
        return False
    import threading
    threading.Thread(target=send, args=(title, text, level, key), daemon=True).start()
    return True


# ---------------------------------------------------------------- 语义化快捷入口
def seat_open(count: int | None = None, url: str = "") -> bool:
    return send_async("有名额了！", f"系统出现可预约时段。\n\n{url}\n\n**立刻**跑 `python grab.py --workers 3`",
                      "alert", key="seat_open")


def queued(where: str = "") -> bool:
    """拿到排队位（还没进填写页）。"""
    return send_async("已拿到排队位", f"已进入排队，等系统放行。\n\n{where}\n\n"
                                  f"这段时间别关窗口、别刷新。", "warn", key="queued")


def admitted(office_hint: str = "") -> bool:
    return send_async("已放行，进入填写页",
                      f"排队通过，开始填资料。**20 分钟倒计时开始**。\n\n{office_hint}",
                      "warn", key="admitted")


def bounced(where: str = "") -> bool:
    """被官方弹回入口页（会话/网络被判异常，或排队位丢失）。"""
    return send_async("被弹回入口页", f"{where}\n\n已自动重走勾选流程；"
                                   f"若连续多次弹回，先换家宽 IP 再跑。", "warn", key="bounced")


def otp_missing(email: str, waited: int) -> bool:
    """等 OTP 超时：明确告诉是哪个邮箱、等了多久。"""
    return send_async(
        "⚠ OTP 没收到",
        f"邮箱：**{email}**\n\n"
        f"已等待 **{waited} 秒**仍没抓到 tdabs 的验证码邮件。\n\n"
        f"建议：\n"
        f"1) 翻垃圾邮件/推广分类\n"
        f"2) 页面上点「重新发送」一次\n"
        f"3) 还不行就换邮箱（改 config.json 的 email 后重跑）",
        "alert", key=f"otp_missing|{email}|{waited // 120}")


def captcha_need_human(kind: str, waited: int = 0) -> bool:
    return send_async("需要你完成验证码", f"类型：**{kind}**\n\n请切到浏览器窗口完成，"
                                    f"已等待 {waited} 秒。完成后面板会自动继续。",
                      "alert", key=f"captcha|{kind}")


def otp_arrived(code: str) -> bool:
    return send_async("验证码已到达", f"OTP：**{code}**\n\n已写入 otp.txt，脚本会自动填入。",
                      "info", key="otp")


def success(ref: str = "", when: str = "") -> bool:
    return send_async("★ 抢到预约了",
                      f"预约编号：**{ref or '见页面'}**\n\n时间：{when or '见确认邮件'}\n\n"
                      f"立刻去邮箱确认 tdabs@td.gov.hk 的确认信。", "alert", key="success")


def failed(reason: str) -> bool:
    return send_async("本轮没抢到 / 出错", reason, "warn", key="failed")


def waf_blocked(where: str = "") -> bool:
    """被华为 WAF 的 JS 挑战挡住（HTTP 412）：必须人工过验证，再让脚本接管。"""
    return send_async(
        "被 WAF 挑战挡住（HTTP 412）",
        f"{where}\n\n"
        f"**这不是名额问题**。处理顺序：\n\n"
        f"1) 在 Chrome 窗口打开 `https://www.gov.hk/sc/apps/tddiabs.htm`\n"
        f"2) 手动过掉验证，一路点到「中国内地」排队页\n"
        f"3) 别关窗口，跑 `python resume.py --attach http://127.0.0.1:9223`\n\n"
        f"⚠ 不要清 cookie、不要反复刷新：WAF token 是资产，重试越多越进不去。",
        "alert", key="waf_blocked")


def ip_restricted(code: str = "") -> bool:
    """官方 WAF 按出口 IP 拦截：关掉 VPN/代理/WARP，换真实家宽或手机 5G。"""
    return send_async("出口 IP 被官方拦截",
                      f"{code}\n\n这是华为云 WAF 的 restricted access 页，与名额无关。\n\n"
                      f"处理：关掉 VPN / 代理 / WARP / iCloud 私密转送，"
                      f"改用家宽或手机 5G 再跑。", "alert", key="ip_restricted")


def site_error(code: str) -> bool:
    return send_async("官方站点异常",
                      f"{code}\n\n这不是你的问题，等恢复；用 check.py --loop 300 盯着。",
                      "warn", key="site_error|" + code[:20])


if __name__ == "__main__":
    # 自测：不真的发送，只打印会发什么
    print("已配置:", enabled())
    print("示例消息：")
    ts = datetime.now().strftime("%m-%d %H:%M:%S")
    print(f"### 🚨 需要你完成验证码\n\n类型：**recaptcha_image**\n\n请切到浏览器窗口完成。\n\n> {ts}　hKdriver 自动通知")
