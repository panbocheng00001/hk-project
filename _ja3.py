import json, sys
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    for url in ("https://tls.peet.ws/api/all", "https://tls.browserleaks.com/json"):
        try:
            pg.goto(url, timeout=25000)
            txt = pg.inner_text("body")
            j = json.loads(txt)
            tls = j.get("tls") or {}
            h2 = j.get("http2") or {}
            print(f"=== {url} ===")
            print("  ja3      :", tls.get("ja3") or j.get("ja3_hash"))
            print("  ja3_hash :", tls.get("ja3_hash"))
            print("  ja4      :", tls.get("ja4"))
            print("  h2       :", h2.get("akamai_fingerprint") or j.get("akamai_hash"))
            print("  ua       :", j.get("user_agent", "")[:80])
            break
        except Exception as e:
            print(f"{url} ERR {type(e).__name__}: {str(e)[:100]}")
    b.close()
