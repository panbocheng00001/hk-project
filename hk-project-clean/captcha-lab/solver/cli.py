"""
命令行入口
==========

    python -m solver solve  --bg bg.png --piece piece.png --display-width 340
    python -m solver track  --distance 212 --profile stealth
    python -m solver bench  --n 200
    python -m solver profiles
    python -m solver serve  --host 0.0.0.0 --port 8100

设计约定：所有命令默认输出人类可读摘要，加 `--json` 输出机器可读结果（便于接 CI）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .engine import SolveError, SliderSolver
from .profiles import SITE_PROFILES
from .track import PROFILES, analyze, generate_track

__all__ = ["main"]


def _utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_solve(args: argparse.Namespace) -> int:
    solver = SliderSolver(site=args.site, track_profile=args.track_profile,
                          topk=args.topk, audit_track=not args.no_audit)
    try:
        result = solver.solve(
            args.bg, piece=args.piece, full_bg=args.full,
            display_width=args.display_width, slider_start_x=args.slider_start_x,
            seed=args.seed,
        )
    except (SolveError, FileNotFoundError, ValueError) as e:
        print(f"识别失败：{e}", file=sys.stderr)
        return 2

    if args.debug_out:
        solver.save_debug(args.bg, result, args.debug_out)

    if args.json:
        _print_json(result.to_dict())
        return 0

    print(f"缺口中心 x = {result.x:.1f}px（原图坐标，缩放 {result.scale:.3f}）")
    print(f"拖动距离   = {result.distance:.1f}px（口径 {result.answer.method} / "
          f"{result.meta.get('answer_mode')}）")
    print(f"置信度     = {result.confidence:.2%}（top1 得分 {result.answer.score:.2f}）")
    print(f"候选       = {len(result.answers)} 个，耗时 {result.elapsed_ms:.0f}ms")
    for i, a in enumerate(result.answers[:5]):
        print(f"  [{i}] x={a.x:.1f} dist={a.distance:.1f} conf={a.confidence:.2%} "
              f"method={a.method}")
    feats = analyze(result.track)
    print(f"轨迹       = {len(result.track)} 点 / {feats.get('duration_ms', 0):.0f}ms / "
          f"风险分 {feats.get('risk', 0):.3f}")
    if args.debug_out:
        print(f"标注图     -> {args.debug_out}")
    return 0


def cmd_track(args: argparse.Namespace) -> int:
    pts = generate_track(args.distance, profile=args.profile, seed=args.seed,
                         audit_risk=not args.no_audit)
    feats = analyze(pts)
    site = SITE_PROFILES[args.site]
    payload = site.build_payload(args.distance, pts)
    if args.json:
        _print_json({"track": pts, "features": feats, "payload": payload})
        return 0
    print(f"档案 {args.profile}: {len(pts)} 点 / {feats.get('duration_ms', 0):.0f}ms / "
          f"风险分 {feats.get('risk', 0):.3f}")
    print(f"句柄: x={payload.get(site.fields.get('distance', 'x'))} "
          f"passtime={payload.get('passtime')}")
    print("前 5 点:", pts[:5])
    print("后 3 点:", pts[-3:])
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """合成样本端到端评测：命中率、耗时、重试收益。"""
    import time

    from . import gap
    from .synth import make_sample

    solver = SliderSolver(site=args.site, track_profile=args.track_profile,
                          topk=args.topk, audit_track=False)
    n = int(args.n)
    hit1 = hit3 = 0
    t0 = time.perf_counter()
    for i in range(n):
        img, piece, (gx, gy, gw, gh) = make_sample(seed=i)
        gt = gx + gw / 2.0
        try:
            res = solver.solve(img, piece=piece, display_width=img.shape[1])
        except SolveError:
            continue
        d1 = abs(res.answers[0].x - gt)
        d3 = min(abs(a.x - gt) for a in res.answers)
        hit1 += d1 <= args.tolerance
        hit3 += d3 <= args.tolerance

    elapsed = time.perf_counter() - t0
    out = {
        "samples": n,
        "tolerance_px": args.tolerance,
        "hit@1": round(hit1 / n, 4),
        "hit@topk": round(hit3 / n, 4),
        "avg_ms": round(elapsed / n * 1000, 1),
        "site": args.site,
    }
    if args.json:
        _print_json(out)
    else:
        print(f"样本 {n}  容差 {args.tolerance}px  平均 {out['avg_ms']}ms/张")
        print(f"一次命中  {hit1}/{n} = {hit1 / n:.1%}")
        print(f"候选命中  {hit3}/{n} = {hit3 / n:.1%}（top{args.topk}）")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """目录批量识别，可选带标注直接出精度报表。"""
    import json as _json

    gt: Dict[str, Any] = {}
    if args.gt:
        gt = _json.loads(Path(args.gt).read_text(encoding="utf-8"))

    solver = SliderSolver(site=args.site, track_profile=args.track_profile,
                          topk=args.topk, audit_track=False)
    root = Path(args.dir)
    files = sorted(p for p in root.glob(args.pattern) if p.is_file())
    if args.piece_suffix:
        # 别把拼图块当成背景图重复识别
        files = [p for p in files if not p.stem.endswith(args.piece_suffix)]
    if not files:
        print(f"{root} 下没有匹配 {args.pattern} 的图片", file=sys.stderr)
        return 2

    rows: List[Dict[str, Any]] = []
    hit = 0
    t0 = time.perf_counter()
    for f in files:
        piece_path = f.with_name(f.stem + args.piece_suffix + f.suffix)
        piece = str(piece_path) if args.piece_suffix and piece_path.exists() else None
        row: Dict[str, Any] = {"file": f.name}
        try:
            r = solver.solve(str(f), piece=piece, display_width=args.display_width)
            row.update({"ok": True, "x": round(r.x, 2), "distance": round(r.distance, 2),
                        "confidence": round(r.confidence, 4)})
            label = gt.get(f.name) or gt.get(f.stem)
            if label is not None:
                cx = float(label["cx"] if isinstance(label, dict) else label)
                err = abs(r.x - cx)
                row["gt"] = cx
                row["error_px"] = round(err, 2)
                hit += err <= args.tolerance
        except Exception as e:                      # 单张失败不影响整批
            row.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        rows.append(row)

    elapsed = time.perf_counter() - t0
    total = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    summary = {
        "files": total,
        "solved": ok,
        "failed": total - ok,
        "avg_ms": round(elapsed / total * 1000, 1),
        "site": args.site,
    }
    if gt:
        labeled = sum(1 for r in rows if "error_px" in r)
        summary.update({
            "labeled": labeled,
            "tolerance_px": args.tolerance,
            "hit_rate": round(hit / labeled, 4) if labeled else None,
        })

    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    if args.json:
        _print_json({"summary": summary, "rows": rows})
    else:
        print(f"目录 {root}：{ok}/{total} 张识别成功，平均 {summary['avg_ms']}ms/张")
        if gt:
            print(f"标注 {summary['labeled']} 张，容差 {args.tolerance}px 命中率 "
                  f"{summary['hit_rate']:.1%}")
        bad = [r for r in rows if not r["ok"]]
        for r in bad[:10]:
            print(f"  失败 {r['file']}: {r['error']}")
        if args.out:
            print(f"报表 -> {args.out}")
    return 0 if ok == total else 1


def cmd_waf(args: argparse.Namespace) -> int:
    """几何重合滑块（画布实心/空心配对）：画布图 -> dx / slider_dx / 轨迹。"""
    from .overlap import analyze as analyze_canvas, draw_debug, solve_canvas

    out = solve_canvas(args.image, bmp_w=args.bmp_w,
                       track_w=args.track_w, handle_w=args.handle_w)
    if not out["ok"]:
        print(f"识别失败：{out.get('error')}", file=sys.stderr)
        return 2

    track = generate_track(out["slider_dx"], profile=args.track_profile,
                           seed=args.seed, audit_risk=not args.no_audit)
    feats = analyze(track)

    if args.debug_out:
        draw_debug(args.image, analyze_canvas(args.image), args.debug_out)

    if args.json:
        _print_json({"result": out, "track": track,
                     "features": {k: (round(v, 4) if isinstance(v, float) else v)
                                  for k, v in feats.items()}})
        return 0

    print(f"实心中心   = {out['current_x']:.1f}px    空心中心 = {out['target_x']:.1f}px")
    print(f"画布 dx    = {out['dx']:.2f}px（配对 {out['pairs']} 块，阈值档 {out['level']}）")
    print(f"滑轨位移   = {out['slider_dx']:.2f}px"
          f"（range = {args.track_w:.0f} - {args.handle_w:.0f}）")
    print(f"轨迹       = {len(track)} 点 / {feats.get('duration_ms', 0):.0f}ms / "
          f"风险分 {feats.get('risk', 0):.3f}")
    if args.debug_out:
        print(f"标注图     -> {args.debug_out}")
    return 0


def cmd_profiles(_: argparse.Namespace) -> int:
    print("站点档案：")
    for name, p in SITE_PROFILES.items():
        print(f"  {name:10s} answer_mode={p.answer_mode:6s} "
              f"track={p.track_format:8s} tol={p.tolerance_px}px")
    print("轨迹档案：")
    for name, p in PROFILES.items():
        print(f"  {name:10s} points={p.points} duration={p.duration_base:.0f}"
              f"+{p.duration_per_px}/px decimals={p.decimals}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("solver.serve:app", host=args.host, port=args.port, reload=args.reload)
    return 0


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="solver", description="captcha-lab：滑块验证码识别 / 轨迹 / 服务")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common_track = dict(default="mouse", help="轨迹档案：mouse/touch/fast/stealth")

    p = sub.add_parser("solve", help="识别一张图并生成提交载荷")
    p.add_argument("--bg", required=True, help="背景图路径 / base64")
    p.add_argument("--piece", help="拼图块小图")
    p.add_argument("--full", help="无缺口完整背景图（差分法）")
    p.add_argument("--display-width", type=float, help="前端渲染宽度（CSS px）")
    p.add_argument("--slider-start-x", type=float, default=0.0)
    p.add_argument("--site", default="generic")
    p.add_argument("--track-profile", default="mouse")
    p.add_argument("--topk", type=int, default=3)
    p.add_argument("--seed", type=int)
    p.add_argument("--no-audit", action="store_true", help="跳过轨迹风控自检")
    p.add_argument("--debug-out", help="标注图输出路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("track", help="只生成轨迹")
    p.add_argument("--distance", type=float, required=True)
    p.add_argument("--profile", **common_track)
    p.add_argument("--site", default="generic")
    p.add_argument("--seed", type=int)
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("bench", help="合成样本端到端评测")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--tolerance", type=int, default=4)
    p.add_argument("--site", default="generic")
    p.add_argument("--track-profile", default="mouse")
    p.add_argument("--topk", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("batch", help="目录批量识别（可带标注出精度报表）")
    p.add_argument("--dir", required=True)
    p.add_argument("--pattern", default="*.png")
    p.add_argument("--piece-suffix", default="_piece",
                   help="拼图块文件后缀约定：X.png + X_piece.png；留空表示不配对")
    p.add_argument("--gt", help="标注 JSON：{文件名: cx} 或 {文件名: {cx,cy,w,h}}")
    p.add_argument("--tolerance", type=int, default=4)
    p.add_argument("--display-width", type=float)
    p.add_argument("--site", default="generic")
    p.add_argument("--track-profile", default="mouse")
    p.add_argument("--topk", type=int, default=3)
    p.add_argument("--out", help="报表输出 JSON 路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("waf", help="几何重合滑块（画布实心/空心配对）：画布图 -> slider_dx + 轨迹")
    p.add_argument("--image", required=True, help="画布图（png 路径 / base64 / data-url）")
    p.add_argument("--bmp-w", type=float, help="画布位图宽度，默认取图像宽度")
    p.add_argument("--track-w", type=float, default=400.0, help="滑轨宽度（CSS px）")
    p.add_argument("--handle-w", type=float, default=34.0, help="手柄宽度（CSS px）")
    p.add_argument("--track-profile", default="stealth")
    p.add_argument("--seed", type=int)
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--debug-out")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_waf)

    p = sub.add_parser("profiles", help="列出站点 / 轨迹档案")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("serve", help="启动 HTTP 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    _utf8()
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
