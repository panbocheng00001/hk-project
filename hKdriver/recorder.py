# -*- coding: utf-8 -*-
"""recorder.py —— 全程录制：每一步的截图 + 页面代码 + DOM 结构 + 时间线
=====================================================================
为什么要有它：
  · 抢到号那一刻只有 20 分钟，来不及思考页面结构；
  · 事后想优化脚本（改选择器、加自动选时段），必须有当时的真实 DOM；
  · 出问题要能复盘：是不是按钮文案变了、是不是多了验证码、是不是慢在哪一步。

产物结构（每次运行一个目录）：
    runs\\20261109-170012\\w1\\
        01_entry.png        整页截图
        01_entry.html       当时页面源码（敏感串已打码）
        01_entry.json       表单字段 / 按钮 / 文本摘要
        ...
        steps.jsonl         每行一条：序号、时间、步骤、URL、备注
        index.md            时间线表格，直接看这个

用法（由 grab.py 自动调用，不需要单独跑）：
    rec = Recorder(run_dir, "w1", redact=["证件号","驾照号","邮箱"])
    rec.shot(page, "step2_fields", "第2步空表")
    rec.finish()
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime
from pathlib import Path

import captcha

# 验证码取证：把判断「是哪家验证码、能不能自动过」需要的东西一次抓全
CAPTCHA_EVIDENCE_JS = r"""
() => {
  const out = {iframes: [], scripts: [], sitekeys: [], images: [], inputs: [], globals: {}, meta: {}};
  document.querySelectorAll('iframe').forEach(f => out.iframes.push({
    src: f.src || '', title: f.title || '', id: f.id || '',
    w: f.getBoundingClientRect().width, h: f.getBoundingClientRect().height
  }));
  document.querySelectorAll('script[src]').forEach(s => out.scripts.push(s.src));
  document.querySelectorAll('[data-sitekey]').forEach(e => out.sitekeys.push({
    tag: e.tagName, sitekey: e.getAttribute('data-sitekey') || '',
    cls: (e.className || '').toString().slice(0, 80),
    style: e.getAttribute('style') || ''
  }));
  document.querySelectorAll('img').forEach(im => {
    const s = im.src || '';
    if (!s) return;
    const box = im.getBoundingClientRect();
    if (box.width < 20 || box.height < 12) return;
    out.images.push({src: s.slice(0, 300000), id: im.id || '', alt: im.alt || '',
                     w: box.width, h: box.height, cls: (im.className || '').toString().slice(0, 80)});
  });
  document.querySelectorAll('input,textarea').forEach(el => {
    const n = ((el.name || '') + ' ' + (el.id || '')).trim();
    if (/captcha|verify|checkcode|validcode|yzm|authcode|code/i.test(n)) {
      out.inputs.push({name: el.name || '', id: el.id || '', type: el.type || '',
                       maxlength: el.maxLength, placeholder: el.placeholder || '',
                       label: (el.closest('label')?.innerText || '').trim().slice(0, 60)});
    }
  });
  out.globals = {
    grecaptcha: typeof window.grecaptcha, turnstile: typeof window.turnstile,
    hcaptcha: typeof window.hcaptcha, g_recaptcha: !!document.querySelector('.g-recaptcha')
  };
  out.meta = {title: document.title, url: location.href, readyState: document.readyState};
  return out;
}
"""

DOM_JS = r"""
() => {
  const out = {url: location.href, title: document.title, fields: [], buttons: [], texts: [], tables: []};
  document.querySelectorAll('input,select,textarea').forEach(el => {
    const lab = el.closest('label')?.innerText
      || document.querySelector(`label[for="${el.id}"]`)?.innerText
      || el.getAttribute('aria-label') || '';
    const holder = el.closest('.MuiFormControl-root') || el.closest('tr') || el.parentElement;
    out.fields.push({
      tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
      value: el.value || '', required: !!el.required, disabled: !!el.disabled,
      checked: !!el.checked, accept: el.accept || '',
      label: (lab || '').trim().replace(/\s+/g, ' ').slice(0, 100),
      ctx: (holder ? holder.innerText : '').trim().replace(/\s+/g, ' ').slice(0, 160)
    });
  });
  document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]').forEach(b => {
    out.buttons.push({
      text: (b.innerText || b.value || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      disabled: !!b.disabled, id: b.id || '',
      cls: (b.className || '').toString().slice(0, 90),
      aria: b.getAttribute('aria-label') || ''
    });
  });
  document.querySelectorAll('p,span,h2,h3,h4,li,div.MuiFormHelperText-root,td,th').forEach(el => {
    const t = el.innerText?.trim();
    if (t && t.length < 200 && el.children.length === 0) out.texts.push(t);
  });
  out.texts = [...new Set(out.texts)].slice(0, 300);
  document.querySelectorAll('table').forEach(tb => {
    const rows = [...tb.querySelectorAll('tr')].slice(0, 40)
      .map(tr => [...tr.children].map(c => (c.innerText || '').trim().replace(/\s+/g, ' ')).join(' | '));
    if (rows.length) out.tables.push(rows);
  });
  return out;
}
"""


class Recorder:
    def __init__(self, run_dir: Path, who: str, redact: list[str] | None = None,
                 enabled: bool = True):
        self.enabled = enabled
        self.dir = Path(run_dir) / who
        self.dir.mkdir(parents=True, exist_ok=True)
        self.who = who
        self.n = 0
        self.redact = [r for r in (redact or []) if r and len(str(r)) >= 4]
        self.steps_path = self.dir / "steps.jsonl"
        self.records: list[dict] = []

    # ---------------------------------------------------------------- 工具
    def _mask(self, s: str) -> str:
        for v in self.redact:
            v = str(v)
            keep = 2 if len(v) > 7 else 0
            masked = (v[:keep] + "*" * max(3, len(v) - keep * 2) + v[-keep:]) if keep else "*" * len(v)
            s = s.replace(v, masked)
        return s

    def _dirsize(self) -> int:
        try:
            return sum(f.stat().st_size for f in self.dir.iterdir() if f.is_file())
        except Exception:
            return 0

    # ---------------------------------------------------------------- 主接口
    def shot(self, page, step: str, note: str = "", full: bool = True) -> dict | None:
        """在关键时刻拍一张：截图 + 源码 + DOM。返回本次记录。"""
        if not self.enabled:
            return None
        self.n += 1
        tag = f"{self.n:02d}_{step}"
        rec = {
            "n": self.n,
            "t": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "ts": time.time(),
            "step": step,
            "note": note,
            "url": "",
            "files": {},
        }
        try:
            rec["url"] = page.url
        except Exception:
            pass

        try:
            page.screenshot(path=str(self.dir / f"{tag}.png"), full_page=full)
            rec["files"]["png"] = f"{tag}.png"
        except Exception as exc:
            rec["png_error"] = f"{type(exc).__name__}: {str(exc)[:80]}"

        try:
            html = page.content()
            (self.dir / f"{tag}.html").write_text(self._mask(html), encoding="utf-8")
            rec["files"]["html"] = f"{tag}.html"
            rec["html_size"] = len(html)
        except Exception as exc:
            rec["html_error"] = f"{type(exc).__name__}: {str(exc)[:80]}"

        try:
            data = page.evaluate(DOM_JS)
            for f in data.get("fields", []):
                if f.get("value"):
                    f["value"] = self._mask(str(f["value"]))
            (self.dir / f"{tag}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            rec["files"]["json"] = f"{tag}.json"
            rec["n_fields"] = len(data.get("fields", []))
            rec["n_buttons"] = len(data.get("buttons", []))
            rec["buttons"] = [b["text"] for b in data.get("buttons", []) if b["text"]][:15]
        except Exception as exc:
            rec["dom_error"] = f"{type(exc).__name__}: {str(exc)[:80]}"

        try:
            rec["text"] = self._mask(page.inner_text("body"))[:800]
        except Exception:
            pass

        # 每一帧都顺带判一次有没有验证码：这样验证码在任何时刻冒出来都会留在记录里，
        # 不依赖脚本「正好在提交前」去检测。
        try:
            cap = captcha.detect(page)
            rec["captcha"] = {k: cap.get(k) for k in ("type", "tokenLen", "v3", "detail")}
            if cap.get("type") not in (None, "none"):
                rec["captcha_alert"] = True
        except Exception:
            pass

        self.records.append(rec)
        try:
            with self.steps_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return rec

    def mark(self, note: str, extra: dict | None = None) -> None:
        """只记一条时间线事件（不截图），比如「开始填表」「点继续」。"""
        if not self.enabled:
            return
        rec = {"n": self.n, "t": datetime.now().strftime("%H:%M:%S.%f")[:-3],
               "step": "mark", "note": note}
        if extra:
            rec.update(extra)
        self.records.append(rec)
        try:
            with self.steps_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---------------------------------------------------------------- 收尾
    def captcha_pack(self, page, info: dict | None = None, note: str = "") -> Path:
        """验证码专项取证包：单独存到 captcha\\ 子目录，方便事后判断是哪家、能不能自动过。

        保存内容：
            NN_<类型>.json           检测结果 + iframe/script/sitekey/输入框
            NN_<类型>_evidence.json  原始取证（含 base64 图片源）
            NN_<类型>.png            整页截图
            NN_<类型>.html           页面源码（已打码）
            NN_img1.png ...          验证码图片本体（从 data: base64 解出来的）
        """
        cap_dir = self.dir / "captcha"
        cap_dir.mkdir(parents=True, exist_ok=True)
        n = len(list(cap_dir.glob("*.json"))) // 2 + 1
        info = info or captcha.detect(page)
        kind = (info or {}).get("type", "unknown")
        base = f"{n:02d}_{kind}"

        # 1) 检测结果
        try:
            (cap_dir / f"{base}.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

        # 2) 专项取证
        ev = {}
        try:
            ev = page.evaluate(CAPTCHA_EVIDENCE_JS)
            (cap_dir / f"{base}_evidence.json").write_text(
                json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            ev = {"error": f"{type(exc).__name__}: {str(exc)[:100]}"}

        # 3) 截图 + 源码
        try:
            page.screenshot(path=str(cap_dir / f"{base}.png"), full_page=True)
        except Exception:
            pass
        try:
            (cap_dir / f"{base}.html").write_text(self._mask(page.content()), encoding="utf-8")
        except Exception:
            pass

        # 4) 把 data: base64 的验证码图抠出来单独存（能直接拿去喂 OCR 试）
        saved = 0
        for img in (ev.get("images") or []):
            src = img.get("src", "")
            if not src.startswith("data:image"):
                continue
            m = re.match(r"data:image/(png|jpeg|jpg|gif|webp);base64,(.+)", src, re.S)
            if not m:
                continue
            try:
                raw = base64.b64decode(m.group(2))
            except Exception:
                continue
            if len(raw) < 40:      # 小图标/1px 追踪像素，不是验证码
                continue
            saved += 1
            ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
            (cap_dir / f"{base}_img{saved}.{ext}").write_bytes(raw)

        rec = {
            "n": self.n, "t": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "step": "captcha", "note": note or f"验证码：{kind}",
            "url": (info or {}).get("url", ""), "captcha_type": kind,
            "iframes": len(ev.get("iframes") or []),
            "sitekeys": len(ev.get("sitekeys") or []),
            "img_saved": saved,
        }
        self.records.append(rec)
        try:
            with self.steps_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return cap_dir / base

    def finish(self) -> Path:
        """生成 index.md 时间线，方便直接翻。"""
        lines = [
            f"# 运行复盘 · {self.who}",
            "",
            f"- 目录：`{self.dir}`",
            f"- 记录数：{len(self.records)}",
            f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
            "| # | 时间 | 步骤 | URL | 备注 | 字段 | 按钮 |",
            "|---|---|---|---|---|---|---|",
        ]
        t0 = self.records[0]["ts"] if self.records and "ts" in self.records[0] else 0
        for r in self.records:
            if r.get("step") == "mark":
                continue
            dt = f"+{r['ts'] - t0:.1f}s" if t0 and "ts" in r else ""
            url = (r.get("url") or "")[:70].replace("|", "\\|")
            note = (r.get("note") or "").replace("|", "\\|")
            btn = ", ".join(r.get("buttons", [])[:6]).replace("|", "\\|")
            lines.append(
                f"| {r['n']:02d} | {r['t']}<br>{dt} | `{r['step']}` | {url} | {note} | "
                f"{r.get('n_fields', '')} | {btn} |")
        lines += ["", "## 事件流", ""]
        for r in self.records:
            if r.get("step") == "mark":
                lines.append(f"- {r['t']}　{r.get('note', '')}")
        out = self.dir / "index.md"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out
