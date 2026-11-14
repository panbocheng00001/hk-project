# -*- coding: utf-8 -*-
"""批量试探钉钉机器人的「自定义关键词」。
原理：关键词不匹配的消息会被钉钉拒收（不会出现在群里），
      所以一条条试，只有命中的那条才会真的发到群里，最多刷 1 条。
"""
import json
import time
import urllib.error
import urllib.request

WEBHOOK = ("https://oapi.dingtalk.com/robot/send?access_token="
           "016fe6728159b75c3708e71dd79fdb81a1897ec7d336d0178173849a4d6a2e43")

CANDIDATES = [
    "预约", "预约系统", "香港", "香港驾照", "驾照", "驾驶证", "换证",
    "抢号", "运输署", "车牌", "牌照", "提醒", "通知", "监控",
    "tdabs", "TDABS", "Tdabs", "gov", "GovHK", "hk", "HK",
    "test", "测试", "alert", "hKdriver", "driver", "yuyue",
]


def try_one(word: str) -> tuple[str, str]:
    payload = {
        "msgtype": "text",
        "text": {"content": f"{word}｜关键词探测，收到请忽略"},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "ignore")
        code = "?"
        try:
            code = str(json.loads(body).get("errcode"))
        except Exception:
            pass
        return code, body[:120]
    except urllib.error.HTTPError as e:
        return f"HTTP{e.code}", e.read().decode("utf-8", "ignore")[:120]
    except Exception as e:
        return type(e).__name__, str(e)[:120]


hit = None
for i, w in enumerate(CANDIDATES, 1):
    code, body = try_one(w)
    mark = " ✅ 命中" if code == "0" else ""
    print(f"{i:2}. {w:12} errcode={code}{mark}")
    if code == "0":
        hit = w
        break
    if code not in ("310000",):
        print("    非关键词错误，停止探测：", body)
        break
    time.sleep(1.6)

print()
if hit:
    print(f"找到关键词：{hit}")
    cfg = json.loads(open("config.json", encoding="utf-8").read())
    cfg.setdefault("notify", {})["dingtalk_keyword"] = hit
    open("config.json", "w", encoding="utf-8").write(
        json.dumps(cfg, ensure_ascii=False, indent=2))
    print("已写入 config.json → notify.dingtalk_keyword")
else:
    print("没命中。可能关键词是自造词，或机器人还开了加签。")
