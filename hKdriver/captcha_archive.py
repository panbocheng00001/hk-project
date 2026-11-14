# -*- coding: utf-8 -*-
"""captcha_archive.py —— 把验证码样本收成结构化样本库
=================================================================================
为什么需要：
    现在我们手里只有一堆 png（captcha_*.png / captcha_dbg_*.png / *_dpr2.png）和
    几个整页 HTML 快照。它们能看，但没法直接喂给「复刻服务」做回归测试 ——
    缺尺寸、缺 DPR、缺当时算出来的 dx、缺失败原因。

本脚本把它们归一成 **样本（specimen）**：

    runs/captcha_specimens/
        manifest.json                 索引：id / 类型 / 尺寸 / 哈希 / 来源
        spec_<id>.json                单个样本的完整 JSON（含 ground truth、几何、诊断）
        bitmaps/<id>.png              原始 canvas 位图（从 png 复制，改名便于对账）
        dom/<id>.html                 页面/组件 DOM（如果当时有存）

判定依据（都是实测过的）：
    · 位图尺寸 = canvas.width/height（dx 在这个坐标系里量）
    · CSS 尺寸与 DPR 决定「位图像素 → 滑块行程」的换算比例
    · ground_truth.dx 只在「算法当初算对」时可信；算错的样本标 verified=false

用法：
    python captcha_archive.py                 # 收全部历史产物
    python captcha_archive.py --list          # 只看索引
    python captcha_archive.py --verify <id>   # 用当前算法重算并存回结论
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
OUT = RUNS / "captcha_specimens"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def png_size(p: Path) -> tuple[int, int]:
    """不依赖 PIL 读 PNG 头（IHDR 固定在第 16..24 字节）。"""
    try:
        with p.open("rb") as fh:
            head = fh.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))
    except Exception:
        return (0, 0)


def classify(p: Path) -> dict:
    """按文件名反推它是什么：原始 canvas / 算法标注图 / DPR 变体 / 整页截图。"""
    n = p.name.lower()
    d = {"kind": "unknown", "note": ""}
    if n.startswith("captcha_dbg_"):
        d.update(kind="annotated", note="locate_shapes 画了绿/红框的调试图")
    elif n.startswith("captcha_") and re.match(r"captcha_\d{6}\.png", n):
        d.update(kind="canvas_bitmap", note="扩展 toDataURL 发来的原始 canvas")
    elif "dpr2" in n:
        d.update(kind="dpr_variant", note="DPR=2 环境下的同题截图")
    elif n.startswith("slider_"):
        d.update(kind="slider_shot", note="滑块调试截图")
    elif n.startswith("screen_"):
        d.update(kind="page_screenshot", note="整页截图")
    return d


def parse_ts(p: Path) -> str:
    m = re.search(r"(\d{6})", p.stem)
    if m:
        hm, ss = m.group(1)[:4], m.group(1)[4:]
        try:
            mt = p.stat().st_mtime
            return datetime.fromtimestamp(mt).strftime("%Y-%m-%dT") + f"{hm[:2]}:{hm[2:]}:{ss}"
        except Exception:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")


def build_specimen(p: Path) -> dict:
    w, h = png_size(p)
    meta = classify(p)
    sid = f"{meta['kind']}_{p.stem}"
    spec = {
        "schema": "captcha-specimen/1",
        "id": sid,
        "type": "waf_slider",                 # 目前实测只有这一种
        "source_file": p.name,
        "captured_at": parse_ts(p),
        "kind": meta["kind"],
        "note": meta["note"],
        "bitmap": {"file": f"bitmaps/{sid}.png", "w": w, "h": h, "sha256": sha256(p)},
        # 下面这些「运行时几何」历史产物里没有，先留空并标 unknown；
        # 新样本由扩展上报（见 content.js 的 captcha_spec 上报）。
        "geometry": {"css_w": None, "css_h": None, "dpr": None,
                     "track_w": None, "handle_w": None, "in_frame": None},
        "ground_truth": {"dx": None, "verified": False, "method": None},
        "solver": {"dx": None, "pairs": None, "level": None, "hue": None,
                   "tries": [], "verdict": None},
        "env": {"ua": None, "locale": None, "timezone": None, "url": None, "inst": None},
    }
    if meta["kind"] == "dpr_variant":
        spec["geometry"]["dpr"] = 2
    return spec


def collect() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bitmaps").mkdir(exist_ok=True)
    (OUT / "dom").mkdir(exist_ok=True)
    files = [p for p in RUNS.glob("*.png")
             if re.search(r"captcha|slider", p.name, re.I)]
    specs = []
    for p in sorted(files):
        spec = build_specimen(p)
        dst = OUT / spec["bitmap"]["file"]
        if not dst.exists():
            shutil.copy2(p, dst)
        specs.append(spec)

    # 再加上扩展实时上报的 live 样本（这类带完整几何/环境/判定，价值最高）
    for sp in sorted(OUT.glob("raw_*.json")):
        try:
            live = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            continue
        bmp = live.get("bitmap_file")
        bw = bh = 0
        if bmp and (OUT / bmp).exists():
            bw, bh = png_size(OUT / bmp)
        geo = live.get("geometry") or {}
        specs.append({
            "schema": "captcha-specimen/1",
            "id": live.get("id") or sp.stem,
            "type": live.get("type") or "waf_slider",
            "source_file": sp.name,
            "captured_at": datetime.fromtimestamp(sp.stat().st_mtime).isoformat(timespec="seconds"),
            "kind": "live",
            "note": "扩展实时上报：含几何 / 环境 / DOM / scan",
            "bitmap": {"file": bmp or "", "w": bw or geo.get("bmp_w") or 0,
                       "h": bh or geo.get("bmp_h") or 0,
                       "sha256": sha256(OUT / bmp) if bmp and (OUT / bmp).exists() else ""},
            "geometry": {
                "css_w": geo.get("css_w"), "css_h": geo.get("css_h"),
                "dpr": (live.get("env") or {}).get("dpr"),
                "track_w": geo.get("track_w"), "handle_w": geo.get("handle_w"),
                "in_frame": (live.get("env") or {}).get("in_frame"),
            },
            "ground_truth": {"dx": None, "verified": False, "method": None},
            "solver": {"dx": None, "pairs": None, "level": None, "hue": None,
                       "tries": [], "verdict": None},
            "env": {"ua": (live.get("env") or {}).get("ua"),
                    "locale": (live.get("env") or {}).get("locale"),
                    "timezone": (live.get("env") or {}).get("timezone"),
                    "url": live.get("url"), "inst": None},
        })
    return specs


def load_manifest() -> dict:
    mp = OUT / "manifest.json"
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schema": "captcha-manifest/1", "specimens": []}


def save(specs: list[dict]) -> None:
    saved = []
    for s in specs:
        (OUT / f"spec_{s['id']}.json").write_text(
            json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        saved.append({
            "id": s["id"], "type": s["type"], "kind": s["kind"],
            "w": s["bitmap"]["w"], "h": s["bitmap"]["h"],
            "sha256": s["bitmap"]["sha256"][:16],
            "captured_at": s["captured_at"],
            "dx": s["ground_truth"]["dx"], "verified": s["ground_truth"]["verified"],
        })
    man = {"schema": "captcha-manifest/1",
           "generated_at": datetime.now().isoformat(timespec="seconds"),
           "count": len(saved),
           "by_kind": {},
           "specimens": saved}
    for s in saved:
        man["by_kind"][s["kind"]] = man["by_kind"].get(s["kind"], 0) + 1
    (OUT / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")


def verify(sid: str) -> int:
    """用当前算法重算这张图的 dx，并存回样本（作为 ground truth 的候选）。"""
    import slider as SL
    spec_p = OUT / f"spec_{sid}.json"
    if not spec_p.exists():
        matches = list(OUT.glob(f"spec_*{sid}*.json"))
        if not matches:
            print(f"找不到样本 {sid}")
            return 1
        spec_p = matches[0]
    spec = json.loads(spec_p.read_text(encoding="utf-8"))
    bmp = OUT / spec["bitmap"]["file"]
    loc = SL.locate_shapes(str(bmp), debug_path=str(OUT / f"dbg_{sid}.png"))
    if loc and "dx" in loc:
        spec["solver"].update(dx=round(float(loc["dx"]), 2), pairs=loc.get("pairs"))
        spec["ground_truth"].update(dx=round(float(loc["dx"]), 2), verified=True,
                                    method="python-slider.locate_shapes")
        spec_p.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{sid}: dx={loc['dx']:.1f} shape={loc.get('shape')} 已写回")
        return 0
    print(f"{sid}: 算法没算出 dx（{loc}）")
    return 2


def cmd_list() -> int:
    man = load_manifest()
    if not man["specimens"]:
        print("还没有样本，先跑 python captcha_archive.py")
        return 1
    print(f"{'id':<34}{'kind':<15}{'尺寸':<12}{'dx':>8} {'verified'}")
    print("-" * 84)
    for s in man["specimens"]:
        dx = f"{s['dx']:.1f}" if isinstance(s.get("dx"), (int, float)) else "-"
        print(f"{s['id']:<34}{s['kind']:<15}{str(s['w']) + 'x' + str(s['h']):<12}{dx:>8} {s['verified']}")
    print("-" * 84)
    print(f"共 {man['count']} 个样本，按类型：{man['by_kind']}")
    print(f"目录：{OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", default="", help="用当前算法重算某张图的 dx")
    ap.add_argument("--verify-all", action="store_true")
    a = ap.parse_args()
    if a.list:
        return cmd_list()
    if a.verify:
        return verify(a.verify)
    if a.verify_all:
        man = load_manifest()
        for s in man["specimens"]:
            verify(s["id"])
        return 0
    specs = collect()
    save(specs)
    print(f"已归档 {len(specs)} 个样本 → {OUT}")
    return cmd_list()


if __name__ == "__main__":
    sys.exit(main())
