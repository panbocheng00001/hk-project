"""合成训练数据 / 自测样例生成。

真实场景拿不到标注时，合成是冷启动的关键：
  背景：随机纹理 / 渐变 / 噪声 / 实拍图库抽样
  图标：抓取目标站点的图标素材（几十个类别就够），做随机 缩放/旋转/模糊/亮度/阴影
  缺口：矩形挖空 + 1~2px 灰白描边 + 右下 3~5px 深色阴影  <-- 这是识别缺口的核心特征
"""
from __future__ import annotations

import os
import random
from typing import List, Tuple

import cv2
import numpy as np


def random_background(w: int = 340, h: int = 212, seed: int | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    kind = rng.integers(0, 4)
    if kind == 0:                                    # 渐变
        c1, c2 = rng.integers(30, 225, 3), rng.integers(30, 225, 3)
        grad = np.linspace(0, 1, w)[None, :, None]
        img = (c1[None, None, :] * (1 - grad) + c2[None, None, :] * grad)
        img = np.repeat(img, h, axis=0)
    elif kind == 1:                                  # 噪声纹理
        base = rng.integers(60, 190, 3).astype(np.float32)
        img = base + rng.normal(0, 26, (h, w, 3))
    elif kind == 2:                                  # 色块
        img = np.zeros((h, w, 3), np.float32) + rng.integers(40, 200)
        for _ in range(rng.integers(3, 7)):
            x1, y1 = rng.integers(0, w), rng.integers(0, h)
            x2, y2 = x1 + rng.integers(30, 140), y1 + rng.integers(30, 140)
            img[y1:y2, x1:x2] = rng.integers(20, 240, 3)
    else:                                            # 条纹
        img = np.zeros((h, w, 3), np.float32) + 120
        step = int(rng.integers(6, 22))
        for x in range(0, w, step):
            img[:, x:x + step // 2] += rng.integers(-70, 70)
    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return img


def make_notch(bg: np.ndarray, side: int = 46, seed: int | None = None
               ) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """在背景上挖一个带描边+阴影的缺口，返回 (图, rect)。"""
    rng = np.random.default_rng(seed)
    h, w = bg.shape[:2]
    x = int(rng.integers(int(w * 0.28), max(int(w * 0.28) + 1, w - side - 6)))
    y = int(rng.integers(4, max(5, h - side - 4)))
    img = bg.copy()

    # 缺口内部略微变暗（模拟挖空/遮罩）
    roi = img[y:y + side, x:x + side].astype(np.float32) * 0.72
    img[y:y + side, x:x + side] = np.clip(roi, 0, 255).astype(np.uint8)

    # 1px 浅色描边（核心特征）
    cv2.rectangle(img, (x, y), (x + side, y + side), (238, 238, 238), 1)
    # 右侧 + 底部阴影
    cv2.rectangle(img, (x + side, y), (x + side + 3, y + side + 3), (40, 40, 40), -1)
    cv2.rectangle(img, (x, y + side), (x + side + 3, y + side + 3), (55, 55, 55), -1)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return img, (x, y, side, side)


def make_sample(w: int = 340, h: int = 212, side: int = 46, seed: int | None = None):
    """
    生成一组真实形态的样本：先定缺口位置 -> 从原图裁下该方块作为滑块图(piece)
    -> 再把该区域挖空/加描边阴影。这样 piece 与背景内容一致，模板匹配才有意义。
    返回 (带缺口背景, 带alpha的滑块图, (x,y,w,h))
    """
    rng = np.random.default_rng(seed)
    bg = random_background(w, h, seed=seed)
    x = int(rng.integers(int(w * 0.28), max(int(w * 0.28) + 1, w - side - 6)))
    y = int(rng.integers(4, max(5, h - side - 4)))

    # 1) 先裁真实图块
    patch = bg[y:y + side, x:x + side].copy()
    piece = np.zeros((side + 8, side + 8, 4), np.uint8)
    piece[4:4 + side, 4:4 + side, :3] = patch
    piece[4:4 + side, 4:4 + side, 3] = 255
    cv2.rectangle(piece, (4, 4), (4 + side, 4 + side), (250, 250, 250, 255), 1)

    # 2) 挖空 + 描边 + 阴影
    img = bg.copy()
    roi = img[y:y + side, x:x + side].astype(np.float32) * 0.72
    img[y:y + side, x:x + side] = np.clip(roi, 0, 255).astype(np.uint8)
    cv2.rectangle(img, (x, y), (x + side, y + side), (238, 238, 238), 1)
    cv2.rectangle(img, (x + side, y), (x + side + 3, y + side + 3), (40, 40, 40), -1)
    cv2.rectangle(img, (x, y + side), (x + side + 3, y + side + 3), (55, 55, 55), -1)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    return img, piece, (x, y, side, side)


def make_piece(side: int = 46, seed: int | None = None) -> np.ndarray:
    """生成带 alpha 的拼图块（圆角方形 + 高光边）。"""
    rng = np.random.default_rng(seed)
    s = side + 10
    piece = np.zeros((s, s, 4), np.uint8)
    color = tuple(int(v) for v in rng.integers(70, 210, 3))
    cv2.rectangle(piece, (0, 0), (side, side), (*color, 255), -1)
    cv2.rectangle(piece, (0, 0), (side, side), (250, 250, 250, 255), 2)
    # 左上高光
    cv2.line(piece, (2, 2), (side - 2, 2), (255, 255, 255, 255), 1)
    return piece


def build_dataset(out_dir: str, n: int = 300, classes: int = 8,
                  icons_dir: str | None = None) -> None:
    """生成 YOLO 格式数据集：images/labels train/val。"""
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    icons: List[np.ndarray] = []
    if icons_dir and os.path.isdir(icons_dir):
        for f in sorted(os.listdir(icons_dir))[:classes]:
            p = os.path.join(icons_dir, f)
            ic = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_UNCHANGED)
            if ic is not None:
                icons.append(ic)
    while len(icons) < classes:
        ic = make_piece(40, seed=len(icons))
        ic = ic[:, :, :3]
        icons.append(ic)

    for i in range(n):
        W, H = 340, 212
        bg = random_background(W, H, seed=i)
        labels = []
        # 随机放 4~7 个图标
        for _ in range(random.randint(4, 7)):
            ci = random.randrange(len(icons))
            ic = icons[ci]
            sc = random.uniform(0.55, 0.95)
            ic2 = cv2.resize(ic, None, fx=sc, fy=sc)
            ih, iw = ic2.shape[:2]
            if ih >= H - 8 or iw >= W - 8:
                continue
            x, y = random.randint(2, W - iw - 2), random.randint(2, H - ih - 2)
            ic2 = cv2.convertScaleAbs(ic2, alpha=random.uniform(0.8, 1.15),
                                      beta=random.uniform(-20, 20))
            bg[y:y + ih, x:x + iw] = ic2
            labels.append(f"{ci} {(x + iw/2)/W:.6f} {(y + ih/2)/H:.6f} {iw/W:.6f} {ih/H:.6f}")
        # 加噪声 & 轻微模糊，贴近真实截屏
        bg = cv2.GaussianBlur(bg, (3, 3), 0)
        split = "train" if i % 10 else "val"
        cv2.imwrite(os.path.join(out_dir, f"images/{split}/{i:05d}.jpg"), bg)
        with open(os.path.join(out_dir, f"labels/{split}/{i:05d}.txt"), "w") as f:
            f.write("\n".join(labels))

    with open(os.path.join(out_dir, "data.yaml"), "w") as f:
        f.write(f"path: {os.path.abspath(out_dir)}\n")
        f.write("train: images/train\nval: images/val\n")
        f.write(f"nc: {classes}\n")
        f.write("names: [" + ", ".join(f"c{k}" for k in range(classes)) + "]\n")
    print(f"dataset -> {out_dir}")


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "dataset"
    build_dataset(d, n=int(sys.argv[2]) if len(sys.argv) > 2 else 300)
