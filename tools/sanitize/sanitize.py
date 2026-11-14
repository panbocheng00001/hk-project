# -*- coding: utf-8 -*-
"""sanitize.py —— 把项目清洗成可共享/可交给其他模型审查的形态

做四件事，一个命令跑完：
    1) 归档：真实身份物料、live 凭据、抓包、日志、一次性脚本 → _private/archive/
       （不是删除。原文都在，只是离开了共享树，并写下归档理由）
    2) 改名：hKdriver/ → booking-lab/，sniper.py → http_flow.py 等（消除"作战"语义命名）
    3) 替换：真实域名 → 占位域名；叙事物 → 工程述语；凭据 → 环境变量占位
    4) 重写：README / 安全模型 / 交接说明 由本脚本生成，不再手写

用法：
    python tools/sanitize/sanitize.py --src hKdriver --dst hk-project-clean
    python tools/sanitize/sanitize.py --src hKdriver --dst hk-project-clean --dry-run
    python tools/sanitize/sanitize.py --src hKdriver --dst hk-project-clean --keep-archive

回滚：目标目录是整个新建的，删掉即可；_private/archive/ 保留原始物料。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lexicon import (  # noqa: E402
    ARCHIVE, ARCHIVE_GLOBS, EXTRA_TEXT, HOSTS, IDENTIFIERS,
    PHRASES, PY_ONLY_IDENTIFIERS, RENAMES,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TEXT_EXT = {
    ".py", ".md", ".js", ".json", ".ps1", ".bat", ".cmd", ".html", ".txt",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".gitignore", ".iml", ".xml",
}
SKIP_EXT = {".pyc", ".pyo", ".exe", ".dll", ".pyd", ".so", ".png", ".jpg",
            ".jpeg", ".gif", ".webp", ".zip", ".docx", ".pdf", ".log", ".pem",
            ".pack", ".idx", ".bin"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "vendor", ".git", "runs", "cft",
             "profiles", "chrome_sys", ".idea"}

# 凭据 → 占位（在文本替换阶段最后执行，覆盖上面任何残留）
SECRET_SCRUBS: list[tuple[str, str]] = [
    (r"M\.C[0-9A-Za-z_\-]{3,}\.[A-Za-z0-9._~!*$'()\-]{40,}", "${MAIL_REFRESH_TOKEN}"),
    (r"Ew[A-Za-z0-9+/=]{80,}", "${MAIL_ACCESS_TOKEN}"),
    (r"access_token=[0-9a-f]{32,}", "access_token=${WEBHOOK_TOKEN}"),
    (r"SEC[0-9a-f]{40,}", "${WEBHOOK_SECRET}"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}:[^\s:\"']+:[^\s:\"']+", "${PROXY_URL_WITH_AUTH}"),
    (r"(trade_no|sign)=[0-9a-f]{16,}", r"\1=${PROXY_VENDOR_KEY}"),
    (r"\b\d{17}[\dXx]\b", "${DL_NO}"),
    (r"\b[CEG]\d{8}\b", "${DOC_NO}"),
    (r"\b1[3-9]\d{9}\b", "${PHONE}"),
    (r"\b[\w.+-]+@(?:outlook|gmail|163|qq|foxmail)\.[\w.]+\b", "${MAIL_ADDR}"),
    (r'"name_cn"\s*:\s*"(?![\[<])([^"\\]{2,8})"', '"name_cn": "${APPLICANT_NAME}"'),
]


class Stats:
    def __init__(self) -> None:
        self.archived: list[dict] = []
        self.renamed: list[tuple[str, str]] = []
        self.rewritten: dict[str, int] = {}
        self.copied = 0
        self.skipped = 0

    def hit(self, rel: str, n: int) -> None:
        if n:
            self.rewritten[rel] = self.rewritten.get(rel, 0) + n


def build_rules(py_file: bool) -> list[tuple[re.Pattern, str]]:
    rules: list[tuple[re.Pattern, str]] = []
    for pat, rep in HOSTS:
        rules.append((re.compile(re.escape(pat)), rep))
    for pat, rep in PHRASES:
        rules.append((re.compile(re.escape(pat)), rep))
    for pat, rep in EXTRA_TEXT:
        rules.append((re.compile(re.escape(pat)), rep))
    for pat, rep in IDENTIFIERS:
        rules.append((re.compile(pat), rep))
    if py_file:
        for pat, rep in PY_ONLY_IDENTIFIERS:
            rules.append((re.compile(pat), rep))
    for pat, rep in SECRET_SCRUBS:
        rules.append((re.compile(pat), rep))
    return rules


def should_skip(p: Path, src_root: Path) -> bool:
    rel_parts = p.relative_to(src_root).parts
    if any(part in SKIP_DIRS for part in rel_parts):
        return True
    return p.suffix.lower() in SKIP_EXT


def archive_target(src_root: Path, archive_root: Path, rel: str, sub: str) -> Path:
    """把 rel 放到 archive_root/sub/ 下，保持原文件名。"""
    return archive_root / sub / Path(rel).name


def plan_archive(src_root: Path) -> tuple[list[tuple[Path, str, str]], set[Path]]:
    """返回 (归档项, 被排除的路径集合)。"""
    items: list[tuple[Path, str, str]] = []
    excluded: set[Path] = set()
    for spec in ARCHIVE:
        if "src" not in spec:
            continue
        p = src_root / spec["src"]
        if p.exists():
            items.append((p, spec["dst"], spec["reason"]))
            excluded.add(p.resolve())
    for spec in ARCHIVE_GLOBS:
        for p in sorted(src_root.glob(spec["glob"])):
            if not p.is_file() or p.resolve() in excluded:
                continue
            # config.json 之类必须留在树里的，单独放过
            if p.name in {"config.example.json", "requirements.txt", "README.md"}:
                continue
            items.append((p, spec["dst"], spec["reason"]))
            excluded.add(p.resolve())
    return items, excluded


def do_archive(src_root: Path, archive_root: Path, items, dry: bool, stats: Stats) -> None:
    for src, sub, reason in items:
        dst = archive_target(src_root, archive_root, str(src.relative_to(src_root)), sub)
        stats.archived.append({
            "from": str(src.relative_to(src_root)),
            "to": str(dst.relative_to(archive_root.parent)),
            "reason": reason,
        })
        if dry:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def transform_text(text: str, rules) -> tuple[str, int]:
    """按规则顺序替换。用函数式替换，避免 replacement 里的 \\1 / $ 被当成模板。"""
    n = 0
    for rx, rep in rules:
        # 只有显式写了 \1 这类组引用的规则才走模板；其余按字面量替换
        if "\\1" in rep or "\\g<" in rep:
            text, k = rx.subn(rep, text)
        else:
            text, k = rx.subn(lambda _m, _r=rep: _r, text)
        n += k
    return text, n


def do_copy(src_root: Path, dst_root: Path, excluded: set[Path], dry: bool, stats: Stats) -> None:
    name_map = dict(RENAMES)

    def is_excluded(p: Path) -> bool:
        rp = p.resolve()
        if rp in excluded:
            return True
        # 目录级归档：其下所有文件都要排除
        for ex in excluded:
            try:
                rp.relative_to(ex)
                return True
            except ValueError:
                continue
        return False

    for p in sorted(src_root.rglob("*")):
        if not p.is_file():
            continue
        if is_excluded(p):
            stats.skipped += 1
            continue
        if should_skip(p, src_root):
            stats.skipped += 1
            continue

        rel = p.relative_to(src_root)
        # 目录名随 IDENTIFIERS 一起改（hKdriver 已在 dst 层处理）
        out_rel = Path(*[_rename_part(part) for part in rel.parts])
        out_rel = out_rel.parent / name_map.get(out_rel.name, out_rel.name)
        if out_rel.name != rel.name:
            stats.renamed.append((str(rel), str(out_rel)))

        dst = dst_root / out_rel
        if dry:
            stats.copied += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)

        if p.suffix.lower() in TEXT_EXT or p.name in {".gitignore"}:
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(p, dst)
                stats.copied += 1
                continue
            rules = build_rules(p.suffix.lower() == ".py")
            new, n = transform_text(text, rules)
            dst.write_text(new, encoding="utf-8", newline="\n")
            stats.hit(str(out_rel), n)
        else:
            shutil.copy2(p, dst)
        stats.copied += 1


PART_RULES = [
    (re.compile(r"\bhKdriver\b"), "booking-lab"),
    (re.compile(r"^waf_"), "edge_"),
    (re.compile(r"^waf\.py$"), "edge_challenge.py"),
]

FILE_RULES = {
    "waf.py": "edge_challenge.py",
}


def _rename_part(part: str) -> str:
    if part in FILE_RULES:
        return FILE_RULES[part]
    for rx, rep in PART_RULES:
        if rx.search(part):
            part = rx.sub(rep, part)
    return part


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="hKdriver")
    ap.add_argument("--dst", default="hk-project-clean")
    ap.add_argument("--archive", default="_private/archive")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="目标已存在时清空重建")
    ap.add_argument("--keep-archive", action="store_true",
                    help="不复制物料到归档（假定已归档过）")
    args = ap.parse_args()

    root = Path.cwd()
    src_root = (root / args.src).resolve()
    if not src_root.is_dir():
        print(f"src not found: {src_root}")
        return 2

    dst_root = (root / args.dst).resolve()
    lab = dst_root / "booking-lab"
    archive_root = (root / args.archive).resolve()

    if dst_root.exists():
        if not args.force:
            print(f"dst exists: {dst_root}  (use --force to rebuild)")
            return 2
        if not args.dry_run:
            shutil.rmtree(dst_root)

    stats = Stats()
    items, excluded = plan_archive(src_root)
    print(f"archive plan: {len(items)} entries")
    for it, sub, reason in items:
        print(f"   - {it.relative_to(src_root)}  →  {sub}   [{reason}]")

    if not args.dry_run:
        lab.mkdir(parents=True, exist_ok=True)
    if not args.keep_archive:
        do_archive(src_root, archive_root, items, args.dry_run, stats)

    do_copy(src_root, lab, excluded, args.dry_run, stats)

    print(f"\ncopied: {stats.copied}  skipped: {stats.skipped}  renamed: {len(stats.renamed)}")
    print(f"text files rewritten: {len(stats.rewritten)}  "
          f"substitutions: {sum(stats.rewritten.values())}")

    if not args.dry_run:
        report = {
            "source": str(src_root),
            "target": str(lab),
            "archive": str(archive_root),
            "copied": stats.copied,
            "skipped": stats.skipped,
            "renamed": stats.renamed,
            "archive_manifest": stats.archived,
            "rewritten_files": stats.rewritten,
        }
        rp = root / "tools" / "sanitize" / "reports" / "sanitize_run.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
