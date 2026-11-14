# -*- coding: utf-8 -*-
"""browser.py —— 用真实浏览器渲染一页，并把页面事实落成快照

定位
----
入口站是客户端渲染的 SPA，HTTP 抓到的 HTML 只有一个空壳（``<div id="root">``）。
本模块用 Playwright 起 Chromium（或系统 Edge），等页面渲染完成，然后把
「标题 / 大纲 / 表单字段 / 按钮 / 链接 / 请求行 / 存储键 / 正文 / 截图」一次性取出。

输入输出
--------
    PageSession(run_dir, headless=True).open(url) -> dict
    PageSession.capture(phase) -> dict          # 写盘后返回快照字典
    PageSession.click(target) -> dict           # 点完顺手等页面稳定
    PageSession.close()

    快照字典键：phase / url / title / status / captured_at / headings / buttons /
    links / fields / forms / requests / storage / console / text / html / screenshot

不变量
------
· 只读优先：``capture`` 不点击、不填表；``click`` 是唯一会产生页面状态变化的入口
· 原始快照只落 ``runs/pagekit/``（gitignore 已含 runs/），不外发
· 每次抓取都记录抓取时间与最终 URL，跨页对照时不用猜
· 等待策略固定：``domcontentloaded`` → 等网络静默（有上限）→ 再等 ``settle_ms``

失败路径
--------
· Playwright 未安装 / 浏览器启动失败 → ``BrowserUnavailable``
· 导航超时或 4xx/5xx → 仍尝试抓取当前 DOM，``status`` 如实记录，不抛异常
· 单个选择器解析失败 → 该项为空列表，快照其余部分照常返回
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

EXTRACT_JS = r"""
() => {
  const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
  const sel = (el) => {
    const bits = [];
    if (el.id) bits.push('#' + el.id);
    for (const attr of ['name', 'data-testid', 'data-test', 'aria-label']) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v) bits.push(el.tagName.toLowerCase() + '[' + attr + '="' + v + '"]');
    }
    if (!bits.length) bits.push(el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
      ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
    return bits[0];
  };
  const labelOf = (el) => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const wrap = el.closest('label');
    if (wrap) return txt(wrap);
    if (el.id) {
      const lab = document.querySelector('label[for="' + el.id + '"]');
      if (lab) return txt(lab);
    }
    const parent = el.closest('div, td, li, fieldset');
    if (parent) {
      const first = parent.querySelector('label, legend, .label, [class*=label]');
      if (first && first !== el) return txt(first);
    }
    return el.getAttribute('placeholder') || '';
  };
  // 只看真正的标题语义元素；div 带 "title" 字样的容器大多是隐藏的错误占位，别当大纲
  const headingEls = [...document.querySelectorAll(
    'h1,h2,h3,h4,h5,h6,[role=heading],[class*="MuiTypography-h"]')];
  const seenHeadings = new Set();
  const headings = headingEls
    .map(el => {
      const fromTag = Number((el.tagName || '').replace(/\D/g, '')) || 0;
      const fromClass = Number((String(el.className || '').match(/h([1-6])\b/) || [0, 0])[1]) || 0;
      const level = fromTag || Number(el.getAttribute('aria-level')) || fromClass || 0;
      return {level, text: txt(el), visible: el.getClientRects().length > 0};
    })
    .filter(h => {
      if (!h.text || h.text.length > 80 || !h.visible) return false;
      const key = h.level + ':' + h.text;
      if (seenHeadings.has(key)) return false;
      seenHeadings.add(key);
      return true;
    })
    .map(h => ({level: h.level, text: h.text}))
    .slice(0, 80);
  const buttons = [...document.querySelectorAll('button,[role=button],input[type=submit],input[type=button]')]
    .map(el => ({label: txt(el) || el.value || '', selector: sel(el), disabled: !!el.disabled}))
    .filter(b => b.label);
  const links = [...document.querySelectorAll('a[href]')]
    .map(el => ({label: txt(el), href: el.getAttribute('href')}))
    .filter(l => l.href);
  const fields = [...document.querySelectorAll('input,select,textarea')]
    .map(el => ({
      name: el.getAttribute('name') || el.id || '',
      type: (el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text')),
      label: labelOf(el),
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      maxlength: el.getAttribute('maxlength') || '',
      pattern: el.getAttribute('pattern') || '',
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : [],
      selector: sel(el),
    }));
  const forms = [...document.querySelectorAll('form')]
    .map(el => ({selector: sel(el), action: el.getAttribute('action') || '',
                 method: (el.getAttribute('method') || 'get').toUpperCase(),
                 field_count: el.querySelectorAll('input,select,textarea').length}));
  const storage = {
    local: Object.keys(localStorage),
    session: Object.keys(sessionStorage),
    cookie_names: document.cookie.split(';').map(c => c.split('=')[0].trim()).filter(Boolean),
  };
  return {
    title: document.title,
    url: location.href,
    lang: document.documentElement.lang || '',
    headings, buttons, links, fields, forms, storage,
    text: (document.body ? document.body.innerText : '').trim(),
    html: document.documentElement.outerHTML,
  };
}
"""


class BrowserUnavailable(RuntimeError):
    """Playwright 或浏览器不可用；调用方据此切到纯 HTTP 路径或退出。"""


class PagekitError(RuntimeError):
    """页面操作无法完成（找不到元素、超时等）。"""


@dataclass
class SessionConfig:
    """一次会话的固定参数；默认值按「够稳、够慢」取。"""

    headless: bool = True
    locale: str = "zh-HK"
    width: int = 1366
    height: int = 900
    timeout_ms: int = 45000
    settle_ms: int = 1500
    user_agent: str = DEFAULT_UA
    channel: str = ""


@dataclass
class PageSession:
    """一个浏览器上下文 + 一页；生命周期由调用方管（MCP 工具面里是进程级单例）。"""

    run_dir: Path
    config: SessionConfig = field(default_factory=SessionConfig)
    profile_dir: Optional[Path] = None
    _pw: Any = None
    _browser: Any = None
    _context: Any = None
    page: Any = None
    requests: List[Dict[str, Any]] = field(default_factory=list)
    console: List[Dict[str, str]] = field(default_factory=list)
    last_url: str = ""

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> "PageSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise BrowserUnavailable(
                "playwright 不可用：python -m pip install playwright && python -m playwright install chromium"
            ) from exc

        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._pw = sync_playwright().start()
            launch_kwargs = {"headless": self.config.headless}
            if self.config.channel:
                launch_kwargs["channel"] = self.config.channel
            if self.config.channel == "msedge":
                # 系统 Edge 走 channel，不再传 executable_path
                launch_kwargs.pop("executable_path", None)
            if self.profile_dir:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                self._context = self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    locale=self.config.locale,
                    user_agent=self.config.user_agent,
                    viewport={"width": self.config.width, "height": self.config.height},
                    **launch_kwargs,
                )
                self._browser = None
            else:
                self._browser = self._pw.chromium.launch(**launch_kwargs)
                self._context = self._browser.new_context(
                    locale=self.config.locale,
                    user_agent=self.config.user_agent,
                    viewport={"width": self.config.width, "height": self.config.height},
                )
            self._context.set_default_timeout(self.config.timeout_ms)
            self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self.page.on("response", self._on_response)
            self.page.on("console", self._on_console)
        except Exception as exc:  # pragma: no cover - 环境相关
            raise BrowserUnavailable(f"浏览器启动失败：{type(exc).__name__}: {exc}") from exc
        return self

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = self._browser = self._pw = self.page = None

    def __enter__(self) -> "PageSession":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ---------------------------------------------------------------- 事件
    def _on_response(self, response: Any) -> None:
        try:
            self.requests.append({
                "method": response.request.method,
                "url": response.url,
                "status": response.status,
                "kind": response.request.resource_type,
            })
            del self.requests[:-160]
        except Exception:
            pass

    def _on_console(self, message: Any) -> None:
        try:
            if message.type in ("error", "warning"):
                self.console.append({"level": message.type, "text": message.text})
                del self.console[:-40]
        except Exception:
            pass

    # ---------------------------------------------------------------- 动作
    def open(self, url: str, wait: str = "domcontentloaded") -> Dict[str, Any]:
        """导航到 url；返回 {status, url, elapsed_ms}，超时不抛异常。"""
        started = time.time()
        status: Any = ""
        try:
            response = self.page.goto(url, wait_until=wait, timeout=self.config.timeout_ms)
            status = response.status if response else ""
        except Exception as exc:
            status = f"{type(exc).__name__}"
        self.settle()
        self.last_url = getattr(self.page, "url", url)
        return {"status": status, "url": self.last_url,
                "elapsed_ms": int((time.time() - started) * 1000)}

    def settle(self) -> None:
        """等网络静默（有上限）再固定等一段，避免抓到渲染中的中间态。"""
        try:
            self.page.wait_for_load_state("networkidle", timeout=self.config.timeout_ms)
        except Exception:
            pass
        if self.config.settle_ms:
            try:
                self.page.wait_for_timeout(self.config.settle_ms)
            except Exception:
                pass

    def click(self, target: str) -> Dict[str, Any]:
        """按文本或 CSS 选择器点一个元素；返回 {target, clicked, url}。

        选择器歧义用前缀消掉：``css=input[name=x]`` 走 CSS，``text=预约`` 走可见文本；
        不写前缀时按「像选择器就当成选择器」处理（``#`` / ``.`` / ``[`` / ``//`` 开头）。
        """
        locators = []
        if target.startswith(("css=", "css:")):
            locators.append(self.page.locator(target[4:]))
        elif target.startswith(("text=", "text:")):
            name = target[5:]
            locators.extend([self.page.get_by_role("button", name=name, exact=True),
                             self.page.get_by_text(name, exact=True)])
        elif target.startswith(("#", ".", "[", "//")) or ">>" in target:
            locators.append(self.page.locator(target))
        else:
            locators.extend([
                self.page.get_by_role("button", name=target, exact=True),
                self.page.get_by_role("button", name=target),
                self.page.get_by_role("link", name=target, exact=True),
                self.page.get_by_text(target, exact=True),
            ])
        for locator in locators:
            try:
                if locator.count() == 0:
                    continue
                locator.first.click(timeout=self.config.timeout_ms)
                self.settle()
                return {"target": target, "clicked": True, "url": self.page.url}
            except Exception:
                continue
        raise PagekitError(f"点不到：{target}")

    def fill(self, target: str, value: str) -> Dict[str, Any]:
        """往一个字段里填值；只在操作者显式要求时用，快照流程不会调用。"""
        try:
            locator = (self.page.locator(target) if target.startswith(("#", ".", "["))
                       else self.page.get_by_label(target).first)
            locator.first.fill(value, timeout=self.config.timeout_ms)
            return {"target": target, "filled": True}
        except Exception as exc:
            raise PagekitError(f"填不了：{target}（{type(exc).__name__}）") from exc

    def text(self) -> str:
        try:
            return str(self.page.inner_text("body"))
        except Exception:
            return ""

    # ---------------------------------------------------------------- 抓取
    def capture(self, phase: str, status: Any = "") -> Dict[str, Any]:
        """取一页的结构事实，写 ``<phase>.snapshot.json`` / ``.html`` / ``.png``。"""
        try:
            facts = self.page.evaluate(EXTRACT_JS)
        except Exception as exc:
            raise PagekitError(f"页面抽取失败：{type(exc).__name__}: {exc}") from exc

        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        safe_phase = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in phase) or "page"
        png = self.run_dir / f"{safe_phase}.png"
        try:
            self.page.screenshot(path=str(png), full_page=True)
        except Exception:
            png = Path("")

        snapshot: Dict[str, Any] = {
            "phase": phase,
            "url": facts.get("url") or self.page.url,
            "title": facts.get("title") or "",
            "lang": facts.get("lang") or "",
            "status": status,
            "captured_at": stamp,
            "headings": facts.get("headings") or [],
            "buttons": facts.get("buttons") or [],
            "links": facts.get("links") or [],
            "fields": facts.get("fields") or [],
            "forms": facts.get("forms") or [],
            "requests": [dict(item) for item in self.requests],
            "storage": facts.get("storage") or {},
            "console": [dict(item) for item in self.console],
            "text": facts.get("text") or "",
            "screenshot": str(png),
        }
        html_path = self.run_dir / f"{safe_phase}.html"
        try:
            html_path.write_text(facts.get("html") or "", encoding="utf-8")
            snapshot["html"] = str(html_path)
        except OSError:
            snapshot["html"] = ""
        (self.run_dir / f"{safe_phase}.snapshot.json").write_text(
            json.dumps({k: v for k, v in snapshot.items() if k != "html"},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return snapshot


def default_run_dir(root: Optional[Path] = None) -> Path:
    """``booking-lab/runs/pagekit/<时间戳>``；runs/ 已在 lint 与 git 的忽略名单里。"""
    base = Path(root) if root else Path(__file__).resolve().parents[1] / "runs" / "pagekit"
    return base / datetime.now().strftime("%Y%m%d-%H%M%S")
