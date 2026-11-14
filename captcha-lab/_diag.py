"""临时诊断脚本：检查形状提取质量。"""
import numpy as np
from solver.synth_match import make_board
from solver import shape_match as sm

for seed in (3, 7, 11):
    b = make_board(seed=seed, n=4, hard=(seed % 2 == 1))
    kinds = b.meta["kinds"]
    print("=== seed", seed, kinds, b.truth)
    for tag, boxes in (("P", b.pieces), ("S", b.slots)):
        for i, box in enumerate(boxes):
            s = sm.extract(b.image, box, label=f"{tag}{i}")
            if s is None:
                print(f" {tag}{i} FAIL")
                continue
            kind = kinds[i] if tag == "S" else kinds[b.truth[i]]
            print(f" {tag}{i} {kind:9s} box={box} area={s.area:.0f} "
                  f"ar={s.ar:.2f} ext={s.extent:.2f} sol={s.solidity:.2f} "
                  f"cov={s.nmask.mean()/255:.3f}")
