"""
自建滑块验证码服务（FastAPI）
==============================

把 challenge.py（出题）和 trajectory.py（行为风控）组装成一个可部署的发题/验题服务。
服务端校验的几条硬规则，都是常见验证码被批量绕过的根因：

  1) 真值只存服务端，绝不下发（不放在 token、图片元数据、前端变量里）
  2) 每道题只能验一次，验完即焚 —— 杜绝"同一题反复试 x"的爆破
  3) 题目有 TTL，过期作废 —— 杜绝"先囤题、离线算好再交"
  4) 发题按客户端限速 —— 拉高批量刷题成本
  5) 位置正确 + 轨迹风险低，两者都满足才放行
  6) 验证通过后签发一次性 pass_token，业务后端用 /api/redeem 核销（同样只能用一次）

运行：
    uvicorn defense.service:app --reload
    打开 http://127.0.0.1:8000/          演示页（含真人轨迹录制，用于校准检测器）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import curve
from .challenge import PIECE_PAD, ChallengeConfig, encode_png, generate
from .robustness import CURVE_PRESETS, PRESETS
from .trajectory import score_risk

STATIC_DIR = Path(__file__).parent / "static"
KINDS = ("slider", "curve")


@dataclass
class _Pending:
    """一道待验的题。answer 只放可序列化的数据，换 Redis 时直接 json 存取。

    slider: {"gap_cx", "side", "tol"}       提交 x（滑块图左边缘）
    curve : {"target": [x, y], "path", "tol"}  提交 s（滑块值 0~1）
    """
    kind: str
    answer: dict
    expires_at: float


@dataclass
class _RateBucket:
    tokens: float
    updated: float


@dataclass
class ServiceSettings:
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    preset: str = "hardened"
    curve_preset: str = "curve_hardened"
    ttl_s: float = 120.0
    pass_ttl_s: float = 300.0
    risk_threshold: float = 0.5
    rate_per_min: float = 20.0          # 每个客户端每分钟最多发题数
    burst: float = 5.0
    record_path: Optional[Path] = None  # 设置后开启 /api/record（仅用于实验室采样）


class ChallengeStore:
    """线程安全的内存存储：待验题目、已签发通行证、限速桶。

    单进程演示足够；多实例部署时把这三张表换成 Redis（SET NX + EXPIRE 即可等价实现）。
    """

    def __init__(self, settings: ServiceSettings):
        self.s = settings
        self._lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._passes: Dict[str, float] = {}
        self._buckets: Dict[str, _RateBucket] = {}

    def allow(self, client: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        rate = self.s.rate_per_min / 60.0
        with self._lock:
            b = self._buckets.get(client)
            if b is None:
                b = self._buckets[client] = _RateBucket(self.s.burst, now)
            b.tokens = min(self.s.burst, b.tokens + (now - b.updated) * rate)
            b.updated = now
            if b.tokens < 1.0:
                return False
            b.tokens -= 1.0
            return True

    def put(self, cid: str, p: _Pending) -> None:
        with self._lock:
            self._gc()
            self._pending[cid] = p

    def take(self, cid: str) -> Optional[_Pending]:
        """取出即删除：同一题只能被验证一次。"""
        with self._lock:
            p = self._pending.pop(cid, None)
        if p is None or p.expires_at < time.monotonic():
            return None
        return p

    def issue_pass(self) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._passes[token] = time.monotonic() + self.s.pass_ttl_s
        return token

    def redeem(self, token: str) -> bool:
        with self._lock:
            exp = self._passes.pop(token, None)
        return exp is not None and exp >= time.monotonic()

    def _gc(self) -> None:
        now = time.monotonic()
        for cid in [k for k, v in self._pending.items() if v.expires_at < now]:
            del self._pending[cid]
        for t in [k for k, v in self._passes.items() if v < now]:
            del self._passes[t]


def _sign(secret: bytes, cid: str) -> str:
    mac = hmac.new(secret, cid.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{cid}.{mac}"


def _unsign(secret: bytes, token: str) -> Optional[str]:
    cid, _, mac = token.partition(".")
    if not cid or not mac:
        return None
    good = hmac.new(secret, cid.encode(), hashlib.sha256).hexdigest()[:16]
    return cid if hmac.compare_digest(mac, good) else None


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


class VerifyRequest(BaseModel):
    token: str
    x: Optional[float] = Field(None, description="直线拼图：滑块图左边缘在背景图像素坐标系中的 x")
    s: Optional[float] = Field(None, ge=0.0, le=1.0, description="曲线重合：滑块值 0~1")
    track: List[Tuple[float, float, float]] = Field(default_factory=list)


def _issue_slider(s: "ServiceSettings") -> Tuple[_Pending, dict]:
    cfg: ChallengeConfig = PRESETS[s.preset]
    ch = generate(cfg, seed=secrets.randbits(32))
    pending = _Pending("slider", {"gap_cx": ch.gap_cx, "side": ch.side,
                                  "tol": cfg.tolerance_px},
                       time.monotonic() + s.ttl_s)
    return pending, {
        "bg": _data_url(encode_png(ch.bg)),
        "piece": _data_url(encode_png(ch.piece)),
        "piece_y": ch.gap_y - PIECE_PAD,
        "width": cfg.width,
        "height": cfg.height,
    }


def _issue_curve(s: "ServiceSettings") -> Tuple[_Pending, dict]:
    cfg = CURVE_PRESETS[s.curve_preset]
    ch = curve.generate(cfg, seed=secrets.randbits(32))
    path = [[round(float(x), 2), round(float(y), 2)] for x, y in ch.path]
    pending = _Pending("curve", {"target": path[ch.target_idx], "path": path,
                                 "tol": cfg.tolerance_px},
                       time.monotonic() + s.ttl_s)
    # 路径必须下发（前端要实时渲染实心图形），但答案在路径上的哪个位置绝不下发
    return pending, {
        "bg": _data_url(encode_png(ch.bg)),
        "piece": _data_url(encode_png(ch.piece)),
        "path": path,
        "angle0": ch.angle0,
        "rotate_deg": cfg.rotate_deg,
        "width": cfg.width,
        "height": cfg.height,
        "prompt": "拖动滑块，让实心图形和空心图形完全重合",
    }


def _position_ok(p: _Pending, body: VerifyRequest) -> bool:
    a = p.answer
    if p.kind == "slider":
        if body.x is None:
            return False
        cx = body.x + PIECE_PAD + a["side"] / 2.0
        return abs(cx - a["gap_cx"]) <= a["tol"]
    if body.s is None:
        return False
    x, y = a["path"][curve.CurveChallenge.index_of(body.s)]
    tx, ty = a["target"]
    return ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5 <= a["tol"]


class RecordRequest(BaseModel):
    track: List[Tuple[float, float, float]]
    label: str = Field("human", pattern="^(human|bot)$")
    note: str = ""


class RedeemRequest(BaseModel):
    pass_token: str


def create_app(settings: Optional[ServiceSettings] = None) -> FastAPI:
    s = settings or ServiceSettings()
    if s.preset not in PRESETS:
        raise ValueError(f"unknown preset {s.preset!r}, choose from {list(PRESETS)}")
    if s.curve_preset not in CURVE_PRESETS:
        raise ValueError(f"unknown curve preset {s.curve_preset!r}, "
                         f"choose from {list(CURVE_PRESETS)}")
    store = ChallengeStore(s)
    app = FastAPI(title="captcha-lab defense service", version="1.0.0")
    app.state.store = store
    app.state.settings = s
    record_lock = threading.Lock()

    def _client(req: Request) -> str:
        return req.client.host if req.client else "unknown"

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/curve")
    def curve_page():
        return FileResponse(STATIC_DIR / "curve.html")

    @app.post("/api/challenge")
    def new_challenge(req: Request, kind: str = "slider"):
        if kind not in KINDS:
            raise HTTPException(400, f"kind must be one of {KINDS}")
        if not store.allow(_client(req)):
            raise HTTPException(429, "too many challenges, slow down")
        pending, public = (_issue_curve if kind == "curve" else _issue_slider)(s)
        cid = secrets.token_urlsafe(12)
        store.put(cid, pending)
        return {"token": _sign(s.secret, cid), "kind": kind, **public,
                "expires_in": s.ttl_s}

    @app.post("/api/verify")
    def verify(body: VerifyRequest):
        cid = _unsign(s.secret, body.token)
        if cid is None:
            raise HTTPException(400, "bad token")
        p = store.take(cid)
        if p is None:
            return {"ok": False, "reason": "expired_or_used"}

        position_ok = _position_ok(p, body)
        risk = score_risk(body.track, threshold=s.risk_threshold)

        if not position_ok:
            return {"ok": False, "reason": "position", "risk": round(risk.risk, 3)}
        if risk.is_bot:
            return {"ok": False, "reason": "behavior", "risk": round(risk.risk, 3),
                    "signals": risk.reasons}
        return {"ok": True, "pass_token": store.issue_pass(),
                "risk": round(risk.risk, 3)}

    @app.post("/api/redeem")
    def redeem(body: RedeemRequest):
        """业务后端核销通行证：只能成功一次，防止通行证被转卖/重放。"""
        return {"valid": store.redeem(body.pass_token)}

    @app.post("/api/record")
    def record(body: RecordRequest):
        """实验室采样：把轨迹连同标签追加到 JSONL，用于校准检测器。生产环境不要开。"""
        if s.record_path is None:
            raise HTTPException(404, "recording disabled")
        line = json.dumps({"label": body.label, "note": body.note,
                           "track": body.track, "ts": time.time()})
        with record_lock:
            s.record_path.parent.mkdir(parents=True, exist_ok=True)
            with s.record_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return {"saved": True}

    return app


def _settings_from_env() -> ServiceSettings:
    s = ServiceSettings()
    if os.environ.get("CAPTCHA_SECRET"):
        s.secret = os.environ["CAPTCHA_SECRET"].encode()
    s.preset = os.environ.get("CAPTCHA_PRESET", s.preset)
    s.curve_preset = os.environ.get("CAPTCHA_CURVE_PRESET", s.curve_preset)
    if os.environ.get("CAPTCHA_RECORD"):
        s.record_path = Path(os.environ["CAPTCHA_RECORD"])
    return s


app = create_app(_settings_from_env())
