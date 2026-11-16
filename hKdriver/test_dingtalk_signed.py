# -*- coding: utf-8 -*-
"""填入加签密钥，发真实测试消息（打印原始返回）。"""
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

WEBHOOK = ("https://oapi.dingtalk.com/robot/send?access_token="
           "016fe6728159b75c3708e71dd79fdb81a1897ec7d336d0178173849a4d6a2e43")
SECRET = "SEC91c8f16ea4d103db75e0ee54483e559e69d8d894ad9e393f84d29fedeb91f1bd"

# 1) 写入 config.json
CFG = Path("config.json")
cfg = json.loads(CFG.read_text(encoding="utf-8"))
n = cfg.setdefault("notify", {})
n["enabled"] = True
n["dingtalk_webhook"] = WEBHOOK
n["dingtalk_secret"] = SECRET
n["dingtalk_keyword"] = ""
CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("config.json 已写入加签密钥（尾号 ...%s）" % SECRET[-8:])


def signed(webhook: str, secret: str) -> str:
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    return f"{webhook}&timestamp={ts}&sign={sign}"


ts = datetime.now().strftime("%m-%d %H:%M:%S")
md = (f"### ✅ 钉钉通道已打通\n\n"
      f"这是 hKdriver 抢号助手的联调消息。\n\n"
      f"- 名额出现\n- 排队放行\n- 需要点验证码\n- OTP 到达\n"
      f"- 抢到预约\n- 失败/超时\n- 官方 502\n\n"
      f"以上七类事件以后都会推到这里。\n\n"
      f"> {ts}　hKdriver 自动通知")
payload = {
    "msgtype": "markdown",
    "markdown": {"title": "钉钉通道已打通", "text": md},
    "at": {"atMobiles": ["13631539547"], "isAtAll": False},
}
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(signed(WEBHOOK, SECRET), data=body,
                             headers={"Content-Type": "application/json; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=12) as r:
        print("HTTP", r.status)
        print("返回:", r.read().decode("utf-8", "ignore"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", "ignore"))
except Exception as e:
    print("异常:", type(e).__name__, str(e)[:200])
