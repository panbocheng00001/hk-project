# -*- coding: utf-8 -*-
"""replay.py —— 会话「门票」验证与接口层接力（骨架，默认只读）
=====================================================================
定位（按 2026-11-09 复盘结论）：
    浏览器（extension）负责过三道门：
        ① WAF 412 JS 挑战 / 滑块
        ② abs4 排队放行（服务端写会话）
        ③ MTCaptcha（captchaToken，由页面生成）
    一旦 JSP 表单页出现，才轮到本脚本用「接口」接力，而不是任何一步都走接口。

门票 = 两样东西
    · cookie：HWWAFSESID / HIK0kat3rt9xO / JSESSIONID …
    · 服务端会话状态：abs4 放行后才写入

两个硬边界（已在实盘确认）
    · 门票与出口 IP 绑定，换 IP 即作废
    · maxAgeMillis = 1200000（20 分钟），页面与服务端双向校验

本脚本现阶段只做三件事（不会误提交）：
    1) 找到扩展刚写下的会话快照 runs/session_*.json
    2) 用快照里的 cookie 发一次 GET，判断会话还活着还是已被 302 弹回
    3) 把表单字段 / hidden 参数列出来，供下一步构造 POST

用法：
    python replay.py                 # 用最新快照做一次只读校验
    python replay.py --list          # 列出所有快照
    python replay.py --file runs\session_form_enter_w1_230911.json
    python replay.py --post          # 打印将要发送的 POST 骨架（不发送）
"""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

