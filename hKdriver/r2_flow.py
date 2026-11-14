# -*- coding: utf-8 -*-
"""r2_flow.py —— R2：全程在无窗口内核里跑完（票不转移）

===========================================================================
为什么必须是 R2
---------------------------------------------------------------------------
2026-11-12 16:32 实测（挑战窗口内）：

    弱票（只有 HWWAFSESID，站点不挑战）  → curl_cffi chrome136 → 200   ✅
    硬票（HIK0kat3rt9xO，过了挑战）      → chrome136/131/默认 全 400    ❌ 绑指纹

所以「浏览器铸票 → 导出 cookie → 纯 HTTP 复用」在挑战窗口内不成立。
唯一稳妥：**从排队到进业务，都在同一个浏览器上下文里完成**，cookie 不搬家。

流程（每一步都是导航或同源 fetch，cookie 自然流动）
---------------------------------------------------------------------------
  ① goto abs3/?lang=sc            → 过 412 挑战，拿到 HWWAFSESID + HIK0
  ② goto SelectCountry?…          → 302 落到 abs4/?c=..&e=..&t=..
                                     ★ 从 page.url 里解析 c / e / t
  ③ 同源 fetch POST /spa-api/queue/{c}/{e}/enqueue  → queueId
  ④ 同源 fetch POST .../{queueId}/status  每 3s 轮询
  ⑤ status 出现 redirectUrl     → goto 该 URL（Queue-it 在这里下发放行票）
  ⑥ goto abs1 第 1 步入口        → 业务页 HTML 落盘（判号离线做）

判号为什么离线：step1 要按页面 JS 设的表单 action 提交，字段 66 个，
在浏览器里模拟提交容易踩坑；先把 HTML 拿到手，再用 sniper 的解析函数判，
出错也不会毁掉这次放行。

用法
---------------------------------------------------------------------------
  python r2_flow.py --proxy "http://ip:port" --minutes 35
  python r2_flow.py --proxy "..." --minutes 35 --ip-id r1
  python r2_flow.py --proxy "..." --dry         # 只到 ③，验链路
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

CHROME = str(BASE / "cft" / "chrome-win64" / "chrome.exe")
ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run", "--no-default-browser-check",
    "--disable-dev-shm-usage", "--lang=zh-HK",
    "--disable-background-networking",
    "--window-position=-32000,-32000", "--window-size=1366,820",
    "--js-flags=--max-old-space-size=512",
]

ENTRY = "https://abs3.td.gov.hk/?lang=sc"
SELECT = ("https://abs3.td.gov.hk/SelectCountry?lang=sc&actionType=apply"
          "&authMethod=withOutIas")
STEP1 = ("https://abs1.td.gov.hk/tdab2/tdabs_di_external/AdminServlet"
         "?cmd=cmdIntoSelectAuthDI&func=apply&auth=withOutIas"
         "&country=mainland&lang=sc")
RUN_DIR = BASE / "runs" / "r2"

FETCH_JS = r"""
async ([method, url, body]) => {
  try {
    const r = await fetch(url, {
      method,
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: body || null,
      credentials: 'include',
      redirect: 'follow',
    });
    const t = await r.text();
    return {status: r.status, len: t.length, body: t.slice(0, 1200),
            url: r.url || ''};
  } catch (e) { return {error: String(e).slice(0, 200)}; }
}
"""

PROBE_JS = r"""
() => ({
  url: location.href,
  cookie: document.cookie,
  title: document.title,
  ready: document.readyState,
  bodyLen: (document.body ? (document.body.innerText || document.body.textContent || '').length : 0),
  hik: Object.keys(Object.fromEntries(
        document.cookie.split(';').filter(Boolean).map(s => {
          const i = s.indexOf('='); return [s.slice(0, i).trim(), s.slice(i + 1)];
        }))).filter(k => k.startsWith('HIK0kat3rt9x')),
})
"""


DUMP_JS = r"""
() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const txt = (e) => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  return {
    url: location.href, title: document.title,
    bodyText: (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 1200),
    forms: q('form').map(f => ({ action: f.getAttribute('action') || '', method: f.method || '' })),
    inputs: q('input,select,textarea').map(e => ({ tag: e.tagName, type: e.type || '',
      name: e.name || '', id: e.id || '', value: (e.value || '').slice(0, 40), checked: !!e.checked })),
    buttons: q('button,a[role=button],input[type=button],input[type=submit]')
      .map(e => ({ tag: e.tagName, id: e.id || '', cls: (e.className || '').toString().slice(0, 60), text: txt(e) }))
      .slice(0, 40),
  };
}
"""


def now() -> str:
    return time.strftime("%H:%M:%S")

class Log:
    def __init__(self):
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(RUN_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-r2.log",
                       "a", encoding="utf-8")

    def __call__(self, msg, tag="·"):
        line = f"{now()} {tag} {msg}"
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


def parse_ce(text: str) -> dict:
    """从 URL 里抠 c / e / t（Queue-it 的三个参数）。"""
    out = {}
    try:
        q = parse_qs(urlparse(text).query)
        if q.get("c"):
            out["c"] = q["c"][0]
        if q.get("e"):
            out["e"] = q["e"][0]
        if q.get("t"):
            out["t"] = q["t"][0]
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="R2：浏览器内跑完排队到业务")
    ap.add_argument("--proxy", default="")
    ap.add_argument("--minutes", type=float, default=35.0)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--ip-id", default="r2")
    ap.add_argument("--c", default="", help="customer，如 tdabsditest（外部传入可跳过 ②）")
    ap.add_argument("--e", default="", help="eventId，如 tdabsd261112xxx（外部传入可跳过 ②）")
    ap.add_argument("--dry", action="store_true", help="只跑到 enqueue，验链路")
    ap.add_argument("--no-block", action="store_true",
                    help="不掐资源（排查挑战完成度用）")
    ap.add_argument("--wait-ce", type=float, default=25.0,
                    help="② 等 c/e 出现的最长秒数（挑战窗口内 SelectCountry 不再 302）")
    a = ap.parse_args()

    from patchright.sync_api import sync_playwright

    log = Log()
    log(f"R2 启动  出口={a.proxy or '直连'}  窗口={a.minutes} 分钟", "=")
    p = pw = None
    browser = ctx = page = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(executable_path=CHROME, headless=False,
                                     args=ARGS,
                                     ignore_default_args=["--enable-automation"])
        kw = dict(ignore_https_errors=True, locale="zh-HK",
                  timezone_id="Asia/Hong_Kong",
                  viewport={"width": 1366, "height": 768},
                  extra_http_headers={"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"})
        if a.proxy:
            kw["proxy"] = {"server": a.proxy}
        ctx = browser.new_context(**kw)
        page = ctx.new_page()
        page.set_default_timeout(60000)

        # 记录所有请求 URL：SelectCountry 之后 queue 的 c/e 可能只出现在 XHR/replace 里
        seen_urls = []
        try:
            page.on("request", lambda r: seen_urls.append(r.url))
            page.on("framenavigated", lambda fr: seen_urls.append(fr.url))
        except Exception:
            pass

        # ★ 2026-11-13 16:40 实测：短效代理（巨量 pt=1）扛不住真实内核的几十条并发连接，
        #   表现是 ERR_TUNNEL_CONNECTION_FAILED（代理侧拒绝 CONNECT），而中性站点 curl 还通。
        #   把图片/字体/样式/媒体掐掉，只留 document/script/xhr/fetch，连接数从几十降到几条。
        BLOCK = {"image", "media", "font", "stylesheet", "texttrack", "manifest"}

        def _route(route):
            try:
                rt = route.request.resource_type
            except Exception:
                rt = ""
            if rt in BLOCK:
                return route.abort()
            return route.continue_()

        try:
            if not a.no_block:
                page.route("**/*", _route)
        except Exception:
            pass

        # ---------- ① 过挑战
        t0 = time.time()
        st = None
        try:
            r = page.goto(ENTRY, wait_until="commit", timeout=60000)
            st = r.status if r else None
        except Exception as e:
            log(f"① goto 异常 {type(e).__name__}", "!")
        log(f"① 首包 {st}（412=挑战）")

        # ★ 2026-11-13 17:03 实测修正：**HIK0 出现 ≠ 挑战完成**。
        #   首包 400 时页面也会带一枚 HIK0（种子），我们在那种状态下就去敲 SelectCountry，
        #   结果那次导航又被判 412/400。真正的成功判据是「主文档被挑战 JS 重写后有正文」。
        def _probe():
            try:
                return page.evaluate(PROBE_JS)
            except Exception:
                return None

        def wait_doc_ok(seconds: float, tag: str) -> bool:
            end = time.time() + seconds
            while time.time() < end:
                s = _probe()
                if s:
                    try:
                        bl = int(s.get("bodyLen") or 0)
                    except Exception:
                        bl = 0
                    if bl > 300 or (s.get("title") or "").strip():
                        log(f"{tag} 文档就绪 title={s.get('title')!r} bodyLen={bl}", "√")
                        return True
                time.sleep(0.5)
            return False

        deadline = time.time() + 75
        reloaded = 0
        last_note = 0.0
        while time.time() < deadline:
            s = _probe()
            try:
                jar = ctx.cookies()
            except Exception:
                jar = []
            hik = {c["name"]: len(c.get("value") or "") for c in jar
                   if str(c.get("name", "")).startswith("HIK0kat3rt9x")}
            best = max(hik.values()) if hik else 0
            bl = 0
            if s:
                try:
                    bl = int(s.get("bodyLen") or 0)
                except Exception:
                    bl = 0
            # ★ 判据二选一：正文出现，或 HIK0 长到"真令牌"级别（∅109 是种子，343/492 才是票）
            if bl > 300 or (s and (s.get("title") or "").strip()) or best >= 200:
                log(f"① 挑战完成 bodyLen={bl} title={(s or {}).get('title')!r} "
                    f"HIK0={hik} {time.time()-t0:.1f}s", "√")
                break
            if time.time() - last_note > 6:
                last_note = time.time()
                log(f"① 等待中 bodyLen={bl} HIK0={hik} {time.time()-t0:.0f}s", ".")
            if reloaded < 2 and time.time() - t0 > 18 * (reloaded + 1):
                reloaded += 1
                log(f"① {time.time()-t0:.0f}s 仍无内容/无长令牌 → reload #{reloaded}", "~")
                try:
                    page.reload(wait_until="commit", timeout=45000)
                except Exception as e:
                    log(f"   reload 异常 {type(e).__name__}", "!")
            time.sleep(0.5)
        else:
            log("① 75s 内既无正文也无长令牌 —— 挑战没跑完，后面大概率被挡", "!")

        # ---------- ② 走 SelectCountry，靠 302 拿到 c/e/t
        # 若命令行给了 --c/--e 就跳过（c/e 可以先用纯 HTTP 发现，省挑战配额）
        ce = {}
        if a.c and a.e:
            ce = {"c": a.c, "e": a.e}
            log(f"② 使用外部传入 c={a.c} e={a.e}（跳过页面发现）")
        else:
            # 优先用**页面导航**：WAF 的动态参数是页面 JS 钩子给 XHR/fetch 加的，
            # ctx.request 绕过钩子 → 即便有 HIK0 也 412（17:03 实测）。
            # 导航会走完整挑战流程（412 → 挑战 → 302），代价是慢，但能拿到 Location。
            try:
                r2r = page.goto(SELECT, wait_until="commit", timeout=45000)
                log(f"② 导航落地 status={r2r.status if r2r else '?'} {page.url[:110]}")
                # 这次导航本身可能又被挑战（400/412）：等它自己跑完挑战再找 c/e
                if r2r is not None and r2r.status in (400, 401, 412):
                    log("② 该次导航被挑战，等挑战 JS 重写文档", "~")
                    wait_doc_ok(35, "②")
                # ★ 2026-11-13 16:30 实测：挑战窗口内 SelectCountry 直接 200 落 SPA，
                #   不再像软票时那样 302 出 Location。c/e 只能从「请求流水 / 页面 replace / DOM」里找。
                ce = {}
                t_ce = time.time() + a.wait_ce
                while time.time() < t_ce and not (ce.get("c") and ce.get("e")):
                    ce = parse_ce(page.url)
                    if not (ce.get("c") and ce.get("e")):
                        for u in reversed(seen_urls[-40:]):
                            ce = parse_ce(u)
                            if ce.get("c") and ce.get("e"):
                                log(f"   从请求流水命中 {u[:120]}")
                                break
                    if not (ce.get("c") and ce.get("e")):
                        try:
                            html_now = page.content() or ""
                        except Exception:
                            html_now = ""
                        m2 = re.search(r"[?&]c=([\w.-]+)&(?:amp;)?e=([\w.-]+)", html_now)
                        if m2:
                            ce = {"c": m2.group(1), "e": m2.group(2)}
                            log("   从页面源码命中 c/e")
                    time.sleep(0.8)
                log(f"   解析 c={ce.get('c')} e={ce.get('e')} t={str(ce.get('t'))[:60]}")
                # ★ 还拿不到就按真人路径点一次：选「中国内地」→ 继续。
                #   挑战窗口内 SPA 不会自己跳队列，必须由这次点击触发 302/abs4。
                if not (ce.get("c") and ce.get("e")):
                    try:
                        info = page.evaluate(DUMP_JS)
                        (RUN_DIR / f"{a.ip_id}-select.json").write_text(
                            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
                        log(f"   页面取证 title={info.get('title')!r} inputs={len(info.get('inputs') or [])} "
                            f"buttons={len(info.get('buttons') or [])}")
                        clicked = []
                        for sel in ("input[value=mainland]", "input#mainland", "input[name=country]",
                                    "input[type=radio]"):
                            try:
                                if page.locator(sel).count():
                                    page.locator(sel).first.click(timeout=4000, force=True)
                                    clicked.append(sel)
                                    break
                            except Exception:
                                continue
                        page.wait_for_timeout(600)
                        for sel in ("button:has-text('繼續')", "button:has-text('继续')",
                                    "a:has-text('繼續')", "a:has-text('继续')",
                                    "input[type=submit]", "input[type=button]", "button"):
                            try:
                                if page.locator(sel).count():
                                    page.locator(sel).first.click(timeout=4000, force=True)
                                    clicked.append(sel)
                                    break
                            except Exception:
                                continue
                        log(f"   点击路径 {clicked}")
                        t2 = time.time() + 20
                        while time.time() < t2 and not (ce.get("c") and ce.get("e")):
                            ce = parse_ce(page.url)
                            if not (ce.get("c") and ce.get("e")):
                                for u in reversed(seen_urls[-60:]):
                                    ce = parse_ce(u)
                                    if ce.get("c") and ce.get("e"):
                                        log(f"   点击后从请求流水命中 {u[:120]}")
                                        break
                            time.sleep(0.8)
                    except Exception as e:
                        log(f"   点击路径异常 {type(e).__name__}: {str(e)[:80]}", "!")
                    # ★ 最后一招：不在导航层碰 SelectCountry，改成**页内同源 fetch**。
                    #   挑战后导航被判 400 时，XHR 有时仍放行；302 落到 abs4 的 URL
                    #   会出现在请求流水里（跨源读不到响应，但 URL 能看见）。
                    if not (ce.get("c") and ce.get("e")):
                        try:
                            page.evaluate(
                                "(u)=>{try{return fetch(u,{credentials:'include'}).then(r=>r.status).catch(e=>String(e));}"
                                "catch(e){return String(e)}}", SELECT)
                            t3 = time.time() + 12
                            while time.time() < t3 and not (ce.get("c") and ce.get("e")):
                                for u in reversed(seen_urls[-60:]):
                                    ce = parse_ce(u)
                                    if ce.get("c") and ce.get("e"):
                                        log(f"   fetch 路径命中 {u[:120]}")
                                        break
                                time.sleep(0.8)
                        except Exception as e:
                            log(f"   fetch 路径异常 {type(e).__name__}: {str(e)[:80]}", "!")
            except Exception as e:
                log(f"② 导航异常 {type(e).__name__}: {str(e)[:90]}", "!")
                ce = parse_ce(page.url)
                if ce:
                    log(f"   从当前 URL 兜底解析 c={ce.get('c')} e={ce.get('e')}")

        if not (ce.get("c") and ce.get("e")):
            # 兜底：从页面里 textContent 找一次
            try:
                html = page.content() or ""
                m = re.search(r"[?&]c=([\w.-]+)&e=([\w.-]+)", html)
                if m:
                    ce = {"c": m.group(1), "e": m.group(2)}
                    log(f"   兜底解析 c={ce['c']} e={ce['e']}")
            except Exception:
                pass

        if not (ce.get("c") and ce.get("e")):
            log("② 拿不到 c/e，放弃（队列未开或页面形态变了）", "!!")
            return 21

        # ---------- ③ enqueue（同源 fetch，就在 abs4 队列页上做）
        base = f"https://abs4.td.gov.hk/spa-api/queue/{ce['c']}/{ce['e']}"
        r1 = page.evaluate(FETCH_JS, ["POST", base + "/enqueue", "{}"])
        log(f"③ enqueue → {json.dumps(r1, ensure_ascii=False)[:260]}")
        qid = ""
        try:
            qid = json.loads(r1.get("body") or "{}").get("queueId") or ""
        except Exception:
            pass
        if not qid:
            log("③ 没拿到 queueId，停", "!!")
            return 22
        log(f"③ queueId={qid}", "√")

        if a.dry:
            log("--dry：链路验到 enqueue 为止", "=")
            return 0

        # ---------- ④ 轮询 status
        deadline = time.time() + a.minutes * 60
        redirect = ""
        n = 0
        while time.time() < deadline:
            n += 1
            r = page.evaluate(FETCH_JS, ["POST", f"{base}/{qid}/status", "{}"])
            body = r.get("body") or ""
            try:
                j = json.loads(body)
            except Exception:
                j = {}
            stt = j.get("QueueState")
            fc = j.get("forecastStatus")
            rurl = j.get("redirectUrl") or ""
            ticket = j.get("ticket") or {}
            log(f"④ #{n:03d} state={stt} forecast={fc} "
                f"ahead={ticket.get('usersInLineAheadOfYou')} "
                f"redirect={rurl[:50]}")
            if rurl:
                redirect = rurl
                break
            if stt == 3 or (fc or "").lower() in ("served", "afterevent", "after"):
                log("④ 状态显示已放行但没有 redirectUrl —— 继续等一下", "~")
            time.sleep(a.interval)

        if not redirect:
            log("④ 窗口内没放行（event 未开闸）", "!!")
            (RUN_DIR / f"{a.ip_id}-noRelease.html").write_text(
                page.content() or "", encoding="utf-8")
            return 42

        # ---------- ⑤ 用 redirectUrl 换放行票
        if redirect.startswith("http://"):
            redirect = "https://" + redirect[len("http://"):]
        log(f"⑤ 走放行 URL {redirect[:110]}")
        try:
            rr = page.goto(redirect, wait_until="domcontentloaded", timeout=60000)
            log(f"⑤ 落地 {page.url[:110]}  status={rr.status if rr else '?'}")
        except Exception as e:
            log(f"⑤ 异常 {type(e).__name__}: {str(e)[:80]}", "!")
        page.wait_for_timeout(1500)
        s = page.evaluate(PROBE_JS)
        log(f"   cookie 里 QueueIT={'QueueITAccepted' in (s.get('cookie') or '')}")

        # ---------- ⑥ 进业务第 1 步
        log("⑥ 进业务层第 1 步")
        try:
            r6 = page.goto(STEP1, wait_until="domcontentloaded", timeout=60000)
            log(f"⑥ 状态 {r6.status if r6 else '?'}  url={page.url[:110]}")
        except Exception as e:
            log(f"⑥ 异常 {type(e).__name__}: {str(e)[:80]}", "!")
        page.wait_for_timeout(1200)
        html = page.content() or ""
        out = RUN_DIR / f"{a.ip_id}-step1.html"
        out.write_text(html, encoding="utf-8")
        log(f"⑥ 业务页 {len(html)} 字节 → {out.name}", "√")

        # 粗判（详细判号用 sniper 的解析离线做）
        for kw, label in (("frmAppointment", "第1步表单在"),
                          ("沒有可供預約", "无名额(繁)"),
                          ("没有可供预约", "无名额(简)"),
                          ("TrackId", "有 TrackId")):
            if kw in html:
                log(f"   命中 {label}")

        (RUN_DIR / f"{a.ip_id}-result.json").write_text(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "proxy": a.proxy,
            "c": ce.get("c"), "e": ce.get("e"), "queueId": qid,
            "redirect": redirect, "final_url": page.url,
            "polls": n, "html_len": len(html),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        log("收尾", "=")
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
        log.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
