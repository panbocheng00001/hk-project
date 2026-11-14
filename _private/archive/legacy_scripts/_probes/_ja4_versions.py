import json, sys
from pathlib import Path
from patchright.sync_api import sync_playwright

EXES = {
    "156": "cft/chrome-win64/chrome.exe",
    "131": "cft/chrome-131/chrome-win64/chrome.exe",
    "120": "cft/chrome-120/chrome-win64/chrome.exe",
}

with sync_playwright() as p:
    for tag, exe in EXES.items():
        exe = str(Path(exe).resolve())
        if not Path(exe).exists():
            print(f"{tag}: exe 不存在 {exe}")
            continue
        try:
            b = p.chromium.launch(executable_path=exe, headless=True,
                                  args=["--disable-blink-features=AutomationControlled"])
            ctx = b.new_context(ignore_https_errors=True)
            pg = ctx.new_page()
            pg.goto("https://tls.browserleaks.com/json", timeout=35000)
            j = json.loads(pg.inner_text("body"))
            ver = b.version
            print(f"{tag:4} (实际 {ver:22})  ja4={j.get('ja4')}  h2={j.get('akamai_hash')}  ja3={j.get('ja3_hash')}")
            b.close()
        except Exception as e:
            print(f"{tag}: 启动/访问失败 {type(e).__name__}: {str(e)[:140]}")
