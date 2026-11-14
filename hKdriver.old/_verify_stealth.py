import importlib.util, json
from pathlib import Path
from patchright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("m", "waf_flow_abs4.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CHROME = m.CHROME
JS = """async () => {
  const o = {};
  o.ua = navigator.userAgent;
  o.webdriver = navigator.webdriver;
  o.plugins = navigator.plugins.length;
  o.chromeRuntime = !!(window.chrome && window.chrome.runtime);
  o.deviceMemory = navigator.deviceMemory;
  o.screenVsWindow = {screen:[screen.width,screen.height,screen.availHeight],
                      outer:[window.outerWidth,window.outerHeight],
                      inner:[window.innerWidth,window.innerHeight]};
  if (navigator.userAgentData) {
    o.brands = navigator.userAgentData.brands;
    try {
      const h = await navigator.userAgentData.getHighEntropyValues(['fullVersionList','platformVersion','bitness']);
      o.fullVersionList = h.fullVersionList; o.platformVersion = h.platformVersion;
    } catch(e){ o.hevErr = String(e).slice(0,80); }
  } else { o.userAgentData = null; }
  return o;
}"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled",
                                "--no-first-run", "--no-default-browser-check"])
    ctx = b.new_context(ignore_https_errors=True,
                        user_agent=m.UA, locale="zh-HK", timezone_id="Asia/Hong_Kong",
                        viewport={"width": 1366, "height": 768})
    ctx.add_init_script(m.STEALTH)
    pg = ctx.new_page()
    pg.goto("about:blank")
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False, indent=2))
    b.close()
