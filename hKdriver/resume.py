# -*- coding: utf-8 -*-
"""resume.py —— 从「已经排上队的浏览器窗口」继续，不重新 goto。

为什么要有它：
  WAF 的 412 是 JS 挑战，脚本自己 goto 会再次触发挑战、并把已拿到的排队位作废。
  所以人在窗口里手动过验证、进到 abs4 排队页之后，用本脚本从当前页接管：
      等放行 → 选国家 → 填资料 → 上传 → 验证码 → 继续 → 选日期时段 → 确认 → OTP

用法：
  python resume.py --attach http://127.0.0.1:9223
"""
from __future__ import annotations

import argparse
import time

from playwright.sync_api import sync_playwright

import captcha
import grab as G
import human as H
import notifier as N

WHO = "r1"


def log(msg: str) -> None:
    G.log(WHO, msg)


def build_mapping(ap: dict) -> dict:
    return {
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
        "身份证明文件类型": "往来港澳通行证",
        "Identity Document Type": "Exit-Entry Permit",
    }


def pick_country(page) -> bool:
    for r in page.query_selector_all('input[type="radio"]'):
        try:
            lab = (r.evaluate(
                "e => (e.closest('label')?.innerText || e.parentElement?.innerText || '')") or "").strip()
        except Exception:
            continue
        if "内地" in lab or "Mainland" in lab:
            try:
                r.check(force=True)
            except Exception:
                pass
            page.wait_for_timeout(600)
            log(f"选国家/地方：{lab[:24]}")
            return True
    return False


