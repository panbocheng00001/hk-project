# -*- coding: utf-8 -*-
"""scan.py —— 扫描项目里会被其他模型的安全策略判定为敏感的文案与物料

用法：
    python tools/sanitize/scan.py <项目根> -o reports/before.json
    python tools/sanitize/scan.py <项目根> -o reports/after.json --fail-on-risk

输出两类东西：
    1) 控制台摘要：按类别统计命中数，列出风险最高的文件
    2) JSON 明细：每文件每类命中的具体位置（行号 + 片段），便于逐条处理

判据分四类：
    refusal   叙事物（抢号/过验证码/绕过/代理池/逆向…）—— 别的模型最先开火的地方
    secret    凭据（OAuth token、密码、webhook token、代理账号、cookie）
    pii       个人身份物料（证件号、姓名、邮箱、手机号、证件图片）
    host      真实目标域名硬编码
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lexicon import HOSTS, REFUSAL_FEATURES  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

TEXT_EXT = {
    ".py", ".md", ".js", ".ts", ".json", ".ps1", ".bat", ".cmd",
    ".html", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".gitignore", ".iml", ".xml", ".pem", "",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "vendor", ".idea", "dist", "build"}

# --- 凭据模式 -------------------------------------------------------------
SECRET_PATTERNS = [
    ("oauth_refresh", re.compile(r"M\.C[0-9A-Za-z_\-]{3,}\.[A-Za-z0-9._~!*$'()-]{40,}")),
    ("oauth_access", re.compile(r"Ew[A-Za-z0-9+/=]{80,}")),
    ("dingtalk_hook", re.compile(r"access_token=[0-9a-f]{32,}")),
    ("dingtalk_sign", re.compile(r"SEC[0-9a-f]{40,}")),
    ("proxy_cred", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}:[^\s:]+:[^\s:]+")),
    ("api_sign", re.compile(r"(?:trade_no|sign)=[0-9a-f]{16,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}")),
    ("cookie_blob", re.compile(r"\"(?:HWWAFSESID|JSESSIONID|HIK0kat3rt9xO)\"\s*:")),
    ("email_plain_pw", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}----\S{6,}----")),
]

# --- 个人身份物料 ---------------------------------------------------------
PII_PATTERNS = [
    ("cn_id_or_dl", re.compile(r"\b\d{17}[\dXx]\b")),
    ("cn_pass", re.compile(r"\b[CEG]\d{8}\b")),
    ("phone_cn", re.compile(r"\b1[3-9]\d{9}\b")),
    ("email", re.compile(r"\b[\w.+-]+@(?:outlook|gmail|163|qq|foxmail)\.[\w.]+\b")),
    ("cn_name_line", re.compile(r"\"name_cn\"\s*:\s*\"(?![\[<])([^\"]{2,8})\"")),
    ("id_photo_path", re.compile(r"(?:files|资料)[\\/][^\s\"']*(?:驾驶证|通行证|身份证)")),
]

# --- 真实目标 -------------------------------------------------------------
HOST_PATTERNS = [("host", re.compile(re.escape(src))) for src, _ in HOSTS if "." in src]


@dataclass
class Hit:
    kind: str
    pattern: str
    line: int
    snippet: str


@dataclass
class FileReport:
    path: str
    size: int
    hits: list[Hit] = field(default_factory=list)

    @property
    def score(self) -> int:
        weight = {"refusal": 3, "secret": 10, "pii": 8, "host": 2}
        return sum(weight.get(h.kind, 1) for h in self.hits)


def iter_text_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_EXT and p.name not in {".gitignore"}:
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        try:
            yield p, raw.decode("utf-8")
        except UnicodeDecodeError:
            continue


def scan_file(path: Path, text: str) -> FileReport:
    rep = FileReport(path=str(path), size=len(text))
    seen: set[tuple[str, int]] = set()

    def add(kind: str, pat: str, lineno: int, line: str):
        key = (kind, lineno)
        if key in seen:
            return
        seen.add(key)
        rep.hits.append(Hit(kind, pat, lineno, line.strip()[:160]))

    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        low = line
        for feat in REFUSAL_FEATURES:
            if feat in low:
                add("refusal", feat, i, line)
        for name, rx in SECRET_PATTERNS:
            if rx.search(line):
                add("secret", name, i, _mask(line))
        for name, rx in PII_PATTERNS:
            if rx.search(line):
                add("pii", name, i, _mask(line))
        for name, rx in HOST_PATTERNS:
            if rx.search(line):
                add("host", name, i, line)
    return rep


def _mask(line: str) -> str:
    """脱敏：把长串中间打掉，报告本身不再二次泄漏。"""
    def cut(m: re.Match) -> str:
        s = m.group(0)
        return s[:6] + "…" + s[-4:] if len(s) > 14 else s[:4] + "…"
    return re.sub(r"[A-Za-z0-9+/=_\-]{18,}", cut, line)


def scan_tree(root: Path) -> dict:
    reports = []
    for p, text in iter_text_files(root):
        rep = scan_file(p.relative_to(root), text)
        if rep.hits:
            reports.append(rep)
    reports.sort(key=lambda r: r.score, reverse=True)

    by_kind: dict[str, int] = {}
    by_pattern: dict[str, int] = {}
    for r in reports:
        for h in r.hits:
            by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
            by_pattern[h.pattern] = by_pattern.get(h.pattern, 0) + 1

    return {
        "root": str(root),
        "files_scanned_with_hits": len(reports),
        "total_hits": sum(len(r.hits) for r in reports),
        "by_kind": by_kind,
        "top_patterns": dict(sorted(by_pattern.items(), key=lambda kv: -kv[1])[:25]),
        "files": [
            {"path": r.path, "score": r.score, "n": len(r.hits),
             "hits": [asdict(h) for h in r.hits[:400]]}
            for r in reports
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--fail-on-risk", action="store_true",
                    help="命中 secret/pii 时退出码 2，用于 CI 卡口")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    result = scan_tree(root)

    print(f"root: {result['root']}")
    print(f"files with hits: {result['files_scanned_with_hits']}   total hits: {result['total_hits']}")
    print("by kind:", result["by_kind"])
    print("top patterns:")
    for k, v in result["top_patterns"].items():
        print(f"   {v:5d}  {k}")
    print(f"\ntop {args.top} files by risk score:")
    for f in result["files"][: args.top]:
        print(f"   {f['score']:6d}  {f['n']:4d}  {f['path']}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {out}")

    if args.fail_on_risk:
        bad = result["by_kind"].get("secret", 0) + result["by_kind"].get("pii", 0)
        if bad:
            print(f"\nFAIL: {bad} secret/pii hits remain")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
