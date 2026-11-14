# -*- coding: utf-8 -*-
"""probe_select.py —— 只看 SelectCountry 页在挑战窗口内长什么样（零判断，纯取证）

用途：R2 在 ② 拿不到 c/e 时，先把页面的输入项/按钮/DOM 结构拍下来，
避免"猜选择器 → 打空 → 又烧一个出口"。

    python probe_select.py --proxy http://ip:port --ip-id s1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")
ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run", "--no-default-browser-check",
    "--disable-dev-shm-usage", "--lang=zh-HK",
    "--window-position=-32000,-32000", "--window-size=1366,820",
]
SELECT = ("https://abs3.td.gov.hk/SelectCountry?lang=sc&actionType=apply"
          "&authMethod=withOutIas")
OUT = BASE / "runs" / "r2"

DUMP = r"""
() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const txt = (e) => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  return {
    url: location.href,
    title: document.title,
    bodyText: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 1200),
    forms: q('form').map(f => ({ action: f.getAttribute('action') || '', method: f.method || '' })),
    inputs: q('input,select,textarea').map(e => ({
      tag: e.tagName, type: e.type || '', name: e.name || '', id: e.id || '',
      value: (e.value || '').slice(0, 40), checked: !!e.checked,
    })),
    buttons: q('button,a[role=button],input[type=button],input[type=submit]')
      .map(e => ({ tag: e.tagName, id: e.id || '', cls: (e.className || '').toString().slice(0, 60), text: txt(e) }))
      .slice(0, 40),
    labels: q('label').map(txt).slice(0, 30),
  };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="")
    ap.add_argument("--ip-id", default="s1")
    a = ap.parse_args()

    from patchright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    pw = browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(executable_path=CHROME, headless=False, args=ARGS,
                                     ignore_default_args=["--enable-automation"])
        kw = dict(ignore_https_errors=True, locale="zh-HK", timezone_id="Asia/Hong_Kong",
                  viewport={"width": 1366, "height": 768})
        if a.proxy:
            kw["proxy"] = {"server": a.proxy}
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        t0 = time.time()
        r = page.goto(SELECT, wait_until="domcontentloaded", timeout=60000)
        print(f"{time.strftime('%H:%M:%S')} goto {r.status if r else '?'} {time.time()-t0:.1f}s {page.url[:100]}")
        page.wait_for_timeout(2500)
        info = page.evaluate(DUMP)
        (OUT / f"{a.ip_id}-select.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT / f"{a.ip_id}-select.html").write_text(page.content() or "", encoding="utf-8")
        print("title:", info.get("title"))
        print("url:", info.get("url"))
        print("body:", (info.get("bodyText") or "")[:400])
        print("forms:", json.dumps(info.get("forms"), ensure_ascii=False))
        print("inputs:", json.dumps(info.get("inputs"), ensure_ascii=False)[:800])
        print("buttons:", json.dumps(info.get("buttons"), ensure_ascii=False)[:800])
        print("labels:", json.dumps(info.get("labels"), ensure_ascii=False)[:400])
        return 0
    finally:
        for f in (lambda: browser and browser.close(), lambda: pw and pw.stop()):
            try:
                f()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
