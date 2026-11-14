# -*- coding: utf-8 -*-
"""gen_manifest.py —— 生成 ARCHIVE_MANIFEST.md

这份清单是"为什么某些文件不在共享树里"的唯一解释来源。
没有它，接手的人只会看到一堆 import 失败和缺失目录，然后自己猜。

输入：reports/sanitize_run.json 里的 archive_manifest
输出：<dst>/booking-lab/ARCHIVE_MANIFEST.md

用法：
    python tools/sanitize/gen_manifest.py --dst hk-project-clean
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

GROUPS = {
    "credentials": ("凭据与会话状态", "真实账号/令牌，不应进入任何共享树"),
    "identity_materials": ("个人身份物料", "证件影像与个人资料，属于个人信息"),
    "identity_materials_docs": ("个人身份物料（资料目录）", "同上"),
    "config": ("本机配置", "含真实申请人与通知配置"),
    "captures": ("真实站点抓包与页面快照", "含站点内容与会话痕迹"),
    "logs": ("运行日志", "含出口地址与站点响应"),
    "legacy_docs": ("旧作业记录", "原始实测记录，仅本地保留"),
    "legacy_scripts": ("一次性脚本", "已执行完毕的补丁/探测脚本"),
    "unrelated": ("与项目无关的产物", "第三方压缩包等"),
    "capability_review/image_solver": (
        "能力收敛：图像求解",
        "整份文件的用途是识别验证码图像并推算位移，属于设计上排除的能力"),
    "capability_review/env_spoofing": (
        "能力收敛：环境参数对齐",
        "整份文件的用途是让请求伪装成某个浏览器的环境特征"),
    "capability_review/protocol_re": (
        "能力收敛：协议分析",
        "整份文件的用途是追溯/重放站点内部协议与签名"),
    "capability_review/egress_rotation": (
        "能力收敛：出口轮换",
        "整份文件的用途是批量轮换网络出口"),
    "capability_review/bulk_accounts": (
        "能力收敛：批量账号",
        "整份文件的用途是批量维护通知邮箱凭据"),
    "capability_review/superseded": (
        "已被取代的实现",
        "新版逻辑已在 captcha_flow/，旧实现保留但移出共享树"),
}


def build(manifest: list[dict]) -> str:
    by_group: dict[str, list[dict]] = defaultdict(list)
    for item in manifest:
        # 归档目标形如 _private/archive/<group>/...
        parts = Path(item["to"]).parts
        group = "/".join(parts[2:-1]) if len(parts) > 3 else (parts[2] if len(parts) > 2 else "-")
        by_group[group].append(item)

    lines = [
        "# ARCHIVE_MANIFEST",
        "",
        "这份清单说明：哪些文件**不在**共享树里，它们在哪，以及为什么移出。",
        "",
        "共享树（`booking-lab/`）只保留能解释清楚用途的代码与文档。",
        "原始物料全部保留在 `_private/archive/`，没有删除。",
        "",
        f"共 {len(manifest)} 项。原文件名在归档目录中保持不变，",
        "本表只描述用途——需要定位具体文件时请直接看 `_private/archive/`。",
        "",
    ]

    for group in sorted(by_group):
        title, why = GROUPS.get(group, (group, "见具体条目"))
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"{why}。")
        lines.append("")
        lines.append("| 归档子目录 | 项数 | 说明 |")
        lines.append("|---|---|---|")
        reasons: dict[str, list[str]] = defaultdict(list)
        for item in by_group[group]:
            reasons[item["reason"]].append(item["from"])
        for reason, files in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"| `{group}/` | {len(files)} | {reason} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## 恢复方式",
        "",
        "需要某个被移出的能力时，从 `_private/archive/` 拷回对应文件即可。",
        "但要注意：`capability_review/` 下的模块与当前共享版本**存在接口差异**，",
        "直接拷回不一定能跑——它们的依赖模块（如 `slider`、`align_client`）",
        "也已移出，需要一并恢复。",
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
    ap.add_argument("--dst", default="hk-project-clean")
    args = ap.parse_args()

    root = Path.cwd()
    run_path = root / "tools" / "sanitize" / "reports" / "sanitize_run.json"
    if not run_path.is_file():
        print(f"missing {run_path}，先跑 sanitize.py")
        return 2

    data = json.loads(run_path.read_text(encoding="utf-8"))
    manifest = data.get("archive_manifest") or []
    text = build(manifest)

    out = root / args.dst / "booking-lab" / "ARCHIVE_MANIFEST.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out}  ({len(manifest)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
