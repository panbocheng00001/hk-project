# -*- coding: utf-8 -*-
"""gen_manifest.py —— 生成共享树里的 booking-lab/ARCHIVE_MANIFEST.md

这份清单是"为什么某些文件不在共享树里"的唯一解释来源。没有它，接手的人
只会看到一堆 import 失败和缺失目录，然后自己猜。

与旧版的区别
------------
1. 不再列出原始文件名：清单只写"哪一类、多少项、为什么移出"。原始文件名
   留在 `_private/archive/` 里，共享树里不需要出现。
2. 项数直接数磁盘，不依赖 `sanitize_run.json`（那份报告的归档字段是按
   GBK 控制台写坏的，读出来是乱码）。
3. 说明文案只描述用途，不写立场与评价。

用法：
    python tools/sanitize/gen_manifest.py --archive _private/archive --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 顶层分组：(标题, 一句说明)
GROUPS: dict[str, tuple[str, str]] = {
    "credentials": ("凭据与会话状态", "真实账号、令牌与会话 cookie，不应进入任何共享树"),
    "identity_materials": ("个人身份物料", "证件影像与个人资料，属于个人信息"),
    "identity_materials_docs": ("个人身份物料（资料目录）", "同上"),
    "config": ("本机配置", "含申请人资料与通知配置"),
    "captures": ("抓包与页面快照", "含站点响应与会话痕迹"),
    "logs": ("运行日志", "含出口地址与站点响应"),
    "legacy_docs": ("旧作业记录", "原始实测记录，仅本地保留"),
    "legacy_scripts": ("一次性脚本", "已执行完毕的补丁与探测脚本"),
    "unrelated": ("与项目无关的产物", "第三方压缩包等"),
    "capability_review": ("能力收敛复核", "整份文件的用途落在设计上排除的能力里，见下表"),
}

# capability_review 下的子分组
SUBGROUPS: dict[str, tuple[str, str]] = {
    "image_solver": ("图像求解", "识别验证码图像并推算位移"),
    "env_spoofing": ("环境参数对齐", "按目标浏览器补齐 TLS/UA 环境特征"),
    "protocol_re": ("协议分析", "追溯或重放站点内部协议与签名"),
    "egress_rotation": ("出口轮换", "批量轮换网络出口"),
    "bulk_accounts": ("批量账号", "批量维护通知邮箱凭据"),
    "superseded": ("已被取代的实现", "新版逻辑已在 captcha_flow/，旧实现保留但移出共享树"),
}


def count_files(path: Path) -> int:
    """递归数普通文件；目录不存在时返回 0。"""
    if not path.is_dir():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def build(archive: Path) -> str:
    top: list[tuple[str, str, int]] = []
    for name, (title, why) in GROUPS.items():
        if name == "capability_review":
            continue
        n = count_files(archive / name)
        if n:
            top.append((title, why, n))

    sub: list[tuple[str, str, int]] = []
    cap_root = archive / "capability_review"
    for name, (title, why) in SUBGROUPS.items():
        n = count_files(cap_root / name)
        if n:
            sub.append((title, why, n))
    loose = count_files(cap_root) - sum(n for _, _, n in sub)
    if loose > 0:
        sub.append(("未分组", "复核目录根下的散装文件", loose))

    total = sum(n for _, _, n in top) + sum(n for _, _, n in sub)

    lines = [
        "# ARCHIVE_MANIFEST",
        "",
        "这份清单说明：哪些东西**不在**共享树里，它们在哪，以及为什么移出。",
        "",
        "共享树只保留能解释清楚用途的代码与文档。原始物料全部保留在",
        "`_private/archive/`，没有删除；清单只列类别与项数，不列原始文件名。",
        "",
        f"共 {total} 项。",
        "",
        "## 按类别",
        "",
        "| 类别 | 项数 | 为什么移出 |",
        "|---|---|---|",
    ]
    for title, why, n in top:
        lines.append(f"| {title} | {n} | {why} |")
    lines.append("")

    if sub:
        lines += [
            "## 能力收敛复核（`capability_review/`）",
            "",
            "这些文件的用途本身就是被设计排除的能力，改名解决不了，只能移出共享树。",
            "",
            "| 子类 | 项数 | 说明 |",
            "|---|---|---|",
        ]
        for title, why, n in sub:
            lines.append(f"| {title} | {n} | {why} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## 恢复方式",
        "",
        "需要某个被移出的能力时，从 `_private/archive/` 拷回对应文件即可。",
        "注意 `capability_review/` 下的模块与当前共享版本**存在接口差异**，",
        "直接拷回不一定能跑——它们的依赖模块也已移出，需要一并恢复。",
        "",
        "## 为什么不是删除",
        "",
        "删除会让人无法判断「当初为什么这么做」，也会让后续排障失去依据。",
        "移出 + 记录理由，既保持共享树干净，又不丢信息。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default=str(Path("_private") / "archive"),
                    help="归档根目录（默认 _private/archive）")
    ap.add_argument("--dst", default="hk-project-clean",
                    help="共享树目录（清单写到 <dst>/booking-lab/ARCHIVE_MANIFEST.md）")
    args = ap.parse_args()

    archive = Path(args.archive).resolve()
    if not archive.is_dir():
        print(f"归档目录不存在：{archive}")
        return 2

    out = Path(args.dst).resolve() / "booking-lab" / "ARCHIVE_MANIFEST.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = build(archive)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
