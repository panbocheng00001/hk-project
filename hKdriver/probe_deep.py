# -*- coding: utf-8 -*-
"""侦察：一路走到 第2步输入资料 / 第3步选择日期，导出全部字段（不提交）。
用法: python probe_deep.py [--headless] [--fill]
  --fill 用占位资料填充第2步并尝试进入第3步看日历（仍不提交）
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

EXTRACT = r"""
() => {
  const out = {url: location.href, fields: [], texts: [], radios: []};
  document.querySelectorAll('input,select,textarea').forEach(el => {
    const lab = el.closest('label')?.innerText
      || document.querySelector(`label[for="${el.id}"]`)?.innerText || '';
    const holder = el.closest('.MuiFormControl-root') || el.closest('tr') || el.parentElement;
    out.fields.push({tag: el.tagName.toLowerCase(), type: el.type||'', name: el.name||'', id: el.id||'',
      value: el.value||'', required: !!el.required, accept: el.accept||'',
      label: (lab||'').trim().replace(/\s+/g,' ').slice(0,100),
      ctx: (holder?holder.innerText:'').trim().replace(/\s+/g,' ').slice(0,140)});
  });
  document.querySelectorAll('button').forEach(b => out.texts.push('BTN:' + b.innerText.trim()));
  document.querySelectorAll('p,span.MuiFormHelperText-root,li,h4').forEach(el => {
    const t = el.innerText?.trim();
    if (t && t.length < 160 && el.children.length === 0) out.texts.push(t);
  });
  out.texts = [...new Set(out.texts)].slice(0, 160);
  return out;
}
"""


def shot(page, tag):
    ts = time.strftime("%H%M%S")
    d = page.evaluate(EXTRACT)
    (OUT / f"{tag}_{ts}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        page.screenshot(path=str(OUT / f"{tag}_{ts}.png"), full_page=True)
    except Exception:
        pass
    print(f"\n===== {tag} {d['url']}")
    print("TEXTS:", " | ".join(d["texts"][:60]))
    for f in d["fields"]:
        print(f"  <{f['tag']} {f['type']} name={f['name']} id={f['id']} req={f['required']} "
              f"accept={f['accept']} label={f['label']!r} ctx={f['ctx'][:70]!r}")
    return d


def click_text(page, text):
    for b in page.query_selector_all("button"):
        if b.inner_text().strip() == text:
            b.click()
            page.wait_for_timeout(3000)
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--fill", action="store_true")
    a = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=a.headless)
        ctx = browser.new_context(locale="zh-CN", timezone_id="Asia/Hong_Kong",
                                  viewport={"width": 1366, "height": 1000})
        page = ctx.new_page()
        page.goto(ENTRY, wait_until="load", timeout=60000)
        page.wait_for_selector('input[name="conditionsCheck"]', timeout=45000)
        page.query_selector('input[name="conditionsCheck"]').check(force=True)
        click_text(page, "预约")
        page.wait_for_timeout(2500)
        page.query_selector('input[type="radio"][value="withOutIas"]').check(force=True)
        click_text(page, "继续")

        # 等真正进入 abs3 SelectCountry（排队页 abs4 的 URL 里也可能含 SelectCountry 参数）
        t0 = time.time()
        while time.time() - t0 < 300:
            if "abs3.td.gov.hk" in page.url and "SelectCountry" in page.url:
                break
            # 排队页偶尔会弹"是否继续访问"，顺手点掉
            try:
                if "abs4.td.gov.hk" in page.url:
                    for b in page.query_selector_all("button"):
                        if b.inner_text().strip() in ("是的。", "是的"):
                            b.click()
                            break
            except Exception:
                pass
            page.wait_for_timeout(2000)
        print("after queue url:", page.url, f"({int(time.time()-t0)}s)")
        page.wait_for_timeout(1500)
        shot(page, "30_country")

        # 选中国内地
        for r in page.query_selector_all('input[type="radio"]'):
            lab = (r.evaluate("e => e.closest('label')?.innerText || ''") or "").strip()
            if "中国内地" == lab:
                r.check(force=True)
                print("country radio:", lab)
                break
        click_text(page, "继续")
        page.wait_for_timeout(4000)
        shot(page, "31_step2")

        if a.fill:
            print("fill mode not implemented yet")
        ctx.close()
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
