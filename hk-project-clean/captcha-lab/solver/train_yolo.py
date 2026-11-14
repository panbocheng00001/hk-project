"""YOLOv8 训练 / 评估 / 导出。

用法：
    python train_yolo.py --data dataset/data.yaml --model yolov8n.pt --epochs 120
    python train_yolo.py --export onnx          # 部署时用 onnxruntime 更快

数据量经验值：
    8 类点选图标，合成 300 张 + 真实标注 150 张 -> mAP50 0.93 左右，够用
    真实站点图标建议每类不少于 120 个实例，且必须包含"真实截屏"负样本
"""
from __future__ import annotations

import argparse


def train(data: str, model: str = "yolov8n.pt", epochs: int = 120,
          imgsz: int = 640, batch: int = 32, project: str = "runs") -> None:
    from ultralytics import YOLO
    m = YOLO(model)
    m.train(
        data=data, epochs=epochs, imgsz=imgsz, batch=batch, project=project,
        patience=25, optimizer="AdamW", lr0=1e-3, cos_lr=True,
        hsv_h=0.015, hsv_s=0.6, hsv_v=0.5,          # 验证码色彩扰动大，aug 要重
        degrees=12, translate=0.08, scale=0.35,
        mosaic=0.6, mixup=0.1, close_mosaic=15,
        cache=True, workers=4, verbose=True,
    )
    # 验证
    metrics = m.val()
    print(f"mAP50={metrics.box.map50:.4f} mAP50-95={metrics.box.map:.4f}")


def export(weights: str = "runs/detect/train/weights/best.pt", fmt: str = "onnx") -> None:
    from ultralytics import YOLO
    YOLO(weights).export(format=fmt, dynamic=True, opset=12, simplify=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="dataset/data.yaml")
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--export", default="")
    ap.add_argument("--weights", default="runs/detect/train/weights/best.pt")
    a = ap.parse_args()
    if a.export:
        export(a.weights, a.export)
    else:
        train(a.data, a.model, a.epochs, a.imgsz, a.batch)
