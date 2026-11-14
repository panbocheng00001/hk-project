import json
from pathlib import Path
from patchright.sync_api import sync_playwright

CHROME = str((Path(__file__).resolve().parent / "cft" / "chrome-win64" / "chrome.exe"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# 用「原型改写」的方式，属性都是 configurable:true，实测可改
STEALTH = r"""
(() => {
  const defG = (o, k, v) => { try { Object.defineProperty(o, k, {get: () => v, configurable: true}); return true; } catch (e) { return false; } };
  const s = {
    uad: !!navigator.userAgentData,
    rt: !!(window.chrome && window.chrome.runtime),
    sw: null, dm: null,
  };
  // 1) chrome.runtime —— 直接在 window.chrome 上挂（它是 writable data property）
  try {
    window.chrome = window.chrome || {};
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        id: undefined,
        connect: () => ({onMessage: {addListener(){}, removeListener(){}}, postMessage(){}, disconnect(){}}),
        sendMessage: () => {},
        onConnect: {addListener(){}, removeListener(){}},
        onMessage: {addListener(){}, removeListener(){}},
      };
    }
  } catch (e) {}
  // 2) screen —— 改 Screen.prototype 的 getter
  try {
    Object.defineProperty(Screen.prototype, 'width',      {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'height',     {get: () => 1080, configurable: true});
    Object.defineProperty(Screen.prototype, 'availWidth', {get: () => 1920, configurable: true});
    Object.defineProperty(Screen.prototype, 'availHeight',{get: () => 1040, configurable: true});
    Object.defineProperty(Screen.prototype, 'availLeft',  {get: () => 0, configurable: true});
    Object.defineProperty(Screen.prototype, 'availTop',   {get: () => 0, configurable: true});
  } catch (e) {}
  // 3) window 外框
  defG(window, 'outerWidth', 1383); defG(window, 'outerHeight', 801);
  defG(window, 'screenX', 8); defG(window, 'screenY', 8);
  // 4) deviceMemory
  try { Object.defineProperty(Navigator.prototype, 'deviceMemory', {get: () => 8, configurable: true}); } catch (e) {}
  return s;
})();
"""

JS = """() => ({
  ua: navigator.userAgent,
  rt: !!(window.chrome && window.chrome.runtime),
  dm: navigator.deviceMemory,
  sw: [screen.width, screen.height, screen.availHeight, window.outerWidth, window.innerWidth],
  brands: navigator.userAgentData ? navigator.userAgentData.brands : null,
})"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(ignore_https_errors=True, user_agent=UA,
                        locale="zh-HK", timezone_id="Asia/Hong_Kong",
                        viewport={"width": 1366, "height": 768})

    print("=== 方式 A: add_init_script ===")
    ctx.add_init_script(STEALTH)
    pg = ctx.new_page()
    pg.goto("https://example.com/", timeout=30000)
    print(json.dumps(pg.evaluate(JS), ensure_ascii=False))

    print("\n=== 方式 B: 页面内 evaluate ===")
    pg2 = ctx.new_page()
    pg2.goto("https://example.com/", timeout=30000)
    pg2.evaluate(STEALTH)
    print(json.dumps(pg2.evaluate(JS), ensure_ascii=False))

    print("\n=== 方式 C: CDP addScriptToEvaluateOnNewDocument ===")
    pg3 = ctx.new_page()
    cdp = ctx.new_cdp_session(pg3)
    cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH})
    pg3.goto("https://example.com/", timeout=30000)
    print(json.dumps(pg3.evaluate(JS), ensure_ascii=False))

    b.close()
