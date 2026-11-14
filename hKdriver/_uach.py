import json
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
JS = """async () => {
  const o = {ua: navigator.userAgent};
  o.brands = navigator.userAgentData ? navigator.userAgentData.brands : null;
  o.platform = navigator.userAgentData ? navigator.userAgentData.platform : null;
  if (navigator.userAgentData) {
    try {
      const h = await navigator.userAgentData.getHighEntropyValues(
        ['fullVersionList','platformVersion','model','uaFullVersion','bitness','wow64']);
      o.full = h.fullVersionList || h.uaFullVersion;
      o.uaFullVersion = h.uaFullVersion;
    } catch (e) { o.hevErr = String(e).slice(0,80); }
  }
  o.chromeRuntime = !!(window.chrome && window.chrome.runtime);
  o.screenVsWindow = [screen.width, screen.height, window.outerWidth, window.outerHeight, window.innerWidth, window.innerHeight];
  return o;
}"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1366, "height": 768},
                        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/131.0.0.0 Safari/537.36"))
    pg = ctx.new_page()
    pg.goto("about:blank")
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False, indent=2))
    b.close()
