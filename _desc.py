import json
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

JS = """() => {
  const info = (o, k) => {
    try {
      const d = Object.getOwnPropertyDescriptor(o, k);
      if (!d) return 'absent';
      return {configurable: d.configurable, enumerable: d.enumerable,
              get: !!d.get, set: !!d.set, writable: d.writable,
              valueType: typeof d.value};
    } catch (e) { return 'err:' + e.message; }
  };
  return {
    'window.chrome':        info(window, 'chrome'),
    'window.screen':        info(window, 'screen'),
    'window.outerWidth':    info(window, 'outerWidth'),
    'window.outerHeight':   info(window, 'outerHeight'),
    'screen.width':         info(screen, 'width'),
    'screen.height':        info(screen, 'height'),
    'screen.availHeight':   info(screen, 'availHeight'),
    'navigator.deviceMemory': info(navigator, 'deviceMemory'),
    'navigator.userAgentData': info(navigator, 'userAgentData'),
    'Screen.prototype.width': info(Screen.prototype, 'width'),
    'Screen.prototype.availHeight': info(Screen.prototype, 'availHeight'),
    'Navigator.prototype.deviceMemory': info(Navigator.prototype, 'deviceMemory'),
    'Window.prototype.chrome': info(Window.prototype, 'chrome'),
    'window.chrome keys': (() => { try { return Object.keys(window.chrome); } catch(e){ return 'err'; } })(),
    'chrome desc': (() => { try { return JSON.stringify(Object.getOwnPropertyDescriptor(window, 'chrome')); } catch(e){ return 'err'; } })(),
    'can write chrome.runtime': (() => { try { window.chrome.runtime = {id: 1}; return !!window.chrome.runtime; } catch(e){ return 'throw:'+e.message; } })(),
    'can define screen.width': (() => { try { Object.defineProperty(screen, 'width', {get: () => 1920}); return screen.width; } catch(e){ return 'throw:'+e.message; } })(),
    'can define Navigator.prototype.deviceMemory': (() => { try { Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 8}); return navigator.deviceMemory; } catch(e){ return 'throw:'+e.message; } })(),
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(ignore_https_errors=True, user_agent=UA,
                        locale="zh-HK", timezone_id="Asia/Hong_Kong",
                        viewport={"width": 1366, "height": 768})
    pg = ctx.new_page()
    pg.goto("https://example.com/", timeout=30000)
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False, indent=2))
    b.close()
