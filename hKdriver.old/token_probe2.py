# -*- coding: utf-8 -*-
"""再试两条备选端点：/common 与 login.live.com 老端点。"""
import json
import urllib.parse
import urllib.request

CID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
RT = ("M.C511_BAY.0.U.MsaArtifacts.-CpaafzT10HdrUPZSU!cF8GCpygeu5OLa50ReZUUkY*3VAkf0gkKO6In5EPDwfHxv99qNk"
      "0ogyMrNN8qDUwEjcBMjOZE2tYZsXiosa7G9SnIGl5MzKDYeVkWvbQ25UA5lXn5j59ae65oN*1JaQVO9GE*yVh0x9aZ48SRmS"
      "kSr0vqHY6k8PTS2523ljUfm9lAFGRjT!FzKmmu*jWPisXV6j8joL1pn75!ZLm!DruoGFrYvcHauEulaxpaLmIF1ZtaGcMz9"
      "2krv19wAbZ6kDHyHy!")

TRIES = [
    ("common/IMAP", "https://login.microsoftonline.com/common/oauth2/v2.0/token",
     {"client_id": CID, "grant_type": "refresh_token", "refresh_token": RT,
      "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"}),
    ("common/default", "https://login.microsoftonline.com/common/oauth2/v2.0/token",
     {"client_id": CID, "grant_type": "refresh_token", "refresh_token": RT,
      "scope": "https://outlook.office.com/.default"}),
    ("live.srf", "https://login.live.com/oauth20_token.srf",
     {"client_id": CID, "grant_type": "refresh_token", "refresh_token": RT,
      "scope": "wl.imap wl.offline_access"}),
    ("consumers/noScope", "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
     {"client_id": CID, "grant_type": "refresh_token", "refresh_token": RT}),
]

for name, url, params in TRIES:
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        print(f"OK   {name:18} token_len={len(d.get('access_token',''))} scope={d.get('scope')}")
        break
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            desc = json.loads(raw).get("error_description", raw)[:120]
        except Exception:
            desc = raw[:120]
        print(f"FAIL {name:18} {desc}")
    except Exception as e:
        print(f"ERR  {name:18} {type(e).__name__} {e}")
