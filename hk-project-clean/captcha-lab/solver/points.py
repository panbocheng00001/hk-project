"""
点选 / 图块拖拽匹配类验证码：检测 + 匹配 + 排序输出。

典型形态：
  A. 文字点选：题面"依次点击 苹果 香蕉"，图上 6~8 个图标，按提示顺序点击
  B. 图标点选：题面给 3 张缩略图，图上找对应位置并依次点击
  C. 拖拽归位：图上有若干"碎片"和若干"空槽"，把碎片拖到外观一致的槽里

技术路线（工程上最稳的是 YOLO + 分类头，而不是纯模板匹配）：
  1) 候选框检测：YOLOv8n-cls/det，类目=图标种类；或先用通用分割(SAM)拿候选
  2) 题面-候选匹配：CLIP 做图文相似度（无监督冷启动）/ 分类头（有标注后）
  3) 排序输出归一化坐标（0~1），交平台或直接点击
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    label: str = ""
    score: float = 0.0
    emb: Optional[np.ndarray] = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def norm(self, W: int, H: int) -> Tuple[float, float]:
        return round(self.cx / W, 4), round(self.cy / H, 4)


# --------------------------------------------------------------------------- #
# 1. 通用候选框检测（无模型时的兜底）
# --------------------------------------------------------------------------- #
def detect_icons_classic(img: np.ndarray,
                         min_area_ratio: float = 0.004,
                         max_area_ratio: float = 0.12,
                         bg_tol: int = 28) -> List[Box]:
    """经典 CV 兜底：背景色差分 + 轮廓。适合背景纯色/渐变的图标题。"""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 用四角像素估计背景色
    corners = np.concatenate([hsv[:8, :8].reshape(-1, 3), hsv[:8, -8:].reshape(-1, 3),
                              hsv[-8:, :8].reshape(-1, 3), hsv[-8:, -8:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    dist = np.linalg.norm(hsv.astype(np.float32) - bg, axis=2).astype(np.uint8)
    _, th = cv2.threshold(dist, bg_tol, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), 2)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), 1)

    out: List[Box] = []
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area_r = (w * h) / (W * H)
        if not (min_area_ratio <= area_r <= max_area_ratio):
            continue
        if not (0.4 <= w / max(h, 1) <= 2.5):
            continue
        fill = cv2.contourArea(c) / max(w * h, 1)
        if fill < 0.35:
            continue
        out.append(Box(x, y, w, h, score=float(fill)))
    return out


# --------------------------------------------------------------------------- #
# 2. YOLO 检测（有数据集后的主力方案）
# --------------------------------------------------------------------------- #
class YoloIconDetector:
    """
    ultralytics YOLOv8/YOLO11 封装。
    训练：见 train_yolo.py，标注格式 YOLO txt（class cx cy w h 归一化）。
    """

    def __init__(self, weights: str = "runs/detect/train/weights/best.pt",
                 conf: float = 0.35, iou: float = 0.45, imgsz: int = 640):
        from ultralytics import YOLO  # 延迟导入
        self.model = YOLO(weights)
        self.conf, self.iou, self.imgsz = conf, iou, imgsz
        self.names = self.model.names

    def __call__(self, img: np.ndarray) -> List[Box]:
        r = self.model.predict(img, conf=self.conf, iou=self.iou,
                               imgsz=self.imgsz, verbose=False)[0]
        out: List[Box] = []
        for b in r.boxes:
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            out.append(Box(int(x1), int(y1), int(x2 - x1), int(y2 - y1),
                           label=self.names[int(b.cls)], score=float(b.conf)))
        return out


# --------------------------------------------------------------------------- #
# 3. CLIP 图文匹配（零标注冷启动：题面"苹果" -> 图上哪个是苹果）
# --------------------------------------------------------------------------- #
class ClipMatcher:
    def __init__(self, model_name: str = "ViT-B-32", pretrained: str = "openai"):
        import open_clip
        import torch
        self.torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained)
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.model.eval()

    def _crop(self, img: np.ndarray, b: Box) -> "object":
        from PIL import Image
        roi = img[max(0, b.y):b.y + b.h, max(0, b.x):b.x + b.w]
        roi = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        return self.preprocess(Image.fromarray(roi)).unsqueeze(0)

    def rank(self, img: np.ndarray, boxes: List[Box],
             prompts: Sequence[str]) -> np.ndarray:
        """返回 [len(boxes), len(prompts)] 的相似度矩阵（softmax 后）。"""
        torch = self.torch
        if not boxes:
            return np.zeros((0, len(prompts)))
        imgs = torch.cat([self._crop(img, b) for b in boxes])
        txt = self.tokenizer([f"a photo of {p}" for p in prompts])
        with torch.no_grad():
            ie = self.model.encode_image(imgs)
            te = self.model.encode_text(txt)
            ie = ie / ie.norm(dim=-1, keepdim=True)
            te = te / te.norm(dim=-1, keepdim=True)
            logits = (100.0 * ie @ te.T).softmax(dim=-1)
        return logits.cpu().numpy()


# --------------------------------------------------------------------------- #
# 4. 排序 & 输出
# --------------------------------------------------------------------------- #
def order_by_prompts(img: np.ndarray, boxes: List[Box],
                     prompts: Sequence[str]) -> List[Tuple[float, float, str, float]]:
    """
    题面顺序匹配：第 i 个提示词 -> 相似度最高的框。
    返回 [(nx, ny, prompt, conf), ...]，坐标已归一化。
    """
    H, W = img.shape[:2]
    if not boxes:
        return []
    m = ClipMatcher().rank(img, boxes, prompts)
    used: set[int] = set()
    res = []
    for pi, p in enumerate(prompts):
        order = np.argsort(m[:, pi])[::-1]
        for bi in order:
            if int(bi) in used:
                continue
            used.add(int(bi))
            res.append((*boxes[int(bi)].norm(W, H), p, float(m[int(bi), pi])))
            break
    return res


def order_by_position(boxes: List[Box], W: int, H: int,
                      mode: str = "left_right") -> List[Tuple[float, float]]:
    """纯位置顺序（题面不指定对象、只说"依次点击"时）。"""
    key = {
        "left_right": lambda b: (b.cx, b.cy),
        "top_bottom": lambda b: (b.cy, b.cx),
        "reading": lambda b: (round(b.y / max(1, H // 3)), b.x),
    }[mode]
    return [b.norm(W, H) for b in sorted(boxes, key=key)]


# --------------------------------------------------------------------------- #
# 5. 拖拽归位（碎片 -> 槽位）
# --------------------------------------------------------------------------- #
def match_pieces_to_slots(img: np.ndarray,
                          pieces: List[Box],
                          slots: List[Box],
                          method: str = "orb") -> List[Tuple[Box, Box, float]]:
    """
    把碎片和空槽配对，输出 (from_box, to_box, sim)。
    ORB 特征 + 形状/颜色直方图混合；碎片通常有透明边或描边，也可用 CLIP。
    """
    out = []
    for p in pieces:
        pc = img[max(0, p.y):p.y + p.h, max(0, p.x):p.x + p.w]
        if pc.size == 0:
            continue
        ph = cv2.calcHist([pc], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(ph, ph)
        best, best_s = None, -1.0
        for s in slots:
            sc = img[max(0, s.y):s.y + s.h, max(0, s.x):s.x + s.w]
            if sc.size == 0:
                continue
            sh = cv2.calcHist([sc], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(sh, sh)
            sim = cv2.compareHist(ph, sh, cv2.HISTCMP_CORREL)
            # 形状相似度补强
            ar_p, ar_s = p.w / max(p.h, 1), s.w / max(s.h, 1)
            sim += 0.3 * (1 - min(abs(ar_p - ar_s), 1.0))
            if sim > best_s:
                best, best_s = s, sim
        if best is not None:
            out.append((p, best, float(best_s)))
    return out


if __name__ == "__main__":
    import sys
    img = cv2.imdecode(np.fromfile(sys.argv[1], np.uint8), cv2.IMREAD_COLOR)
    boxes = detect_icons_classic(img)
    print(f"detected {len(boxes)} icons")
    for b in boxes:
        print(" ", b)
