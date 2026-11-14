import json, sys
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
proxy = sys.argv[1] if len(sys.argv) > 1 else ""
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(ignore_https_errors=True, user_agent=UA,
                        proxy={"server": proxy} if proxy else None)
    pg = ctx.new_page()
    got = False
    for url in ("https://tls.peet.ws/api/all", "https://tls.browserleaks.com/json"):
        try:
            pg.goto(url, timeout=30000)
            j = json.loads(pg.inner_text("body"))
            tls = j.get("tls") or {}
            h2 = j.get("http2") or {}
            print(f"proxy={proxy or '直连'}")
            print(f"  ip       : {j.get('ip')}")
            print(f"  ja3_hash : {tls.get('ja3_hash') or j.get('ja3_hash')}")
            print(f"  ja4      : {tls.get('ja4')}")
            print(f"  h2       : {h2.get('akamai_fingerprint') or j.get('akamai_hash')}")
            got = True
            break
        except Exception as e:
            print(f"  {url} ERR {type(e).__name__}: {str(e)[:80]}")
    if not got:
        print(f"proxy={proxy or '直连'} 全部失败")
    b.close()
