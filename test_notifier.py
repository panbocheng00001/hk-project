# -*- coding: utf-8 -*-
"""notifier 自测：验证签名、payload、去重、无配置降级都不会抛异常。"""
import json
import time
import urllib.parse

import notifier

print("=== 1) 未配置时应安全降级 ===")
notifier._warned["no_conf"] = False
print("  enabled =", notifier.enabled())
print("  send    =", notifier.send("测试", "没有配置时的行为"))
print("  （上面应打印一次提示，返回 False，且不抛异常）")

print("\n=== 2) 签名算法 ===")
url = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
signed = notifier._sign(url, "SECtestsecret")
q = urllib.parse.urlparse(signed).query
params = urllib.parse.parse_qs(q)
print("  含 access_token :", "access_token" in params)
print("  含 timestamp    :", "timestamp" in params, params.get("timestamp", [""])[0])
print("  sign 长度        :", len(params.get("sign", [""])[0]))
print("  手算可复现       :", notifier._sign(url, "SECtestsecret") != signed, "（时间戳变化，两次不同是对的）")

print("\n=== 3) 模拟配置后构造请求（不实际发出，指向不可达地址） ===")
cfg = json.loads(notifier.CONF.read_text(encoding="utf-8"))
old = cfg.get("notify")
cfg["notify"] = {
    "dingtalk_webhook": "http://127.0.0.1:9/robot/send?access_token=fake",
    "dingtalk_secret": "SECfake",
    "at_mobiles": ["13631539547"],
    "min_interval": 1,
}
notifier.CONF.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("  enabled =", notifier.enabled())
t0 = time.time()
r1 = notifier.send("测试标题", "内容正文", "warn", key="k1")
r2 = notifier.send("测试标题", "内容正文", "warn", key="k1")
print(f"  第1次返回 {r1}（地址不可达，应为 False 且不抛异常）耗时 {time.time()-t0:.2f}s")
print(f"  第2次返回 {r2}（1 秒内同 key 被去重，直接 False）")

print("\n=== 4) 还原配置 ===")
if old is None:
    cfg.pop("notify", None)
else:
    cfg["notify"] = old
notifier.CONF.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("  已还原:", "notify" in json.loads(notifier.CONF.read_text(encoding="utf-8")))
