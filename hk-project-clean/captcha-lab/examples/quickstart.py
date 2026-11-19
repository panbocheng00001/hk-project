"""离线快速开始：合成一张题图 -> 识别 -> 打印距离/轨迹/载荷。

    python examples/quickstart.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver import SliderSolver  # noqa: E402
from solver.synth import make_sample  # noqa: E402


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    bg, piece, (x, y, w, h) = make_sample(seed=11)
    solver = SliderSolver(site="geetest", track_profile="natural")

    result = solver.solve(bg, piece=piece, display_width=bg.shape[1])
    truth = x + w / 2

    print(f"真值中心   = {truth:.1f}px")
    print(f"识别中心   = {result.x:.1f}px（误差 {abs(result.x - truth):.1f}px）")
    print(f"提交距离   = {result.distance:.1f}px（口径 {result.meta['answer_mode']}）")
    print(f"置信度     = {result.confidence:.1%}")
    print(f"候选       = {[a.to_dict() for a in result.answers]}")
    print(f"轨迹       = {len(result.track)} 点，末点 {result.track[-1]}")
    print("载荷       =", json.dumps(
        {"x": result.payload["x"], "passtime": result.payload["passtime"],
         "track[:3]": result.payload["track"][:3]}, ensure_ascii=False))

    out = Path(__file__).resolve().parent / "quickstart_debug.png"
    solver.save_debug(bg, result, out)
    print(f"标注图     -> {out}")


if __name__ == "__main__":
    main()
