"""
红队基线：曲线图形重合题的"轮廓对齐"扫描。

输入只用客户端本来就拿得到的东西（背景图、实心图形、路径采样点、旋转参数），
对路径上每个滑块位置，把实心图形的轮廓按当时的位置和角度摆上去，
统计轮廓落在背景强边缘上的程度，取对齐得最好的几个位置。

这是一个通用、无需训练的攻击，代表"知道路径"这一前提下的公开水平。
防守方用它衡量：路径上的假目标、同形异角、弱描边、干扰笔画分别能压低多少。
"""
from __future__ import annotations

import math
from typing import List

import cv2
import numpy as np


def _edge_map(bg: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    g = cv2.magnitude(gx, gy)
    g = cv2.dilate(g, np.ones((3, 3), np.uint8))   # 容忍 1px 的摆放误差
    return g / (float(g.max()) or 1.0)


def _contour_offsets(piece: np.ndarray) -> np.ndarray:
    """实心图形外轮廓点，相对图形中心的偏移 (K, 2)。"""
    mask = (piece[:, :, 3] > 127).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = max(cnts, key=cv2.contourArea)[:, 0, :].astype(np.float32)
    s = piece.shape[0]
    return pts - np.array([s / 2, s / 2], np.float32)


def contour_align(bg: np.ndarray, piece: np.ndarray, path: np.ndarray,
                  angle0: float = 0.0, rotate_deg: float = 0.0,
                  topk: int = 3) -> List[float]:
    """返回按对齐得分降序的滑块值 s 列表（已做非极大值抑制）。"""
    edge = _edge_map(bg)
    H, W = edge.shape
    offs = _contour_offsets(piece)
    n = len(path)
    scores = np.zeros(n, np.float32)
    for i in range(n):
        s = i / (n - 1)
        a = math.radians(angle0 + rotate_deg * s)          # 顺时针为正（屏幕坐标系）
        c, sn = math.cos(a), math.sin(a)
        rot = offs @ np.array([[c, sn], [-sn, c]], np.float32)
        p = rot + path[i]
        xs = np.clip(np.round(p[:, 0]).astype(int), 0, W - 1)
        ys = np.clip(np.round(p[:, 1]).astype(int), 0, H - 1)
        scores[i] = edge[ys, xs].mean()

    radius = piece.shape[0] * 0.6
    order = np.argsort(-scores)
    picked: List[int] = []
    for i in order:
        if all(np.hypot(*(path[i] - path[j])) >= radius for j in picked):
            picked.append(int(i))
            if len(picked) == topk:
                break
    return [i / (n - 1) for i in picked]
