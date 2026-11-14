# -*- coding: utf-8 -*-
"""TDABS 免试签发（内地驾照）预约抢位助手
================================================
用途：在运输署网上预约系统开放名额的瞬间，把「勾同意 -> 预约 -> 不使用智方便 ->
中国内地 -> 填资料 -> 选最早日期时段 -> 递交」这一串动作压缩到几十秒内完成，
人只在「收电邮 OTP」这一步介入。

用法：
  1) 复制 config.example.json 为 config.json，填好个人资料与图片路径
  2) 演练（不提交，只走到第2步/第3步并导出页面结构）：
        python grab.py --probe
  3) 正式抢位（开 N 条独立排队的浏览器）：
        python grab.py --workers 3
  4) OTP 到了但脚本在等：把验证码写进 otp.txt（一行），脚本自动填入

注意：本脚本只在本地驱动浏览器，不使用任何绕过设备指纹的手段；
排队位置由官方随机分配，多 worker 只是增加被抽中的会话数。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import human as H
from recorder import Recorder
import captcha
import notifier as N
import waf

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
PROFILES = BASE / "profiles"
ENTRY = "https://www.gov.hk/sc/apps/tddiabs.htm"
# 被弹回入口页后 SPA 常停在英文版（?lang=en）；填表关键词是中文，所以重入时先把它拉回简体。
ENTRY_SC = "https://abs3.td.gov.hk/?lang=sc"

_STOP = threading.Event()
_PRINT_LOCK = threading.Lock()

# 官方网关/维护页的特征。出现这些说明是站点自身故障，不是我们的操作问题，
# 不能傻等元素出现（否则会像卡死一样挂几十分钟）。
BAD_GATEWAY_MARKERS = (
    "502 Bad Gateway", "502 bad gateway", "503 Service", "503 Service Temporarily",
    "504 Gateway", "Service Temporarily Unavailable", "Application Error",
    "系统正在维修", "系統正在維修", "系统维护中", "暫時無法", "暂时无法",
)

# 华为云 WAF 的「按 IP 拦」页（实测 2026-11-09：出口为 Cloudflare/WARP 网段时触发）。
# 触发后重试没有意义——必须先换回真实家宽/移动网络。
RESTRICT_MARKERS = (
    "access has been restricted", "access has been blocked",
    "your access has been restricted", "restricted access",
    "已被限制存取", "存取已被限制",
)


def _hurry() -> bool:
    """放号窗口内一切停顿压到最短；其余时段按真人节奏走。"""
    return H.phase_now() == "rush"


# --------------------------------------------------------------------------- 基础
def log(who: str, msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} [{who}] {msg}"
    with _PRINT_LOCK:
        print(line, flush=True)
        try:
            with (RUNS / "grab.log").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def beep(times: int = 3) -> None:
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(880, 220)
            time.sleep(0.12)
    except Exception:
        print("\a", end="", flush=True)


def load_config() -> dict:
    path = BASE / "config.json"
    if not path.exists():
        sys.exit("缺少 config.json（先复制 config.example.json 并填写）")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- DOM 工具
DUMP_JS = r"""
() => {
  const out = {url: location.href, fields: [], buttons: [], days: [], slots: [], texts: []};
  document.querySelectorAll('input,select,textarea').forEach(el => {
    const lab = el.closest('label')?.innerText
      || document.querySelector(`label[for="${el.id}"]`)?.innerText || '';
    const holder = el.closest('.MuiFormControl-root') || el.closest('tr') || el.parentElement;
    out.fields.push({tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '',
      id: el.id || '', value: el.value || '', required: !!el.required, accept: el.accept || '',
      disabled: !!el.disabled, label: (lab || '').trim().replace(/\s+/g, ' ').slice(0, 90),
      ctx: (holder ? holder.innerText : '').trim().replace(/\s+/g, ' ').slice(0, 150)});
  });
  document.querySelectorAll('button,[role="button"]').forEach(b => {
    out.buttons.push({text: (b.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40),
      disabled: !!b.disabled, cls: (b.className || '').toString().slice(0, 80),
      aria: b.getAttribute('aria-label') || ''});
  });
  document.querySelectorAll('button.MuiPickersDay-root,[role="gridcell"] button,button[data-timestamp]').forEach(d => {
    out.days.push({text: (d.innerText || '').trim(), aria: d.getAttribute('aria-label') || '',
      disabled: !!d.disabled, cls: (d.className || '').toString().slice(0, 120),
      ts: d.getAttribute('data-timestamp') || ''});
  });
  document.querySelectorAll('p,span,h2,h3,h4,li,div.MuiFormHelperText-root').forEach(el => {
    const t = el.innerText?.trim();
    if (t && t.length < 160 && el.children.length === 0) out.texts.push(t);
  });
  out.texts = [...new Set(out.texts)].slice(0, 200);
  return out;
}
"""


def dump(page, who: str, tag: str) -> dict:
    data = page.evaluate(DUMP_JS)
    out = RUNS / f"{who}_{tag}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        (RUNS / f"{who}_{tag}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(RUNS / f"{who}_{tag}.png"), full_page=True)
    except Exception:
        pass
    return data


def click_text(page, labels, timeout_each=4000) -> bool:
    """按可见文本点按钮/链接（模糊包含）；也支持老式 input[type=button/submit] 的 value。
    点击走 human_click：先移鼠标、悬停、再按下。"""
    if isinstance(labels, str):
        labels = [labels]
    hurry = _hurry()
    for lab in labels:
        for sel in ("button", "a", "[role='button']", "input[type='button']", "input[type='submit']"):
            try:
                els = page.query_selector_all(sel)
            except Exception:
                return False
            for el in els:
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or el.get_attribute("value") or "").strip()
                    if txt and (txt == lab or lab in txt) and el.is_enabled():
                        ok = H.human_click(page, el, timeout=timeout_each, hurry=hurry,
                                           logger=lambda m: log("ui", m))
                        if not ok:
                            return False
                        if not hurry:
                            H.dwell(0.25, 1.1, "点完看一眼", mode=0.5, logger=lambda m: log("ui", m))
                        return True
                except Exception:
                    continue
    return False


def wait_page(page, cond, who: str, limit=600, tag="", health=None):
    """轮询直到 cond(page) 为真，期间打印状态。
    health: 可选的页面健康函数，返回 'gateway_error' 时立即放弃（站点故障，等没意义）。"""
    t0 = time.time()
    last = ""
    tick = 0
    while time.time() - t0 < limit:
        if _STOP.is_set():
            return False
        try:
            if cond(page):
                return True
            cur = page.url
            key = re.sub(r"[?&](c|e|ver|kupver|cver|man|cid|t)=[^&]*", "", cur)[:80]
            if key != last:
                log(who, f"…{int(time.time() - t0)}s {key}")
                last = key
            tick += 1
            if health and tick % 4 == 0:
                hv = health(page)
                if hv in ("gateway_error", "restricted"):
                    log(who, f"{tag}: 页面被判 {'IP 限制(restricted)' if hv == 'restricted' else '5xx/维护'}，提前放弃等待")
                    return False
            # 心跳：URL 不变时也要让人知道它还活着，否则看起来就是卡死
            if tick % 10 == 0:
                try:
                    head = page.inner_text("body")[:60].replace("\n", " ").strip()
                except Exception:
                    head = ""
                log(who, f"  ⏳ 仍在等待 {int(time.time() - t0)}s（{tag}）{key}｜{head}")
        except Exception:
            pass
        page.wait_for_timeout(1500)
    log(who, f"{tag} 等待超时({limit}s) url={page.url}")
    return False


def fill_by_label(page, mapping: dict, who: str) -> int:
    """按 label/上下文关键词填值；逐字输入（不是一次性注入）。返回填充数量。

    · 关键词同时支持中文（简/繁）与英文——被弹回后界面会变成英文。
    · <select> 用 select_option（human_type 对下拉无效）。
    """
    filled = 0
    hurry = _hurry()
    data = page.evaluate(DUMP_JS)
    for idx, f in enumerate(data["fields"]):
        if f["tag"] not in ("input", "textarea", "select") or f["type"] in ("hidden", "file", "checkbox", "radio"):
            continue
        hay = f"{f['label']} {f['ctx']} {f['name']} {f['id']}".lower()
        for kw, val in mapping.items():
            if kw.lower() in hay and val:
                sel = f"input[name='{f['name']}'], #{f['id']}" if f["name"] or f["id"] else None
                if f["tag"] == "select":
                    sel = f"select[name='{f['name']}'], #{f['id']}" if f["name"] or f["id"] else None
                try:
                    el = page.query_selector(sel) if sel else None
                    if el is None:
                        continue
                    H.dwell(0.3, 1.2, "看这一栏", mode=0.6, hurry=hurry,
                            logger=lambda m: log(who, m))
                    if f["tag"] == "select":
                        ok = False
                        for how in ("label", "value"):
                            try:
                                el.select_option(**{how: str(val)})
                                ok = True
                                break
                            except Exception:
                                continue
                        if not ok:
                            log(who, f"下拉没有「{val}」这一项（{f['label'][:20]}），跳过")
                            break
                    else:
                        H.human_type(el, str(val), hurry=hurry, typo_rate=0.0,
                                     logger=lambda m: log(who, m))
                    filled += 1
                    log(who, f"填入 {kw} -> {val}（{f['label'][:20] or f['name'] or f['id']}）")
                except Exception as exc:
                    log(who, f"填入 {kw} 失败: {exc}")
                break
    return filled


def upload_files(page, paths: list, who: str) -> int:
    """按顺序给 file input 塞文件。"""
    if not paths:
        return 0
    inputs = page.query_selector_all("input[type='file']")
    n = 0
    for i, el in enumerate(inputs):
        if i >= len(paths) or not paths[i]:
            continue
        p = Path(paths[i])
        if not p.is_absolute():
            p = BASE / p
        if not p.exists():
            log(who, f"文件不存在: {p}")
            continue
        try:
            el.set_input_files(str(p))
            n += 1
            log(who, f"上传 {p.name} -> file#{i}")
        except Exception as exc:
            log(who, f"上传失败 file#{i}: {exc}")
    return n


def pick_earliest_day(page, who: str) -> str | None:
    """点最早可选日期，返回其 aria-label。"""
    data = page.evaluate(DUMP_JS)
    cands = [d for d in data["days"] if not d["disabled"] and d["text"].strip().isdigit()]
    if not cands:
        return None
    def key(d):
        return d.get("aria") or ""
    cands.sort(key=key)
    for d in cands[:6]:
        try:
            loc = page.query_selector(f"button.MuiPickersDay-root[aria-label='{d['aria']}']")
            if loc and loc.is_enabled():
                H.human_click(page, loc, hurry=True, logger=lambda m: log(who, m))
                page.wait_for_timeout(1200)
                log(who, f"选日期 {d['aria'] or d['text']}")
                return d["aria"] or d["text"]
        except Exception:
            continue
    return None


def pick_earliest_slot(page, who: str) -> str | None:
    """选最早可选时段（时间按钮/单选项）。"""
    data = page.evaluate(DUMP_JS)
    pat = re.compile(r"^\d{1,2}[:：]\d{2}")
    slots = [b for b in data["buttons"] if pat.match(b["text"]) and not b["disabled"]]
    if slots:
        try:
            loc = page.get_by_text(slots[0]["text"], exact=False).first
            H.human_click(page, loc, hurry=True, logger=lambda m: log(who, m))
            log(who, f"选时段 {slots[0]['text']}")
            return slots[0]["text"]
        except Exception:
            pass
    radios = page.query_selector_all("input[type='radio']")
    for r in radios:
        try:
            if r.is_enabled() and not r.is_checked():
                r.check(force=True)
                log(who, "选时段（radio）")
                return "radio"
        except Exception:
            continue
    return None


def read_otp() -> str | None:
    p = BASE / "otp.txt"
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"\d{4,8}", txt):
        return txt
    return None


# --------------------------------------------------------------------------- worker
class Worker(threading.Thread):
    def __init__(self, idx: int, cfg: dict, probe: bool, run_dir: Path | None = None):
        super().__init__(daemon=True)
        self.idx = idx
        self.cfg = cfg
        self.probe = probe
        self.who = f"w{idx}"
        self.result: dict | None = None
        self._queue_shot_done = False
        ap = cfg.get("applicant", {})
        self.rec = None
        if run_dir is not None:
            self.rec = Recorder(
                Path(run_dir), self.who,
                redact=[ap.get("doc_no"), ap.get("dl_no"), ap.get("email"),
                        ap.get("phone"), ap.get("name_cn")],
                enabled=bool(cfg.get("record", True)),
            )

    # --- 录制 ---
    def shot(self, page, step: str, note: str = "") -> None:
        """关键时刻留档：截图 + 源码 + DOM，供事后优化脚本用。"""
        if self.rec is None:
            return
        try:
            r = self.rec.shot(page, step, note)
            if r:
                log(self.who, f"📸 {r['n']:02d} {step}　{note}")
        except Exception as exc:
            log(self.who, f"录制失败 {step}: {type(exc).__name__} {str(exc)[:60]}")

    def mark(self, note: str, extra: dict | None = None) -> None:
        if self.rec is not None:
            try:
                self.rec.mark(note, extra)
            except Exception:
                pass

    def notify_captcha(self, kind: str, waited: int = 0) -> None:
        if kind in captcha.NEED_HUMAN:
            N.captcha_need_human(kind, waited)

    def captcha_pack(self, page, info: dict, note: str = "") -> None:
        """验证码专项取证（截图 + 源码 + iframe/sitekey/图片本体），存到 captcha\\ 子目录。"""
        if self.rec is None:
            return
        try:
            p = self.rec.captcha_pack(page, info, note)
            log(self.who, f"🧩 验证码取证已存：{p}")
        except Exception as exc:
            log(self.who, f"验证码取证失败：{type(exc).__name__} {str(exc)[:60]}")

    # --- 页面判定 ---
    @staticmethod
    def on_queue(page) -> bool:
        return "abs4.td.gov.hk" in page.url

    @staticmethod
    def on_entry(page) -> bool:
        return page.query_selector('input[name="conditionsCheck"]') is not None

    @staticmethod
    def on_auth(page) -> bool:
        return "SelectAuth" in page.url or "身份验证方法" in page.content()[:60000]

    @staticmethod
    def on_country(page) -> bool:
        return "SelectCountry" in page.url

    def body(self, page) -> str:
        try:
            return page.inner_text("body")
        except Exception:
            return ""

    @staticmethod
    def health(page) -> str:
        """'ok' / 'gateway_error'（官方 5xx / 维护页）/ 'restricted'（WAF 按 IP 拦）。"""
        try:
            txt = page.inner_text("body")[:6000]
        except Exception:
            try:
                txt = page.content()[:20000]
            except Exception:
                return "unknown"
        low = txt.lower()
        if any(m.lower() in low for m in RESTRICT_MARKERS):
            return "restricted"
        if any(m.lower() in low for m in BAD_GATEWAY_MARKERS):
            return "gateway_error"
        return "ok"

    # --- 主流程 ---
    def run(self):
        prof = PROFILES / f"w{self.idx}"
        prof.mkdir(parents=True, exist_ok=True)
        # 默认跟随本机（上海）：locale/tz 必须与出口 IP 自洽，
        # 否则"IP 在大陆、时区在香港"本身就是一条异常特征。
        loc = self.cfg.get("locale", "zh-CN")
        tz = self.cfg.get("timezone", "Asia/Shanghai")
        ctx = None
        attach = (self.cfg.get("attach") or "").strip()
        try:
            with sync_playwright() as p:
                if attach:
                    # 接管一个已经用普通方式启动的真 Chrome（见 launch_chrome.ps1）
                    browser = p.chromium.connect_over_cdp(attach)
                    if self.idx == 1 and browser.contexts and browser.contexts[0].pages:
                        # 第 1 条队列用你眼前那个窗口，方便随时接手
                        ctx = browser.contexts[0]
                        page = ctx.pages[0]
                    else:
                        # 其余队列各开独立 context：cookie 不共享，排队位互不踢
                        ctx = browser.new_context(locale=loc, timezone_id=tz, no_viewport=True)
                        page = ctx.new_page()
                    log(self.who, f"接管已开的真 Chrome：{attach}")
                else:
                    # 不指定 viewport：让浏览器用真实窗口尺寸与系统缩放（DPR），
                    # 否则 screen/DPR 会与真人环境差一截
                    kw = dict(headless=self.cfg.get("headless", False), locale=loc,
                              timezone_id=tz, no_viewport=True, slow_mo=0,
                              permissions=[],
                              args=H.LAUNCH_ARGS)
                    ch = (self.cfg.get("channel") or "").strip()
                    if ch:
                        try:
                            ctx = p.chromium.launch_persistent_context(str(prof), channel=ch, **kw)
                            log(self.who, f"内核：本机已装的 {ch}")
                        except Exception as exc:
                            log(self.who, f"{ch} 启动失败({exc.__class__.__name__})，回退 chromium 内核")
                            ctx = p.chromium.launch_persistent_context(str(prof), **kw)
                    else:
                        ctx = p.chromium.launch_persistent_context(str(prof), **kw)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                H.apply_mask(page)          # 抹掉 navigator.webdriver 之类
                page.set_default_timeout(30000)
                log(self.who, f"时段={H.phase_now()} locale={loc} tz={tz}")
                try:
                    self.drive(page)
                finally:
                    if not self.probe:
                        page.wait_for_timeout(2000)
                    if ctx is not None and not attach:
                        try:
                            ctx.close()
                        except Exception:
                            pass
        except Exception:
            log(self.who, "异常:\n" + traceback.format_exc(limit=3))
        finally:
            log(self.who, "结束")

    # --- 被弹回入口页后的恢复 ---
    def _enter_from_home(self, page) -> bool:
        """从 abs3 首页重新走一遍：勾同意 → 预约 → 不用智方便 → 中国内地。

        被弹回时 SPA 常常落在英文界面（?lang=en），所以所有按钮都用中英双语候选匹配。
        返回是否已重新到达「选国家/地方」之后。
        """
        if "lang=en" in page.url or "lang=tc" in page.url:
            log(self.who, "重入：界面是英文/繁体，先切回简体")
            try:
                page.goto(ENTRY_SC, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
        try:
            page.wait_for_selector('input[name="conditionsCheck"]', timeout=40000)
        except Exception:
            log(self.who, "重入：首页没有渲染出勾选框")
            return False
        cb = page.query_selector('input[name="conditionsCheck"]')
        if cb:
            H.human_click(page, cb, hurry=_hurry(), logger=lambda m: log(self.who, m))
            page.wait_for_timeout(400)
        H.dwell(0.6, 2.0, "重入：读完条款再点预约", mode=1.2,
                logger=lambda m: log(self.who, m), hurry=_hurry())
        if not click_text(page, ["预约", "預約", "MAKE AN APPOINTMENT"]):
            log(self.who, "重入：没找到「预约」按钮")
            return False
        wait_page(page, lambda pg: self.on_auth(pg) or self.on_queue(pg) or self.on_country(pg),
                  self.who, limit=150, tag="认证页(重入)", health=self.health)
        if self.on_auth(page):
            r = page.query_selector('input[type="radio"][value="withOutIas"]')
            if r:
                H.human_click(page, r, hurry=_hurry(), logger=lambda m: log(self.who, m))
                page.wait_for_timeout(300)
            click_text(page, ["继续", "繼續", "CONTINUE", "Continue"])
            log(self.who, "重入：再次选择「不使用智方便」")
        if not wait_page(page, lambda pg: self.on_country(pg)
                         and len(pg.query_selector_all('input[type="radio"]')) > 0,
                         self.who, limit=900, tag="选国家(重入)", health=self.health):
            log(self.who, "重入：没能等到选国家页")
            return False
        picked = False
        for r in page.query_selector_all('input[type="radio"]'):
            try:
                lab = (r.evaluate(
                    "e => (e.closest('label')?.innerText || e.parentElement?.innerText || '')") or "").strip()
            except Exception:
                continue
            if "内地" in lab or "Mainland" in lab:
                try:
                    H.human_click(page, r, hurry=_hurry(), logger=lambda m: log(self.who, m))
                except Exception:
                    try:
                        r.check(force=True)
                    except Exception:
                        pass
                page.wait_for_timeout(400)
                log(self.who, f"重入：选国家/地方 {lab[:30]}")
                picked = True
                break
        if not picked:
            log(self.who, "重入：没匹配到「中国内地」单选项")
            return False
        page.wait_for_timeout(1200)
        if "SelectCountry" in page.url:
            click_text(page, ["继续", "繼續", "CONTINUE", "Continue"])
        return True

    def drive(self, page):
        cfg = self.cfg

        # 1) 入口页 -> 勾同意 -> 预约
        # ★ 2026-11-09 决定性实测：
        #   用 CDP 的 page.goto() 进站 → 收到 412 + JS 挑战，自动化环境过不去；
        #   但让 **Chrome 自己导航**（启动参数直接带 URL / 地址栏）→ 挑战自动通过，
        #   页面正常（body 2000+ 字、勾选框可见）。
        #   所以只要当前页已经是入口页，就绝不 goto，直接从勾选开始。
        got_entry = False
        try:
            if page.query_selector('input[name="conditionsCheck"]'):
                log(self.who, f"当前页已是入口页，跳过 goto（避免触发 WAF 挑战）{page.url[:70]}")
                got_entry = True
        except Exception:
            pass

        MAX_TRY, GAP = 2, 60
        for attempt in range(1, 1 if got_entry else MAX_TRY + 1):
            if _STOP.is_set():
                return
            log(self.who, f"打开入口页（第 {attempt}/{MAX_TRY} 次）")
            try:
                resp = page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
                status = resp.status if resp is not None else None
                try:
                    html = page.content()[:6000]
                except Exception:
                    html = ""
                if waf.is_challenge(status, html):
                    log(self.who, f"✗ 被 WAF 挑战页挡住（HTTP {status}），停止自动请求")
                    log(self.who, "  解法：用 --url 让 Chrome 自己导航（launch_chrome.ps1 -Url ...），"
                                  "或人工过验证后 python resume.py --attach ...")
                    self.shot(page, "waf_challenge", f"WAF 挑战页 HTTP {status}")
                    self.result = {"stage": "waf_challenge", "status": status, "url": page.url}
                    N.waf_blocked(f"{self.who} · HTTP {status}")
                    _STOP.set()
                    return
                page.wait_for_selector('input[name="conditionsCheck"]', timeout=70000)
                got_entry = True
                break
            except Exception:
                h = self.health(page)
                if h == "restricted":
                    log(self.who, "✗ 出口 IP 被官方 WAF 限制（restricted access），重试没有意义")
                    self.result = {"stage": "ip_restricted", "url": page.url}
                    N.failed("出口 IP 被官方 WAF 限制：关掉 VPN/代理/WARP 再跑")
                    _STOP.set()
                    return
                wait = GAP
                log(self.who, f"入口页异常（{h}）url={page.url[:80]}，{wait}s 后重试")
                if _STOP.wait(wait):
                    return
        if not got_entry:
            log(self.who, "入口页打不开：站点故障或 WAF 挑战未通过，转人工")
            self.result = {"stage": "entry_unreachable", "url": page.url}
            N.failed("入口页打不开：用 launch_chrome.ps1 -Url 让 Chrome 自己导航，再 --attach")
            return
        H.look_around(page, logger=lambda m: log(self.who, m), hurry=_hurry())
        self.shot(page, "entry", "入口页已加载（勾选框可见）")
        cb = page.query_selector('input[name="conditionsCheck"]')
        if cb:
            H.human_click(page, cb, hurry=_hurry(), logger=lambda m: log(self.who, m))
            page.wait_for_timeout(300)
            log(self.who, "已同意条款")
            self.shot(page, "agreed", "已勾选同意条款")
        H.dwell(0.8, 3.0, "读完同意条款再点预约", mode=1.6,
                logger=lambda m: log(self.who, m), hurry=_hurry())
        log(self.who, "点「预约」")
        click_text(page, ["预约", "預約", "MAKE AN APPOINTMENT"])

        # 2) 认证方式：不使用智方便
        wait_page(page, lambda pg: self.on_auth(pg) or self.on_queue(pg) or self.on_country(pg),
                  self.who, limit=120, tag="认证页", health=self.health)
        if self.on_auth(page):
            dump(page, self.who, "step1_auth")
            self.shot(page, "auth", "第1步 认证页（两种身份验证方式）")
            r = page.query_selector('input[type="radio"][value="withOutIas"]')
            if r:
                H.human_click(page, r, hurry=_hurry(), logger=lambda m: log(self.who, m))
                page.wait_for_timeout(200)
                self.shot(page, "auth_chosen", "已选「不使用智方便」")
                H.dwell(0.4, 1.6, "看清认证方式再继续", mode=0.8,
                        logger=lambda m: log(self.who, m), hurry=_hurry())
                click_text(page, ["继续", "繼續", "CONTINUE", "Continue"])
                log(self.who, "选择：不使用智方便")

        # 3) 排队 -> 选签发国家/地方
        def country_ready(pg) -> bool:
            try:
                if self.on_queue(pg) and not self._queue_shot_done:
                    self._queue_shot_done = True
                    self.shot(pg, "queue", "排队页（abs4，随机分位中）")
                    log(self.who, "🎫 已拿到排队位")
                    N.queued(f"{self.who} · {pg.url[:80]}")
                if not self.on_country(pg):
                    return False
                return len(pg.query_selector_all('input[type="radio"]')) > 0
            except Exception:
                return False

        wait_page(page, country_ready, self.who, limit=1800, tag="排队/选国家", health=self.health)
        if not country_ready(page):
            dump(page, self.who, "step2_country_missing")
            self.shot(page, "country_missing", "没等到国家选项")
            log(self.who, f"没等到国家/地方选项，停在此页 url={page.url[:110]}")
            self.result = {"stage": "country_not_rendered", "url": page.url}
            return
        dump(page, self.who, "step2_country")
        self.shot(page, "country", "选签发国家/地方")
        target = "内地" if cfg.get("country", "mainland") == "mainland" else "以外"
        radios = page.query_selector_all('input[type="radio"]')
        labels = []
        for r in radios:
            lab = (r.evaluate("e => (e.closest('label')?.innerText || e.parentElement?.innerText || '')") or "").strip()
            labels.append(lab.replace("\n", " ")[:30])
        log(self.who, f"国家/地方选项: {labels}")
        picked = False
        for r, lab in zip(radios, labels):
            if target in lab or (target == "内地" and "Mainland" in lab):
                try:
                    H.human_click(page, r, hurry=_hurry(), logger=lambda m: log(self.who, m))
                except Exception:
                    try:
                        r.check(force=True)
                    except Exception:
                        pass
                page.wait_for_timeout(300)
                log(self.who, f"选国家/地方：{lab}")
                self.shot(page, "country_mainland", f"已选：{lab}")
                picked = True
                break
        if not picked:
            log(self.who, "未匹配到国家/地方单选项，停在此页")
            self.result = {"stage": "country_unknown", "labels": labels}
            _STOP.set()
            return
        # 选中后可能自动导航到下一跳，也可能需要点「继续」
        page.wait_for_timeout(1200)
        try:
            if "SelectCountry" in page.url:
                click_text(page, ["继续", "繼續", "CONTINUE", "Continue"])
        except Exception:
            pass

        # 等真正进入「输入资料 / 配额」步（老式 JSP 页：出现 continueBtn1 或错误提示）
        def ready(pg):
            try:
                if self.on_queue(pg):
                    return False
                if pg.query_selector("#continueBtn1, input[value='继续'], button:has-text('继续')"
                                     ", input[value='CONTINUE'], button:has-text('CONTINUE')"):
                    return True
                body = pg.inner_text("body")
                low = body.lower()
                return ("没有可供预约的时段" in body or "输入资料" in body
                        or "no available" in low or "appointment information" in low)
            except Exception:
                return False

        # 实测：从选国家页点「继续」后，会被弹回 abs3 首页（英文界面）。
        # 所以这里不能只等 ready——每秒同时判「是不是被弹回入口」，弹回就立刻重走，
        # 否则会像老版本一样在入口页干等满 600~1800 秒。
        def wait_admit(limit: int, tag: str) -> str:
            """返回 ready / entry / gateway / stop / timeout。"""
            t0 = time.time()
            tick = 0
            while time.time() - t0 < limit:
                if _STOP.is_set():
                    return "stop"
                try:
                    if ready(page):
                        return "ready"
                    if self.on_entry(page):
                        return "entry"
                    tick += 1
                    if tick % 4 == 0:
                        h = self.health(page)
                        if h == "restricted":
                            log(self.who, f"{tag}: 出口 IP 被官方 WAF 限制（restricted access）")
                            return "restricted"
                        if h == "gateway_error":
                            log(self.who, f"{tag}: 官方返回 5xx/维护页，提前放弃等待")
                            return "gateway"
                except Exception:
                    pass
                page.wait_for_timeout(1500)
            return "timeout"

        entered = False
        for bounce in range(1, 4):
            if _STOP.is_set():
                return
            # 16:30 进池 → 17:00 才随机洗牌，排队可能长达 40 分钟以上，
            # 所以这里的等待上限必须覆盖 16:30–17:20 这段（原来是 900 秒，会提前放弃）。
            state = wait_admit(2400, f"第二次排队/资料页#{bounce}")
            log(self.who, f"资料页判定：{state}")
            if state == "ready":
                entered = True
                break
            if state == "stop":
                return
            if state == "restricted":
                log(self.who, "✗ 出口 IP 被官方 WAF 限制：关掉 VPN/代理/WARP，换家宽或手机 5G 再跑")
                self.shot(page, "ip_restricted", "官方 WAF 按 IP 拦截")
                self.result = {"stage": "ip_restricted", "url": page.url}
                N.failed("出口 IP 被官方 WAF 限制（restricted access）：关掉 VPN/代理/WARP 再跑")
                _STOP.set()
                return
            if state == "entry":
                log(self.who, f"⚠ 第 {bounce} 次被弹回入口页（{page.url[:70]}），自动重走")
                self.shot(page, f"bounce_{bounce}", "被弹回入口页，准备重走")
                N.bounced(f"{self.who} · 第 {bounce} 次弹回")
                if not self._enter_from_home(page):
                    h2 = self.health(page)
                    if h2 == "restricted":
                        log(self.who, "✗ 重入时撞上官方 WAF 的 IP 限制页：换家宽/手机网络再跑")
                        self.shot(page, "ip_restricted", "官方 WAF 按 IP 拦截")
                        self.result = {"stage": "ip_restricted", "url": page.url}
                        N.ip_restricted(f"{self.who} · {page.url[:80]}")
                        _STOP.set()
                        return
                    log(self.who, f"自动重走失败（{h2}），停在此页")
                    self.result = {"stage": "reenter_failed", "url": page.url}
                    return
                continue
            log(self.who, f"未进入资料页（{state}），停在此页 url={page.url[:80]}")
            self.result = {"stage": f"not_admitted_{state}", "url": page.url}
            return
        if not entered:
            log(self.who, "连续被弹回，放弃本轮（多为出口 IP / 会话被判异常）")
            self.shot(page, "bounce_final", "多次被弹回入口页")
            self.result = {"stage": "bounced", "url": page.url}
            N.failed("多次被弹回入口页（出口 IP 或会话被判异常）")
            return

        # 4) 第2步：输入资料
        page.wait_for_timeout(1500)
        d = dump(page, self.who, "step3_fields")
        txt = self.body(page)
        log(self.who, f"第2步字段数={len(d['fields'])} 按钮={[b['text'] for b in d['buttons'] if b['text']][:8]}")
        no_quota = "没有可供预约的时段" in txt or "no available" in txt.lower()
        if no_quota:
            log(self.who, "⚠ 系统提示：该服务现时没有可预约时段")
        else:
            log(self.who, "✅ 已放行：进入第2步填写页（20 分钟倒计时开始）")
            N.admitted(f"{self.who} · {page.url[:80]}")
        self.shot(page, "step2_blank", "第2步「输入资料」页面（填表前）")
        # 顺手抓一份各办事处配额概览（只读，弹新窗口；演练模式跳过，避免卡住）
        if not self.probe and cfg.get("snapshot_quota", True):
            try:
                with page.context.expect_page(timeout=6000) as pinfo:
                    page.click("#onOfficeStatusBtn", timeout=6000, no_wait_after=True)
                pop = pinfo.value
                pop.wait_for_load_state("domcontentloaded", timeout=20000)
                pop.wait_for_timeout(1500)
                qtxt = pop.inner_text("body")
                (RUNS / f"{self.who}_quota.txt").write_text(qtxt, encoding="utf-8")
                (RUNS / f"{self.who}_quota.html").write_text(pop.content(), encoding="utf-8")
                log(self.who, f"配额弹窗已保存（{len(qtxt)} 字符） url={pop.url[:110]}")
                try:
                    pop.close()
                except Exception:
                    pass
            except Exception as exc:
                log(self.who, f"配额弹窗跳过: {exc.__class__.__name__}")
        ap = cfg.get("applicant", {})
        # 关键词要中（简/繁）英双语：被弹回入口页后 SPA 会切到英文界面，
        # 第 2 步 JSP 页的按钮实测就是 Continue / Home 这类英文。
        mapping = {
            "身份证明文件号码": ap.get("doc_no"),
            "證件號碼": ap.get("doc_no"),
            "Identity Document Number": ap.get("doc_no"),
            "驾驶执照号码": ap.get("dl_no"),
            "駕駛執照號碼": ap.get("dl_no"),
            "Driving Licence Number": ap.get("dl_no"),
            "Driving License Number": ap.get("dl_no"),
            "电邮": ap.get("email"),
            "電郵": ap.get("email"),
            "Email": ap.get("email"),
            "中文姓名": ap.get("name_cn"),
            "Chinese Name": ap.get("name_cn"),
            "称谓": ap.get("title"),
            "Title": ap.get("title"),
            # 证件类型下拉：值必须与上传的影像同源
            "身份证明文件类型": "往来港澳通行证",
            "Identity Document Type": "Exit-Entry Permit",
        }
        mapping = {k: v for k, v in mapping.items() if v}
        self.mark(f"开始填表，待填字段 {len(mapping)} 个")
        if mapping:
            fill_by_label(page, mapping, self.who)
        upload_files(page, [ap.get("doc_file"), ap.get("dl_file")], self.who)
        self.shot(page, "step2_filled", "资料已填、图片已上传（提交前）")

        if self.probe:
            log(self.who, "演练模式：已导出第2步结构，不继续提交")
            self.result = {"stage": "probe_step2", "url": page.url}
            _STOP.set()
            return

        # 提交前过验证码关（v3 隐形大多自动过；弹图片/图形码就响铃喊人）
        cst = captcha.guard(page, self.who, shot=self.shot, say=lambda m: log(self.who, m),
                            on_captcha=self.captcha_pack)
        if cst == "timeout":
            self.result = {"stage": "captcha_timeout", "url": page.url}
            N.failed("验证码超时，本轮放弃")
            return
        self.mark("点「继续」提交第2步")
        click_text(page, ["继续", "下一步", "繼續", "CONTINUE", "Continue", "NEXT", "Next"])
        page.wait_for_timeout(2500)
        self.shot(page, "after_step2", "提交资料后的页面")

        # 5) 第3步：选牌照事务处 / 日期 / 时段
        for attempt in range(6):
            if _STOP.is_set():
                return
            d = dump(page, self.who, f"step4_calendar_{attempt}")
            self.shot(page, f"calendar_{attempt}", f"第3步 日历/时段（第 {attempt + 1} 次尝试）")
            txt = self.body(page)
            pref = cfg.get("bureau_preference") or []
            chosen = False
            for name in pref:
                if name and name in txt:
                    if click_text(page, name):
                        log(self.who, f"选牌照事务处 {name}")
                        page.wait_for_timeout(1800)
                        self.shot(page, f"bureau_{attempt}", f"已选牌照事务处：{name}")
                        chosen = True
                        break
            day = pick_earliest_day(page, self.who)
            if day:
                self.shot(page, f"date_{attempt}", f"已选日期：{day}")
            slot = pick_earliest_slot(page, self.who) if day else None
            if slot:
                self.shot(page, f"slot_{attempt}", f"已选时段：{slot}")
            if day and slot:
                click_text(page, ["继续", "下一步", "繼續", "CONTINUE", "Continue", "NEXT", "Next"])
                page.wait_for_timeout(2500)
                break
            if not chosen:
                page.wait_for_timeout(2500)

        # 6) 第4步：确认递交
        d = dump(page, self.who, "step5_confirm")
        self.shot(page, "confirm", "第4步 确认递交页面")
        txt = self.body(page)
        for kw in ("确认编号", "预约编号", "成功", "已预约"):
            if kw in txt:
                log(self.who, f"★ 疑似成功：出现「{kw}」")
        cst = captcha.guard(page, self.who, shot=self.shot, say=lambda m: log(self.who, m),
                            on_captcha=self.captcha_pack)
        if cst == "timeout":
            self.result = {"stage": "captcha_timeout", "url": page.url}
            N.failed("验证码超时，本轮放弃")
            return
        self.mark("点「确认 / 递交」")
        click_text(page, ["确认", "確認", "递交", "遞交", "提交", "确定", "確定",
                          "CONFIRM", "Confirm", "SUBMIT", "Submit"])
        page.wait_for_timeout(3000)
        self.shot(page, "submitted", "已点递交，等待结果/OTP")

        # 7) OTP
        otp_t0 = time.time()
        otp_alerted = 0
        otp_email = (cfg.get("applicant") or {}).get("email", "未配置")
        for _ in range(120):
            if _STOP.is_set():
                return
            code = read_otp()
            if not code:
                waited = int(time.time() - otp_t0)
                # 每 120 秒提醒一次，最多 3 次：告诉你是哪个邮箱、等了多久
                stage = waited // 120
                if waited >= 120 and stage > otp_alerted and stage <= 3:
                    otp_alerted = stage
                    log(self.who, f"⚠ OTP 已等 {waited}s 未到（邮箱 {otp_email}）")
                    N.otp_missing(otp_email, waited)
            if code:
                self.mark(f"读到 otp.txt：{code[:2]}****")
                self.shot(page, "otp_page", "需要输入 OTP 的页面")
                data = page.evaluate(DUMP_JS)
                for f in data["fields"]:
                    if f["type"] in ("text", "tel", "number", "") and ("密码" in f["label"] + f["ctx"] or "OTP" in (f["label"] + f["ctx"]).upper()):
                        sel = f"input[name='{f['name']}'], #{f['id']}"
                        try:
                            page.query_selector(sel).fill(code)
                            log(self.who, "已填入 OTP")
                        except Exception:
                            continue
                cst = captcha.guard(page, self.who, shot=self.shot, say=lambda m: log(self.who, m),
                            on_captcha=self.captcha_pack)
                if cst == "timeout":
                    break
                click_text(page, ["继续", "确认", "提交", "验证"])
                page.wait_for_timeout(3000)
                break
            page.wait_for_timeout(2000)

        d = dump(page, self.who, "step6_result")
        txt = self.body(page)
        ok = any(k in txt for k in ("确认编号", "预约编号", "已成功", "确认通知"))
        self.shot(page, "result", "成功" if ok else "未确认成功")
        self.result = {"stage": "done", "ok": ok, "url": page.url,
                       "text": txt[:800]}
        if ok:
            _STOP.set()
            beep(5)
            log(self.who, "★★★ 拿到预约，请立刻检查电邮 tdabs@td.gov.hk ★★★")
            ref = ""
            for line in txt.splitlines():
                if "编号" in line and len(line) < 80:
                    ref = line.strip()
                    break
            N.success(ref)
        else:
            N.failed(f"{self.who} 走完流程但未确认成功：url={page.url[:80]}")


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1,
                    help="同时排几条队列。超过 2 条会提高同 IP 并发，容易被限速")
    ap.add_argument("--stagger", type=float, default=2.5,
                    help="多条队列之间的启动间隔秒数（默认 2.5，别设太小）")
    ap.add_argument("--probe", action="store_true", help="演练：走到第2步即停，导出结构")
    ap.add_argument("--attach", default="",
                    help="接管真 Chrome 的调试端口，如 http://127.0.0.1:9222（先跑 launch_chrome.ps1）")
    ap.add_argument("--channel", default=None,
                    help='"chrome"=用本机 Google Chrome；""=用 Playwright 自带 chromium')
    ap.add_argument("--headless", action="store_true", help="无头模式（仅演练用）")
    a = ap.parse_args()

    cfg = load_config()
    if a.attach:
        cfg["attach"] = a.attach
    if a.channel is not None:
        cfg["channel"] = a.channel
        if a.channel:
            # 命令行明确指定了内核 → 不再走接管模式，避免 config 里的 attach 抢先生效
            cfg["attach"] = ""
    if a.headless:
        cfg["headless"] = True

    if cfg.get("attach"):
        import urllib.request as _u
        try:
            _u.urlopen(cfg["attach"].rstrip("/") + "/json/version", timeout=5).read()
        except Exception as exc:
            sys.exit(f"连不上 {cfg['attach']}（{exc.__class__.__name__}）。"
                     f"先运行 launch_chrome.ps1，或用 --channel chrome 让脚本自己起浏览器。")
        print(f"接管模式：{cfg['attach']}（真 Chrome，指纹最接近人工）")
    else:
        print(f"启动模式：内核={cfg.get('channel') or 'chromium'} "
              f"headless={cfg.get('headless')}（想更接近人工请用 --attach）")

    RUNS.mkdir(exist_ok=True)
    PROFILES.mkdir(exist_ok=True)

    # 每次运行一个独立目录，全程录制（截图 + 页面源码 + DOM），方便事后优化脚本
    run_dir = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"本次录制目录：{run_dir}")

    n_workers = max(1, a.workers)
    if n_workers > 2:
        print(f"提示：{n_workers} 条队列会让同一出口 IP 的并发翻倍，官方 WAF 可能限速；"
              f"建议 2 条（--workers 2）。要更多请确认站点稳定。")
    ws = [Worker(i + 1, cfg, a.probe, run_dir) for i in range(n_workers)]
    for w in ws:
        w.start()
        time.sleep(max(0.5, a.stagger))
    for w in ws:
        w.join()
    print("\n==== 结果 ====")
    for w in ws:
        print(w.who, json.dumps(w.result, ensure_ascii=False)[:400] if w.result else None)
        if w.rec is not None:
            try:
                idx = w.rec.finish()
                n = len([r for r in w.rec.records if r.get("step") != "mark"])
                print(f"  {w.who} 录制 {n} 张 → {idx}")
            except Exception as exc:
                print(f"  {w.who} 生成复盘失败：{exc}")
    print(f"\n复盘索引：{run_dir}\\w1\\index.md")


if __name__ == "__main__":
    main()