def body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", default="", help="已排上队的窗口，如 http://127.0.0.1:9223")
    a = ap.parse_args()

    cfg = G.load_config()
    applicant = cfg.get("applicant", {})
    attach = a.attach or (cfg.get("attach") or "").strip()
    if not attach:
        raise SystemExit("需要 --attach（先手动过验证并进到排队页）")
    mapping = {k: v for k, v in build_mapping(applicant).items() if v}

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(attach)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f"接管现有窗口：{page.url[:120]}")

        # ---------- 1) 等待放行 / 选国家 ----------
        t0 = time.time()
        reached_form = False
        last_beat = -1.0
        blank_streak = 0
        while time.time() - t0 < 2700:
            url = page.url
            elapsed = time.time() - t0

            if "abs4.td.gov.hk" in url:          # 排队中：什么都别做，只打心跳
                if elapsed - last_beat >= 30:
                    last_beat = elapsed
                    log(f"⏳ 排队中 {int(elapsed)}s（位置由官方随机分配，别刷新）")
                page.wait_for_timeout(1500)
                continue

            if "SelectCountry" in url:
                if pick_country(page):
                    page.wait_for_timeout(800)
                    G.click_text(page, ["继续", "繼續", "CONTINUE", "Continue"])
                page.wait_for_timeout(2500)
                continue

            if "AdminServlet" in url or page.query_selector("#continueBtn1"):
                reached_form = True
                break

            txt = body(page)
            if "没有可供预约的时段" in txt or "no available" in txt.lower():
                reached_form = True
                break

            # 既不在排队域、也没到资料页：如果一直是空白，说明撞上 WAF 挑战页
            if elapsed - last_beat >= 20:
                last_beat = elapsed
                log(f"… 等待 {int(elapsed)}s url={url[:70]}")
            try:
                blank = len(txt.strip()) < 60
            except Exception:
                blank = False
            blank_streak = blank_streak + 1 if blank else 0
            if blank_streak >= 5:
                log("✗ 页面是空白挑战页：请在这个窗口手动过验证（脚本已停手，不再请求）")
                N.waf_blocked("resume · 页面空白")
                return
            page.wait_for_timeout(1500)

        if not reached_form:
            log(f"没等到资料页，停在 {page.url[:100]}")
            N.failed("resume：排队未放行或页面异常")
            return
        log(f"★ 已放行，进入资料页：{page.url[:100]}")

        # ---------- 2) 第2步：填资料 ----------
        page.wait_for_timeout(1200)
        txt = body(page)
        if "没有可供预约的时段" in txt or "no available" in txt.lower():
            log("⚠ 系统提示：现时没有可预约时段（今天没号）")
            N.failed("resume：已放行但无名额")
            return
        N.admitted(f"resume · {page.url[:80]}")
        G.dump(page, WHO, "resume_step2_blank")
        n = G.fill_by_label(page, mapping, WHO)
        log(f"填了 {n} 个字段")
        G.upload_files(page, [applicant.get("doc_file"), applicant.get("dl_file")], WHO)
        G.dump(page, WHO, "resume_step2_filled")

        state = captcha.guard(page, WHO, say=log,
                              on_captcha=lambda pg, info, note="": None)
        if state == "timeout":
            N.failed("resume：验证码超时")
            return
        G.click_text(page, ["继续", "下一步", "繼續", "CONTINUE", "Continue", "NEXT", "Next"])
        page.wait_for_timeout(2500)

        # ---------- 3) 第3步：办事处/日期/时段 ----------
        for attempt in range(6):
            G.dump(page, WHO, f"resume_calendar_{attempt}")
            txt = body(page)
            for name in (cfg.get("bureau_preference") or []):
                if name and name in txt:
                    if G.click_text(page, name):
                        log(f"选牌照事务处 {name}")
                        page.wait_for_timeout(1800)
                        break
            day = G.pick_earliest_day(page, WHO)
            slot = G.pick_earliest_slot(page, WHO) if day else None
            if day and slot:
                G.click_text(page, ["继续", "下一步", "繼續", "CONTINUE", "Continue"])
                page.wait_for_timeout(2500)
                break
            page.wait_for_timeout(2000)

        # ---------- 4) 第4步：确认 ----------
        G.dump(page, WHO, "resume_confirm")
        state = captcha.guard(page, WHO, say=log,
                              on_captcha=lambda pg, info, note="": None)
        if state == "timeout":
            N.failed("resume：确认前验证码超时")
            return
        G.click_text(page, ["确认", "確認", "递交", "遞交", "提交", "确定", "確定",
                            "CONFIRM", "Confirm", "SUBMIT", "Submit"])
        page.wait_for_timeout(3000)

        # ---------- 5) OTP ----------
        otp_t0 = time.time()
        for _ in range(180):
            code = G.read_otp()
            if code:
                G.dump(page, WHO, "resume_otp_page")
                data = page.evaluate(G.DUMP_JS)
                for f in data["fields"]:
                    if f["type"] in ("text", "tel", "number", "") and (
                            "密码" in f["label"] + f["ctx"]
                            or "OTP" in (f["label"] + f["ctx"]).upper()):
                        sel = f"input[name='{f['name']}'], #{f['id']}"
                        try:
                            page.query_selector(sel).fill(code)
                            log("已填入 OTP")
                        except Exception:
                            continue
                captcha.guard(page, WHO, say=log, on_captcha=lambda *x: None)
                G.click_text(page, ["继续", "確認", "确认", "提交", "验证",
                                    "CONTINUE", "Continue", "VERIFY", "Verify", "SUBMIT"])
                page.wait_for_timeout(3000)
                break
            if time.time() - otp_t0 > 360:
                log("等 OTP 超时")
                break
            page.wait_for_timeout(2000)

        G.dump(page, WHO, "resume_result")
        txt = body(page)
        ok = any(k in txt for k in ("确认编号", "预约编号", "已成功", "确认通知",
                                    "Confirmation", "Reference"))
        log("★★★ 成功" if ok else "走完流程，需人工确认结果")
        if ok:
            N.success("见页面")
        else:
            N.failed("resume：走完流程但未确认成功")
        log(f"结束，当前页 {page.url[:120]}")


if __name__ == "__main__":
    main()
