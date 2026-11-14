# -*- coding: utf-8 -*-
"""侦察 第1步认证 -> 第2步输入资料 的表单结构。
用法: python probe_fields.py [--headless]
输出: probe/step2_fields.json + 截图
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
OUT = BASE / "probe"
OUT.mkdir(exist_ok=True)
ENTRY = "https://www.gov.hk/sc/apps/tddiabs.htm"

EXTRACT_JS = r"""
() => {
  const out = {url: location.href, title: document.title, fields: [], texts: []};
  document.querySelectorAll('input,select,textarea,button').forEach(el => {
    const lab = el.closest('label')?.innerText
      || document.querySelector(`label[for="${el.id}"]`)?.innerText
      || el.getAttribute('aria-label') || '';
    const box = el.closest('.MuiFormControl-root') || el.parentElement;
    out.fields.push({
      tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '',
      id: el.id || '', value: el.value || '', required: !!el.required,
      disabled: !!el.disabled, label: (lab || '').trim().replace(/\s+/g, ' ').slice(0, 120),
      ctx: (box ? box.innerText : '').trim().replace(/\s+/g, ' ').slice(0, 160),
      options: el.tagName === 'SELECT' ? [...el.options].map(o => [o.value, o.text]) : undefined,
      radioChecked: el.type === 'radio' ? el.checked : undefined
    });
  });
  document.querySelectorAll('p,div.MuiFormHelperText-root,h1,h2,h3,h4,li').forEach(el => {
    const t = el.innerText?.trim();
    if (t && t.length < 200 && el.children.length === 0) out.texts.push(t);
  });
  out.texts = [...new Set(out.texts)].slice(0, 120);
  return out;
}
"""


def step(page, tag, js=EXTRACT_JS):
    ts = time.strftime("%H%M%S")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    data = page.evaluate(js)
    (OUT / f"{tag}_{ts}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        page.screenshot(path=str(OUT / f"{tag}_{ts}.png"), full_page=True)
    except Exception:
        pass
    print(f"\n===== {tag} url={data['url']}")
    print("TEXTS:", " | ".join(data["texts"][:40]))
    for f in data["fields"]:
        if f["tag"] == "button" or f["type"] in ("hidden",):
            continue
        print(f"  <{f['tag']} type={f['type']} name={f['name']} id={f['id']} req={f['required']} "
              f"label={f['label']!r} ctx={f['ctx'][:60]!r} opts={f.get('options')}")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Hong_Kong",
                                  viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()

        page.goto(ENTRY, wait_until="load", timeout=60000)
        page.wait_for_timeout(3500)
        page.query_selector('input[name="conditionsCheck"]').check(force=True)
        for b in page.query_selector_all("button"):
            if b.inner_text().strip() == "预约":
                b.click()
                break
        page.wait_for_timeout(4000)
        step(page, "20_selectauth")

        # 选「不使用智方便」
        r = page.query_selector('input[type="radio"][value="withOutIas"]')
        print("radio withOutIas:", bool(r))
        r.check(force=True)
        page.wait_for_timeout(400)
        # 点「继续」
        for b in page.query_selector_all("button"):
            if b.inner_text().strip() == "继续":
                b.click()
                break
        page.wait_for_timeout(5000)
        step(page, "21_step2")

        ctx.close()
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
