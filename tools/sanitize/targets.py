# -*- coding: utf-8 -*-
"""targets.py —— 占位域名 ↔ 真实域名的可逆映射

为什么需要
----------
清洗把真实站点域名换成了 `example.test` 占位符，这样仓库本身不含目标信息。
但你本地真要跑的时候，代码必须连真实地址 —— 否则 DNS 都解析不了。

所以提供两个方向：

    --export    从词表导出映射文件（占位 → 真实），存到 _private/ 下
    --restore   把目标树里的占位符换回真实域名（本地运行前执行一次）
    --strip     反向操作，把真实域名换回占位符（准备分享/提交前执行）

`--restore` 之后的树**不要提交、不要分享**：它含真实目标地址。
`.gitignore` 拦不住已存在文件的内容变化，这一点只能靠人记住。

用法：
    python tools/sanitize/targets.py --export
    python tools/sanitize/targets.py --restore --dst hk-project-clean
    python tools/sanitize/targets.py --strip   --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lexicon import HOSTS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TEXT_EXT = {
    ".py", ".md", ".js", ".json", ".ps1", ".bat", ".cmd", ".html", ".txt",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", "", ".gitignore",
}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", "vendor", "runs"}

MAP_FILE = Path("_private") / "targets.local.json"


def load_map() -> list[tuple[str, str]]:
    """返回 [(真实, 占位), ...]，以词表为准。"""
    return [(real, ph) for real, ph in HOSTS if "." in real]


def export_map() -> int:
    pairs = load_map()
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "_说明": "占位域名 ↔ 真实域名映射。含真实目标地址，不要提交、不要分享。",
        "_用法": "python tools/sanitize/targets.py --restore 用它对本地树做还原",
        "map": [{"real": real, "placeholder": ph} for real, ph in pairs],
    }
    MAP_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {MAP_FILE}  ({len(pairs)} pairs)")
    for real, ph in pairs:
        print(f"  {ph:34} ← {real}")
    return 0


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_EXT and p.name not in {".gitignore"}:
            continue
        try:
            raw = p.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            yield p, raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def apply_map(root: Path, restore: bool) -> int:
    """restore=True：占位 → 真实；False：真实 → 占位。"""
    pairs = load_map()
    if restore:
        rules = [(ph, real) for real, ph in pairs]
        direction = "占位 → 真实"
    else:
        rules = [(real, ph) for real, ph in pairs]
        direction = "真实 → 占位"

    # 长的先替换，避免 td.gov.hk 先吃掉 abs3.td.gov.hk
    rules.sort(key=lambda kv: -len(kv[0]))

    touched = 0
    total = 0
    for p, text in _iter_files(root):
        orig = text
        n = 0
        for old, new in rules:
            if old in text:
                n += text.count(old)
                text = text.replace(old, new)
        if text != orig:
            with p.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            touched += 1
            total += n
            print(f"  {p.relative_to(root)}  ({n})")

    print(f"\n{direction}：{touched} 个文件，{total} 处")
    if restore and touched:
        print("\n⚠ 还原后的树含真实目标地址：不要提交，不要分享。")
        print("  要回到可分享状态，执行：python tools/sanitize/targets.py --strip --dst <目录>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="导出映射文件到 _private/")
    ap.add_argument("--restore", action="store_true", help="占位 → 真实（本地运行前）")
    ap.add_argument("--strip", action="store_true", help="真实 → 占位（分享前）")
    ap.add_argument("--dst", default="hk-project-clean")
    args = ap.parse_args()

    if args.export:
        return export_map()

    if not (args.restore or args.strip):
        ap.print_help()
        return 1

    root = (Path.cwd() / args.dst).resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}")
        return 2

    if args.restore and not MAP_FILE.exists():
        print(f"缺映射文件 {MAP_FILE}，先跑 --export")
        return 3

    return apply_map(root, restore=args.restore)


if __name__ == "__main__":
    sys.exit(main())
