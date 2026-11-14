# -*- coding: utf-8 -*-
"""mail_watch.py —— 盯 tdabs 的验证码邮件，抓到就写进 otp.txt
=====================================================================
grab.py 每 2 秒读一次 otp.txt，所以这条链路是：
    你的邮箱收到 OTP  →  本脚本提取数字  →  写入 otp.txt  →  grab.py 自动填入

三种收信方式，按 mail.json 里填了什么自动选：
  ① refresh_token（推荐，最快，不需要开 IMAP）
       {"email":"xxx@outlook.com","client_id":"...","refresh_token":"M.C..."}
  ② 密码 + IMAP（该号必须已开通 IMAP）
       {"email":"xxx@outlook.com","password":"..."}
  ③ 什么都不填 → 报错并提示

用法：
    python mail_watch.py                 # 盯一次（读最近 10 封）
    python mail_watch.py --loop 15       # 每 15 秒看一眼，直到抓到验证码
    python mail_watch.py --dry           # 只打印最近邮件标题，不提取、不写文件
    python mail_watch.py --test          # 只测试登录是否成功

抓到后会：写 otp.txt、响铃、在屏幕上大字打印。
"""
from __future__ import annotations

import argparse
import base64
import email
import imaplib
import json
import re
import sys
import time
from datetime import datetime
from email.header import decode_header
from pathlib import Path

import notifier as N

BASE = Path(__file__).resolve().parent
MAIL_CONF = BASE / "mail.json"
OTP_FILE = BASE / "otp.txt"

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"

# 验证码：4-8 位纯数字，前后不能紧贴字母数字；优先靠近 "密码/OTP/验证码" 字样
OTP_NEAR = re.compile(
    r"(?:一次性密碼|一次性密码|驗證碼|验证码|OTP|one[- ]time\s*password)[^0-9]{0,40}(\d{4,8})",
    re.IGNORECASE)
# 兜底只取 4–6 位，避免把 8 位流水号/日期（20261108）误当验证码
OTP_ANY = re.compile(r"(?<![0-9A-Za-z])(\d{4,6})(?![0-9A-Za-z])")
_YEARISH = re.compile(r"^(19|20)\d{2}$")


def beep(times=4):
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(1200, 180)
            time.sleep(0.08)
    except Exception:
        print("\a" * times, end="", flush=True)


def log(msg):
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def load_conf(who: str = "") -> dict:
    """多人模式：mail.json 里可以是 accounts 数组，每个账号带 who（w1/w2/...）。

    兼容旧的单账号结构（整个文件就是一个账号，视为 w1）。
    """
    if not MAIL_CONF.exists():
        sys.exit(f"缺少 {MAIL_CONF.name}。新建一个，内容示例：\n"
                 '{"accounts":[{"who":"w1","email":"xxx@outlook.com","client_id":"...","refresh_token":"M.C..."}]}')
    d = json.loads(MAIL_CONF.read_text(encoding="utf-8"))
    accts = d.get("accounts")
    if isinstance(accts, list) and accts:
        if who:
            for a in accts:
                if str(a.get("who", "")).strip() == who:
                    return a
            sys.exit(f"mail.json 里没有 who={who} 的账号（现有："
                     f"{', '.join(str(a.get('who')) for a in accts)}）")
        return accts[0]
    return d


def all_accounts() -> list[dict]:
    try:
        d = json.loads(MAIL_CONF.read_text(encoding="utf-8"))
    except Exception:
        return []
    accts = d.get("accounts")
    if isinstance(accts, list) and accts:
        return accts
    return [d]


def otp_path(conf: dict) -> Path:
    """每个窗口写自己的 otp_<who>.txt，避免四个人的验证码互相覆盖。"""
    who = str(conf.get("who", "") or "").strip()
    return BASE / (f"otp_{who}.txt" if who else "otp.txt")


