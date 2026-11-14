# -*- coding: utf-8 -*-
"""填入钉钉 Webhook 并发送真实测试消息，打印原始返回便于诊断。"""
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

WEBHOOK = ("https://oapi.dingtalk.com/robot/send?access_token="
           "016fe6728159b75c3708e71dd79fdb81a1897ec7d336d0178173849a4d6a2e43")

CFG = Path("config.json")
cfg = json.loads(CFG.read_text(encoding="utf-8"))
notify = cfg.setdefault("notify", {})
notify["enabled"] = True
notify["dingtalk_webhook"] = WEBHOOK
notify.setdefault("dingtalk_secret", "")
notify.setdefault("dingtalk_keyword", "")
notify.setdefault("at_mobiles", ["13631539547"])
notify.setdefault("min_interval", 5)
CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写入 config.json，webhook 尾号:", WEBHOOK[-12:])

ts = datetime.now().strftime("%m-%d %H:%M:%S")
md = (f"### 🚨 钉钉通道测试\n\n"
      f"这是 hKdriver 抢号助手发出的第一条测试消息。\n\n"
      f"如果你在群里看到这条，说明通知链路已经打通。\n\n"
      f"> {ts}　hKdriver 自动通知")
payload = {
    "msgtype": "markdown",
    "markdown": {"title": "钉钉通道测试", "text": md},
    "at": {"atMobiles": ["13631539547"], "isAtAll": False},
}
body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(WEBHOOK, data=body,
                             headers={"Content-Type": "application/json; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=12) as r:
        print("HTTP", r.status)
        print("返回:", r.read().decode("utf-8", "ignore"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print("返回:", e.read().decode("utf-8", "ignore"))
except Exception as e:
    print("异常:", type(e).__name__, str(e)[:200])
