# -*- coding: utf-8 -*-
"""测试：用「邮箱----密码----client_id----refresh_token」里的 client_id 换令牌。
不入库，只打印结果骨架（不输出令牌本体）。
"""
import json
import urllib.parse
import urllib.request

CID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
RT = ("M.C511_BAY.0.U.MsaArtifacts.-CpaafzT10HdrUPZSU!cF8GCpygeu5OLa50ReZUUkY*3VAkf0gkKO6In5EPDwfHxv99qNk"
      "0ogyMrNN8qDUwEjcBMjOZE2tYZsXiosa7G9SnIGl5MzKDYeVkWvbQ25UA5lXn5j59ae65oN*1JaQVO9GE*yVh0x9aZ48SRmS"
      "kSr0vqHY6k8PTS2523ljUfm9lAFGRjT!FzKmmu*jWPisXV6j8joL1pn75!ZLm!DruoGFrYvcHauEulaxpaLmIF1ZtaGcMz9"
      "2krv19wAbZ6kDHyHy!")

SCOPES = [
    "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "https://graph.microsoft.com/Mail.Read offline_access",
    "https://outlook.office.com/Mail.Read offline_access",
    "https://outlook.office.com/.default offline_access",
]

for scope in SCOPES:
    body = urllib.parse.urlencode({
        "client_id": CID, "grant_type": "refresh_token",
        "refresh_token": RT, "scope": scope,
    }).encode()
    req = urllib.request.Request(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        tok = d.get("access_token", "")
        print(f"OK   scope={scope[:52]:52} token_len={len(tok)} expires={d.get('expires_in')}")
        print(f"     scope_granted={d.get('scope')}")
        with open("token_probe.txt", "w", encoding="utf-8") as fh:
            fh.write(tok)
        break
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "ignore")
        try:
            err = json.loads(msg).get("error_description", msg)[:150]
        except Exception:
            err = msg[:150]
        print(f"FAIL scope={scope[:52]:52} {err}")
    except Exception as e:
        print(f"ERR  scope={scope[:52]:52} {type(e).__name__} {e}")