# 与浏览器保持一致的请求头（少一个都可能被判异常）
COMMON_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def list_sessions() -> list[Path]:
    files = sorted(RUNS.glob("session_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cookie_header(snap: dict) -> str:
    parts = []
    for c in snap.get("cookies") or []:
        if not c.get("name"):
            continue
        parts.append(f"{c['name']}={c.get('value', '')}")
    return "; ".join(parts)


def probe(snap: dict) -> None:
    url = snap.get("url") or ""
    if not url:
        print("快照里没有 url，跳过")
        return
    ck = cookie_header(snap)
    headers = dict(COMMON_HEADERS)
    if ck:
        headers["Cookie"] = ck

    print("=" * 70)
    print("目标:", url[:120])
    print("cookie 数:", len(snap.get("cookies") or []))
    for c in snap.get("cookies") or []:
        flag = " [HttpOnly]" if c.get("httpOnly") else ""
        print(f"   {c.get('domain'):22} {c.get('name'):28} len={len(str(c.get('value') or ''))}{flag}")

    print("-" * 70)
    print("发送只读 GET …")
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    try:
        with OPENER.open(req, timeout=30) as r:
            body = r.read()
            print(f"  status = {r.status}  len = {len(body)}")
            loc = r.headers.get("Location")
            if loc:
                print("  Location:", loc[:140])
            text = body.decode("utf-8", "ignore")
            # 关键判断
            if r.status == 200 and ("continueBtn1" in text or "输入资料" in text
                                    or "Enter Information" in text):
                print("  ✅ 会话有效：仍在第 2 步表单页")
            elif "没有可供预约的时段" in text or "no available booking slots" in text.lower():
                print("  ⚠ 会话有效，但系统当前无名额（正常状态）")
            elif r.status == 200:
                print("  ? 200 但不像表单页，可能是挑战页或首页")
            elif r.status == 302:
                print("  ✗ 被 302 弹回：服务端会话状态已失效（门票作废）")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}")
        loc = e.headers.get("Location")
        if loc:
            print("  Location:", loc[:140])
        if e.code in (302, 303):
            print("  ✗ 门票作废（会话状态缺失或已过期）")
    except Exception as exc:
        print("  异常:", type(exc).__name__, str(exc)[:140])


def show_fields(snap: dict, post_hint: bool = False) -> None:
    fields = snap.get("fields") or {}
    print("-" * 70)
    print(f"表单字段 {len(fields)} 个：")
    for k, v in list(fields.items()):
        sv = "" if v in (None, "") else (str(v)[:40] + ("…" if len(str(v)) > 40 else ""))
        print(f"   {k:42} = {sv}")

    hidden_like = [k for k in fields if k.startswith("storedAppointment")]
    print("-" * 70)
    print(f"其中 storedAppointment* 类参数 {len(hidden_like)} 个 —— 这些通常是服务端回填的会话键")
    if post_hint:
        print("-" * 70)
        print("POST 骨架（**不会发送**，仅供明天对照浏览器实际请求填值）：")
        print(f"  URL    : {snap.get('url')}")
        names = ["storedAppointmentId", "storedAppointmentSessionId",
                 "storedAppointmentTimeslotId", "captchaToken", "lang", "timestamp"]
        for n in names:
            print(f"  {n:32} = <{fields.get(n, '待填') if n in fields else '待填'}>")


def read_cookie_file(path: Path) -> str:
    """支持三种来源：cookies.txt（Netscape）、cookies.json、cookie_header.txt。"""
    name = path.name.lower()
    if name.endswith(".json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        ck = d.get("cookies") or []
        return "; ".join(f"{c['name']}={c.get('value', '')}" for c in ck if c.get("name"))
    if "header" in name:
        return path.read_text(encoding="utf-8").strip()
    # Netscape 格式：7 列，tab 分隔
    parts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 7:
            parts.append(f"{cols[5]}={cols[6]}")
    return "; ".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="")
    ap.add_argument("--cookie-file", default="",
                    help="直接用导出的 cookie 文件探测（runs\\cookies.txt / cookies.json / cookie_header.txt）")
    ap.add_argument("--url", default="", help="配合 --cookie-file 使用的目标 URL")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pool", action="store_true",
                    help="从 token_pool 票池取票（含代理/UA 一致），而不是读导出的 cookie 文件")
    ap.add_argument("--ip-id", default="", help="配合 --pool：指定出口票；留空自动挑最热的")
    ap.add_argument("--db", default="", help="配合 --pool：票池 sqlite 路径")
    ap.add_argument("--post", action="store_true", help="打印 POST 骨架（不发送）")
    a = ap.parse_args()

    # 模式零：票池取票（token_pool.py 管票，本脚本只管用票）
    if a.pool:
        try:
            from token_pool import SlotClient, TokenDead, _store
        except Exception as exc:
            raise SystemExit(f"token_pool 不可用：{exc}")
        url = a.url or ("https://abs2.td.gov.hk/tdab2/tdabs_di_external/"
                        "AdminServlet_tchinese?cmd=cmdGetOfficeQuotaDIAction")
        try:
            c = SlotClient(store=_store(a.db) if a.db else None, ip_id=a.ip_id)
            slot = c.acquire()
        except TokenDead as exc:
            raise SystemExit(f"票池无可用票：{exc}（先 python token_pool.py --warm）")
        try:
            from journal import J
            J().log("replay", "pick", ip_id=slot["ip_id"], stage=slot["state"],
                    how=slot.get("_picked"), left=round(slot.get("_left", 0) / 60, 1),
                    url=url[:150])
        except Exception:
            pass
        print(f"票池取票：{slot['ip_id']}  state={slot['state']}  类型={slot.get('_picked','-')}  "
              f"剩余={slot.get('_left',0)/60:.1f}m  "
              f"出口={slot['egress_ip'] or slot['proxy'] or '直连'}")
        print(f"cookie {len(json.loads(slot['cookies'] or '[]'))} 条 | UA={str(slot['ua'])[:60]}")
        print("目标:", url[:110])
        try:
            r = c.session.get(url, timeout=30, verify=False, allow_redirects=False)
        except Exception as exc:
            raise SystemExit(f"请求异常：{type(exc).__name__} {exc}")
        txt = r.text or ""
        verdict = ("no_quota" if ("没有可供预约的时段" in txt or "no available" in txt.lower())
                   else "has_slot" if "continueBtn1" in txt
                   else "bounced" if r.status_code in (301, 302, 303)
                   else "challenge" if r.status_code in (403, 412) else "unknown")
        try:
            from journal import J
            J().log("replay", "verdict", ip_id=slot["ip_id"], code=r.status_code,
                    n=len(txt), verdict=verdict, ok=verdict in ("no_quota", "has_slot"))
        except Exception:
            pass
        print(f"  status = {r.status_code}  len = {len(txt)}")
        loc = r.headers.get("Location")
        if loc:
            print("  Location:", loc[:140])
        if "没有可供预约的时段" in txt or "no available booking slots" in txt.lower():
            print("  ✅ 门票有效（系统正常返回，当前无名额）")
        elif "continueBtn1" in txt:
            print("  ✅ 门票有效，且有名额（出现 continueBtn1）")
        elif r.status_code in (301, 302, 303):
            print("  ✗ 被弹回：排队放行状态不在这张票上（WAF 层可能已过，见 abs3/?lang=en）")
        elif r.status_code in (403, 412):
            print("  ✗ WAF 挑战未通过：这张票需要重铸")
        else:
            print("  ? 200 但内容不认识，可能被降级")
        c.close()
        return

    # 模式一：直接拿导出的 cookie 文件去验证会话
    if a.cookie_file:
        cp = Path(a.cookie_file)
        if not cp.is_absolute():
            cp = BASE / cp
        if not cp.exists():
            raise SystemExit(f"找不到 {cp}")
        ck = read_cookie_file(cp)
        print("cookie 文件:", cp.name, "| 条数:", len([x for x in ck.split(';') if x.strip()]))
        url = a.url or "https://abs2.td.gov.hk/tdab2/tdabs_di_external/AdminServlet_tchinese?cmd=cmdGetOfficeQuotaDIAction"
        print("目标:", url[:110])
        headers = dict(COMMON_HEADERS)
        headers["Cookie"] = ck
        req = urllib.request.Request(url, headers=headers)
        try:
            with OPENER.open(req, timeout=30) as r:
                body = r.read()
                print(f"  status = {r.status}  len = {len(body)}")
                txt = body.decode("utf-8", "ignore")
                if "没有可供预约的时段" in txt or "no available booking slots" in txt.lower():
                    print("  ✅ 门票有效（系统正常返回，当前无名额）")
                elif "continueBtn1" in txt:
                    print("  ✅ 门票有效，且有名额（出现 continueBtn1）")
                else:
                    print("  ? 200 但内容不认识，可能被降级")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}  Location={e.headers.get('Location')}")
            if e.code in (302, 303):
                print("  ✗ 门票作废（被弹回）")
        except Exception as exc:
            print("  异常:", type(exc).__name__, str(exc)[:140])
        return

    files = list_sessions()
    if a.list or not files:
        print(f"runs/ 下的会话快照（{len(files)} 个）：")
        for p in files[:20]:
            print(f"   {p.name}  {time.strftime('%m-%d %H:%M', time.localtime(p.stat().st_mtime))}")
        if not files:
            print("   （还没有快照 —— 需要扩展先走到 JSP 表单页）")
        return

    path = Path(a.file) if a.file else files[0]
    if not path.exists():
        raise SystemExit(f"找不到 {path}")
    snap = load(path)
    print("快照:", path.name, "| stage:", snap.get("stage"), "| inst:", snap.get("inst") or "-")
    probe(snap)
    show_fields(snap, post_hint=a.post)


if __name__ == "__main__":
    main()
