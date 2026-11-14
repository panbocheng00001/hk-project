"""调用 HTTP 服务的客户端示例（服务需先启动）。

    python -m solver serve --port 8100
    python examples/service_client.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import cv2
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver.synth import make_sample  # noqa: E402

BASE = "http://127.0.0.1:8100"
API_KEY = "dev-key"          # 未设置 CAPTCHA_API_KEY 时可为空


def b64(img) -> str:
    return base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    bg, piece, _ = make_sample(seed=7)
    headers = {"X-API-Key": API_KEY} if API_KEY else {}

    with httpx.Client(timeout=10, headers=headers) as client:
        print("profiles:", client.get(f"{BASE}/v1/profiles").json()["sites"].keys())

        r = client.post(f"{BASE}/v1/solve", json={
            "bg": b64(bg), "piece": b64(piece),
            "display_width": bg.shape[1], "site": "generic",
        })
        r.raise_for_status()
        data = r.json()
        print(json.dumps(
            {k: data[k] for k in ("x", "distance", "confidence", "method")},
            ensure_ascii=False))

        t = client.post(f"{BASE}/v1/track", json={"distance": data["distance"],
                                                  "profile": "stealth"})
        print("轨迹风险分:", t.json()["features"]["risk"])


if __name__ == "__main__":
    main()
