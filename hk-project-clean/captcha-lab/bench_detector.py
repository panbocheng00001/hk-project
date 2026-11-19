"""轨迹检测器评测与阈值校准。

四类样本：
  linear               : 匀速等间隔脚本（下限）
  human_track          : 旧版数学拟人轨迹（solver/track.py 的兼容接口）
  gen:<profile>        : 新版生成器（mouse/touch/fast/natural），带手颤与停顿
  human                : 来自演示页的真实录制（CAPTCHA_RECORD=data/recordings.jsonl
                         启动服务后，勾选"录制为真人样本"拖几十次即可）

指标：
  bot_catch  = 脚本被判为 bot 的比例（对防守方越高越好）
  human_fpr  = 真人被误判为 bot 的比例（越低越好，这是用户体验的代价）

新版生成器是**按这套规则反过来设计的**：它的捕获率低不代表检测器没用，
而是说明"规则型风控只能拦低成本脚本"，对抗性生成器需要更强的特征
（输入设备指纹、事件间隔分布、页面环境一致性）才能识别。

用法：
    python bench_detector.py                              # 只评测脚本侧
    python bench_detector.py data/recordings.jsonl        # 加上真人，并扫描阈值
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from defense.trajectory import linear_bot_track, score_risk
from solver.track import PROFILES, generate_track, human_track

Point = Tuple[float, float, float]


def bot_samples(n: int = 200, seed: int = 0) -> List[Tuple[str, List[Point]]]:
    rng = random.Random(seed)
    out: List[Tuple[str, List[Point]]] = []
    for i in range(n):
        d = rng.uniform(60, 280)
        if i % 4 == 0:
            out.append(("linear", linear_bot_track(d, steps=rng.randint(10, 60),
                                                   dt_ms=rng.choice([8, 10, 16, 20]))))
        elif i % 4 == 1:
            out.append(("human_track", human_track(
                d, overshoot_px=rng.uniform(0, 6),
                jitter_px=rng.uniform(0.5, 2.0), seed=i)))
        else:
            profile = list(PROFILES)[i % len(PROFILES)]
            out.append((f"gen:{profile}", generate_track(
                d, profile=profile, seed=i, audit_risk=False)))
    return out


def load_recordings(path: Path) -> List[List[Point]]:
    tracks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("label") == "human" and len(rec.get("track", [])) >= 3:
            tracks.append([tuple(p) for p in rec["track"]])
    return tracks


def rate(tracks: Sequence[Sequence[Point]], thr: float) -> float:
    if not tracks:
        return float("nan")
    return sum(score_risk(t, threshold=thr).is_bot for t in tracks) / len(tracks)


def main() -> None:
    bots = bot_samples()
    by_kind: dict = {}
    for kind, t in bots:
        by_kind.setdefault(kind, []).append(t)

    print(f"bot samples: {len(bots)}  (默认阈值 0.50)")
    for kind, ts in by_kind.items():
        print(f"  {kind:<12s} catch={rate(ts, 0.5):6.1%}  (n={len(ts)})")

    classic = [t for k, t in bots if k in ("linear", "human_track")]
    gen = [t for k, t in bots if k.startswith("gen:")]

    humans: List[List[Point]] = []
    if len(sys.argv) > 1:
        humans = load_recordings(Path(sys.argv[1]))
    if not humans:
        print("\n没有真人录制样本：human_fpr 无法评估，阈值未校准。")
        print(f"对抗性生成器（gen:*）在阈值 0.50 下捕获率："
              f"{rate(gen, 0.5):.1%} —— 规则型风控的已知盲区。")
        return

    print(f"\nhuman recordings: {len(humans)}")
    print(f"{'thr':>5s}  {'classic_catch':>13s}  {'gen_catch':>9s}  {'human_fpr':>9s}")
    best = None
    for thr in (0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8):
        bc, gc, fpr = rate(classic, thr), rate(gen, thr), rate(humans, thr)
        print(f"{thr:5.2f}  {bc:13.1%}  {gc:9.1%}  {fpr:9.1%}")
        if fpr <= 0.02 and (best is None or bc > best[1]):
            best = (thr, bc, fpr)
    if best:
        print(f"\n建议阈值 {best[0]:.2f}：classic_catch={best[1]:.1%}  "
              f"human_fpr={best[2]:.1%}（约束：真人误判 ≤2%）")
    else:
        print("\n没有阈值能把真人误判压到 2% 以下：需要调整规则权重或用 train_logistic。")


if __name__ == "__main__":
    main()
