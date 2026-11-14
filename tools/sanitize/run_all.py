# -*- coding: utf-8 -*-
"""run_all.py —— 一条命令跑完整个清洗流水线

阶段：
    1) audit        扫描原始树，产出 before 报告
    2) sanitize     归档 + 改名 + 文案替换 → 新树
    3) apply        结构性重写（验证码状态机、去 debugger、换文档）
    4) harden       能力默认值收敛（TLS 画像、证书校验、伪造头）
    5) verify       扫描 after、import 自检、跑测试

用法：
    python tools/sanitize/run_all.py
    python tools/sanitize/run_all.py --src hKdriver --dst hk-project-clean
    python tools/sanitize/run_all.py --skip-tests

任何阶段非 0 退出即停，不带着问题往后跑。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
REPORTS = TOOLS / "reports"


def run(step: str, args: list[str], cwd: Path | None = None) -> dict:
    print(f"\n{'=' * 70}\n[{step}] {' '.join(args)}\n{'=' * 70}")
    r = subprocess.run([sys.executable, *args], cwd=str(cwd or ROOT),
                       capture_output=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out:
        tail = out.splitlines()
        print("\n".join(tail[-30:]) if len(tail) > 30 else out)
    if r.returncode != 0:
        print(f"!! {step} failed (exit {r.returncode})")
        if err:
            print("\n".join(err.splitlines()[-15:]))
    return {"step": step, "exit": r.returncode, "stdout": out, "stderr": err}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="hKdriver")
    ap.add_argument("--dst", default="hk-project-clean")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)
    lab = ROOT / args.dst / "booking-lab"
    stages: list[dict] = []

    stages.append(run("1. audit(before)", [
        str(TOOLS / "scan.py"), args.src,
        "-o", str(REPORTS / "before.json"), "--top", "20"]))

    stages.append(run("2. sanitize", [
        str(TOOLS / "sanitize.py"), "--src", args.src, "--dst", args.dst, "--force"]))
    if stages[-1]["exit"] != 0:
        return finish(stages, 2)

    stages.append(run("3. apply", [str(TOOLS / "apply.py"), "--dst", args.dst]))
    if stages[-1]["exit"] != 0:
        return finish(stages, 3)

    stages.append(run("4. harden", [str(TOOLS / "harden.py"), "--dst", args.dst]))
    if stages[-1]["exit"] != 0:
        return finish(stages, 4)

    stages.append(run("4b. polish", [str(TOOLS / "polish.py"), "--dst", args.dst]))
    if stages[-1]["exit"] != 0:
        return finish(stages, 5)

    stages.append(run("5a. audit(after)", [
        str(TOOLS / "scan.py"), args.dst,
        "-o", str(REPORTS / "after.json"), "--top", "20"]))

    stages.append(run("5b. imports", [str(TOOLS / "check_imports.py"), str(lab)]))

    stages.append(run("5c. archive-manifest",
                      [str(TOOLS / "gen_manifest.py"), "--dst", args.dst]))

    if not args.skip_tests:
        stages.append(run("5d. tests", ["-m", "pytest", "captcha_flow/tests", "-q",
                                        "-p", "no:cacheprovider"], cwd=lab))

    cleanup(lab, stages)
    return finish(stages, 0)


# 自检会产生的临时产物（不属于交付物）
ARTIFACTS = ("__pycache__", ".pytest_cache", "runs", ".mypy_cache")
ARTIFACT_GLOBS = ("*.pyc", "*.log")


def cleanup(lab: Path, stages: list[dict]) -> None:
    """清掉自检过程产生的临时目录，避免它们混进交付树。"""
    removed = []
    for name in ARTIFACTS:
        for p in lab.rglob(name):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed.append(str(p.relative_to(lab)))
    for pattern in ARTIFACT_GLOBS:
        for p in lab.rglob(pattern):
            if p.is_file():
                p.unlink(missing_ok=True)
                removed.append(str(p.relative_to(lab)))
    if removed:
        print(f"\n[cleanup] 移除自检产物 {len(removed)} 项："
              f"{', '.join(removed[:6])}{' …' if len(removed) > 6 else ''}")
        stages.append({"step": "6. cleanup", "exit": 0, "stdout": "", "stderr": ""})


def finish(stages: list[dict], code: int) -> int:
    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for s in stages:
        mark = "OK  " if s["exit"] == 0 else f"FAIL({s['exit']})"
        print(f"  {mark}  {s['step']}")

    before = _load(REPORTS / "before.json")
    after = _load(REPORTS / "after.json")
    if before and after:
        print("\n风险命中对比：")
        print(f"  {'类别':<10}{'清洗前':>8}{'清洗后':>8}")
        keys = sorted(set(before.get("by_kind", {})) | set(after.get("by_kind", {})))
        for k in keys:
            b = before["by_kind"].get(k, 0)
            a = after["by_kind"].get(k, 0)
            print(f"  {k:<10}{b:>8}{a:>8}")
        print(f"  {'文件数':<10}{before['files_scanned_with_hits']:>8}"
              f"{after['files_scanned_with_hits']:>8}")
        print(f"  {'总命中':<10}{before['total_hits']:>8}{after['total_hits']:>8}")

    summary = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "stages": [{"step": s["step"], "exit": s["exit"]} for s in stages],
        "before": {k: before.get(k) for k in ("by_kind", "total_hits",
                                              "files_scanned_with_hits")} if before else None,
        "after": {k: after.get(k) for k in ("by_kind", "total_hits",
                                            "files_scanned_with_hits")} if after else None,
    }
    (REPORTS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n报告目录: {REPORTS}")
    return code


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
