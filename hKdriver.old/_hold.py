import sys, time
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
headless = "--headful" not in sys.argv

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=headless,
                          args=["--disable-blink-features=AutomationControlled",
                                "--no-first-run", "--no-default-browser-check"])
    ctx = b.new_context(ignore_https_errors=True)
    pg = ctx.new_page()
    pg.goto("about:blank")
    print(f"launched headless={headless}, holding 14s", flush=True)
    time.sleep(14)
    b.close()
    print("closed", flush=True)