# ---------------------------------------------------------------- 令牌
def save_conf(conf: dict) -> None:
    """只更新「这个账号」的字段，**不能**把整个 mail.json 覆盖掉。

    多人模式下 mail.json 是 {"accounts":[{who,email,...},...]}；
    早期实现直接 write_text(json.dumps(conf)) 会把数组结构冲掉（实测已发生一次）。
    """
    try:
        d = json.loads(MAIL_CONF.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    accts = d.get("accounts")
    if isinstance(accts, list) and accts:
        who = str(conf.get("who", "") or "").strip()
        merged = False
        for i, a in enumerate(accts):
            if who and str(a.get("who", "")).strip() == who:
                accts[i] = {**a, **conf}
                merged = True
                break
        if not merged:
            accts[0] = {**accts[0], **conf}
        d["accounts"] = accts
        MAIL_CONF.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        MAIL_CONF.write_text(json.dumps(conf, ensure_ascii=False, indent=1), encoding="utf-8")


def refresh_access_token(conf: dict) -> str:
    """用 refresh_token 换 access_token；微软会轮换 refresh_token，换到新的就写回。"""
    import urllib.error
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode({
        "client_id": conf["client_id"],
        "grant_type": "refresh_token",
        "refresh_token": conf["refresh_token"],
        "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            desc = json.loads(raw).get("error_description", raw)[:200]
        except Exception:
            desc = raw[:200]
        if "invalid_grant" in raw:
            sys.exit("refresh_token 已失效（invalid_grant）。\n"
                     "  · 这类批量号的令牌会被微软撤销，或在别处被刷新过而作废；\n"
                     "  · 到邮箱管理后台点「批量刷新令牌」，把新令牌填进 mail.json 再试；\n"
                     "  · 或者改用自己注册的邮箱。")
        sys.exit(f"换令牌失败：{desc}")
    # access_token 也持久化，这样重复启动不会每次都轮换 refresh_token
    conf["access_token"] = d["access_token"]
    conf["access_exp"] = time.time() + int(d.get("expires_in", 3600)) - 120
    new_rt = d.get("refresh_token")
    if new_rt and new_rt != conf.get("refresh_token"):
        conf["refresh_token"] = new_rt
        log("refresh_token 已轮换并写回 mail.json")
    save_conf(conf)
    return d["access_token"]


# ---------------------------------------------------------------- IMAP
_TOKEN = {"value": None, "ts": 0.0}


def get_access_token(conf: dict) -> str:
    """优先用 mail.json 里没过期的 access_token；没有才去刷新。
    频繁刷新会让 refresh_token 不断轮换，既有把号搞挂的风险，也会刷出一堆安全通知。"""
    tok = conf.get("access_token")
    exp = conf.get("access_exp") or 0
    if tok and exp - 120 > time.time():
        return tok
    return refresh_access_token(conf)


def imap_connect(conf: dict) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    if conf.get("refresh_token") and conf.get("client_id"):
        token = get_access_token(conf)
        auth = f"user={conf['email']}\x01auth=Bearer {token}\x01\x01".encode()
        try:
            m.authenticate("XOAUTH2", lambda _: auth)
        except imaplib.IMAP4.error:
            # 持久化的令牌可能被别处作废，强刷一次再试
            conf.pop("access_token", None)
            token = get_access_token(conf)
            auth = f"user={conf['email']}\x01auth=Bearer {token}\x01\x01".encode()
            m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
            m.authenticate("XOAUTH2", lambda _: auth)
    elif conf.get("password"):
        m.login(conf["email"], conf["password"])
    else:
        sys.exit("mail.json 里既没有 refresh_token/client_id，也没有 password")
    return m


def decode(s) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", "ignore"))
            except Exception:
                out.append(txt.decode("utf-8", "ignore"))
        else:
            out.append(txt)
    return "".join(out)


def fetch_recent(m: imaplib.IMAP4_SSL, limit=10):
    m.select("INBOX")
    typ, data = m.search(None, "ALL")
    if typ != "OK":
        return []
    ids = data[0].split()[-limit:]
    out = []
    for i in reversed(ids):
        typ, msg_data = m.fetch(i, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    try:
                        body += part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", "ignore")
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", "ignore")
            except Exception:
                body = str(msg.get_payload())
        out.append({
            "subject": decode(msg.get("Subject")),
            "from": decode(msg.get("From")),
            "date": msg.get("Date", ""),
            "body": body,
        })
    return out


def extract_otp(text: str, strict: bool = False) -> str | None:
    """strict=True 时只认「一次性密码/验证码/OTP」旁边那串，不做兜底猜。"""
    m = OTP_NEAR.search(text)
    if m:
        return m.group(1)
    if strict:
        return None
    for m in OTP_ANY.finditer(text):
        cand = m.group(1)
        if _YEARISH.match(cand):
            continue
        return cand
    return None


def is_tdabs_mail(frm: str, subj: str) -> bool:
    f, s = (frm or "").lower(), (subj or "").upper()
    return ("td.gov.hk" in f) or ("TDABS" in s) or ("预约" in (subj or ""))


# ---------------------------------------------------------------- 主循环
def once(conf, dry=False, test=False) -> str | None:
    m = imap_connect(conf)
    log(f"登录成功：{conf['email']}")
    if test:
        typ, boxes = m.list()
        log(f"邮箱文件夹数：{len(boxes or [])}")
        m.logout()
        return None
    mails = fetch_recent(m, limit=10)
    m.logout()
    log(f"最近 {len(mails)} 封邮件：")
    hit = None
    for i, mail in enumerate(mails, 1):
        subj = mail["subject"][:60]
        frm = mail["from"][:40]
        flag = "  ← tdabs" if "td.gov.hk" in frm.lower() else ""
        log(f"  {i:2}. [{frm}] {subj}{flag}")
        # 只从「tdabs / 预约"相关邮件里提取，别的邮件一律不碰，避免抓错数字
        if is_tdabs_mail(frm, subj):
            code = extract_otp(mail["subject"] + "\n" + mail["body"], strict=False)
            if code and not hit:
                hit = code
    if dry or not hit:
        return hit
    target = otp_path(conf)
    target.write_text(hit, encoding="utf-8")
    who = conf.get("who") or ""
    log(f"★★★ 验证码 {hit} 已写入 {target.name}，"
        f"{'窗口 ' + who + ' ' if who else ''}grab 会在 2 秒内填入 ★★★")
    beep()
    N.otp_arrived(hit)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="每 N 秒看一次（建议 10–20）")
    ap.add_argument("--dry", action="store_true", help="只列邮件，不提取、不写文件")
    ap.add_argument("--test", action="store_true", help="只测登录")
    ap.add_argument("--who", default="", help="只盯某个窗口的邮箱（w1/w2/w3/w4）")
    ap.add_argument("--all", action="store_true", help="同时盯所有账号（四个窗口一起抢时用这个）")
    ap.add_argument("--alert-after", type=int, default=180,
                    help="等多少秒还没抓到 OTP 就推钉钉（默认 180，0=不推）")
    a = ap.parse_args()

    if a.all:
        import threading
        targets = all_accounts()
        print(f"多人模式：同时监控 {len(targets)} 个邮箱")
        for t in targets:
            who = str(t.get("who", "") or "?")
            print(f"  - {who}: {t.get('email')}")
        threads = []
        for t in targets:
            who = str(t.get("who", "") or "")
            args = ["--loop", str(max(10, a.loop or 15)), "--alert-after", str(a.alert_after)]
            if who:
                args += ["--who", who]
            th = threading.Thread(
                target=lambda w=who, ar=args: __import__("subprocess").call(
                    [sys.executable, "-X", "utf8", __file__] + ar),
                daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        return

    conf = load_conf(a.who)
    print(f"监控账号：{conf.get('email')}（who={conf.get('who') or '-'}）")
    gap = max(5, a.loop) if a.loop else 0
    t_start = time.time()
    alerted = 0
    while True:
        try:
            code = once(conf, dry=a.dry, test=a.test)
        except SystemExit:
            raise
        except Exception as exc:
            log(f"异常 {type(exc).__name__}: {str(exc)[:160]}")
            code = None
        if a.test and code is None:
            return
        if code and not a.dry:
            return
        # 等太久还没有 OTP：推一条，明确写出是哪个邮箱、等了多久
        if a.alert_after and not a.dry:
            waited = int(time.time() - t_start)
            stage = waited // max(1, a.alert_after)
            if waited >= a.alert_after and stage > alerted and stage <= 5:
                alerted = stage
                log(f"⚠ 已等 {waited}s 仍未抓到 OTP（邮箱 {conf.get('email')}）")
                N.otp_missing(conf.get("email", "未配置"), waited)
        if not gap:
            return
        time.sleep(gap)


if __name__ == "__main__":
    main()
