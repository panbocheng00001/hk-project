# -*- coding: utf-8 -*-
"""prefill_check.py —— 抢号前的资料体检 + 速填卡
=====================================================================
放号后只有 20 分钟，填错一个字预约会被取消。这里在开抢前把每位申请人的
资料按官方规则过一遍，有问题当场改，而不是 17:00 才发现。

检查项（逐人）：
    · 占位符未替换（"[同事1姓名]" 之类）       → 该人跳过，不算错
    · 驾照号码：18 位 + GB 11643 校验位
    · 证件号码：往来港澳通行证 / 护照 的号码形态
    · 电邮：格式 + 一次性邮箱黑名单（官方不收临时邮箱）
    · 影像文件：存在、真实格式（看文件头不看后缀）、大小上限、
                文件名 ≤30 字且只含字母数字中文与 _ - . ( ) 空格
    · 多人之间证件号/驾照号/邮箱不能重复

用法：
    python prefill_check.py              # 体检全部申请人 + 打印速填卡
    python prefill_check.py --id w1      # 只看某一位
    python prefill_check.py --card-only  # 只打印速填卡
退出码：0=全部可用  1=有阻断性错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONF = BASE / "config.json"

JPEG_MAX = 5 * 1024 * 1024
PDF_MAX = 1 * 1024 * 1024
NAME_MAX = 30
NAME_OK = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff_\-.() ]+$")

DISPOSABLE = {
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
}

_GB_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_GB_CODES = "10X98765432"


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]")


def check_dl_no(dl: str) -> tuple[bool, str]:
    s = (dl or "").strip().upper()
    if not re.fullmatch(r"\d{17}[\dX]", s):
        return False, f"驾照号码应为 18 位（末位可为 X），当前「{dl}」"
    total = sum(int(c) * w for c, w in zip(s[:17], _GB_WEIGHTS))
    expect = _GB_CODES[total % 11]
    if s[17] != expect:
        return False, f"驾照号码校验位不对：末位应为 {expect}，当前 {s[17]}（多半是抄错一位）"
    return True, "18 位，校验位正确"


def check_doc_no(doc: str, doc_type: str) -> tuple[bool, str]:
    s = (doc or "").strip().upper()
    if not s:
        return False, "证件号码为空"
    t = doc_type or ""
    if "通行证" in t or "EEP" in t.upper():
        if re.fullmatch(r"[A-Z]{1,2}\d{7,8}", s):
            return True, "往来港澳通行证号码形态正常"
        return False, f"往来港澳通行证号码通常为 1–2 位字母 + 7–8 位数字，当前「{doc}」，请对照卡面"
    if "护照" in t or "PASSPORT" in t.upper():
        if re.fullmatch(r"[EGDSP][A-Z0-9]?\d{7,8}", s):
            return True, "护照号码形态正常"
        return False, f"护照号码通常为 E/G 开头共 9 位，当前「{doc}」，请对照护照"
    return True, "未识别证件类型，跳过形态检查（务必与上传影像同源）"


def check_email(email: str) -> tuple[bool, str]:
    s = (email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", s):
        return False, f"电邮格式不对「{email}」"
    domain = s.rsplit("@", 1)[1].lower()
    if domain in DISPOSABLE:
        return False, f"「{domain}」是一次性邮箱，官方不收，收不到 OTP"
    return True, "格式正常"


def _sniff(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "unknown"


def check_file(rel: str) -> tuple[bool, str]:
    if not rel:
        return False, "未填写影像文件路径"
    p = Path(rel)
    if not p.is_absolute():
        p = BASE / p
    if not p.exists():
        return False, f"文件不存在：{rel}"
    name = p.name
    if len(name) > NAME_MAX:
        return False, f"文件名 {len(name)} 字，超过 {NAME_MAX} 字上限：{name}"
    if not NAME_OK.match(name):
        return False, f"文件名含不允许的字符（只允许字母数字中文与 _ - . ( ) 空格）：{name}"
    kind = _sniff(p)
    size = p.stat().st_size
    mb = size / 1024 / 1024
    if kind == "jpeg":
        if size > JPEG_MAX:
            return False, f"JPEG {mb:.2f}MB，超过 5MB 上限：{name}"
        return True, f"JPEG {mb:.2f}MB"
    if kind == "pdf":
        if size > PDF_MAX:
            return False, f"PDF {mb:.2f}MB，超过 1MB 上限：{name}"
        return True, f"PDF {mb:.2f}MB"
    if kind == "png":
        return False, f"实际是 PNG（官方只收 JPEG/PDF），请另存为 JPG：{name}"
    return False, f"无法识别的文件格式（不是 JPEG/PDF）：{name}"


def inspect(ap: dict) -> dict:
    """返回 {id, skipped, errors[], oks[]}。"""
    aid = ap.get("id", "?")
    core = [ap.get(k) for k in ("name_cn", "doc_no", "dl_no", "email")]
    if any(_is_placeholder(v) for v in core):
        return {"id": aid, "skipped": True, "errors": [], "oks": []}

    errors: list[str] = []
    oks: list[str] = []

    def run(label, result):
        ok, msg = result
        (oks if ok else errors).append(f"{label}：{msg}")

    if not (ap.get("name_cn") or "").strip():
        errors.append("中文姓名：为空")
    run("驾照号码", check_dl_no(ap.get("dl_no", "")))
    run("证件号码", check_doc_no(ap.get("doc_no", ""), ap.get("doc_type", "")))
    run("电邮", check_email(ap.get("email", "")))
    run("身份证明文件影像", check_file(ap.get("doc_file", "")))
    run("驾驶执照影像", check_file(ap.get("dl_file", "")))
    return {"id": aid, "skipped": False, "errors": errors, "oks": oks}


def cross_duplicates(applicants: list[dict]) -> list[str]:
    """多人之间不能共用证件号/驾照号/邮箱。"""
    issues: list[str] = []
    for field, label in (("doc_no", "证件号码"), ("dl_no", "驾照号码"), ("email", "电邮")):
        seen: dict[str, str] = {}
        for ap in applicants:
            v = (ap.get(field) or "").strip().upper()
            if not v or _is_placeholder(v):
                continue
            if v in seen:
                issues.append(f"{label}重复：{seen[v]} 与 {ap.get('id', '?')} 都是「{v}」")
            else:
                seen[v] = ap.get("id", "?")
    return issues


def card(ap: dict, bureaus: list[str]) -> str:
    doc_type