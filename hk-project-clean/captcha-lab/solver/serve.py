"""
HTTP 服务：把求解器变成可内网调用的 API
======================================

商用形态下，识别通常部署成一个独立服务：爬虫/自动化脚本只发图片、拿距离和轨迹，
不关心 OpenCV 版本和模型加载。这个模块就是那个最小可用服务。

    启动：python -m solver.serve --host 0.0.0.0 --port 8100
    文档：http://127.0.0.1:8100/docs

接口：

    POST /v1/solve      图片(base64/URL) -> {x, distance, confidence, track, payload}
    POST /v1/track      距离 -> 轨迹与提交载荷
    GET  /v1/profiles   可用的站点档案 / 轨迹档案
    GET  /healthz       存活探针
    GET  /metrics       调用量、成功率、耗时（Prometheus 文本格式）

鉴权与限流（都可选，靠环境变量开关）：

    CAPTCHA_API_KEY=key1,key2        设置后要求请求头 X-API-Key
    CAPTCHA_RATE_PER_MIN=120         每个 key 每分钟允许的请求数
    CAPTCHA_MAX_CONCURRENCY=8        同时在跑的识别任务上限
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .engine import SolveError, SliderSolver
from .profiles import SITE_PROFILES
from .track import PROFILES, generate_track, analyze

__all__ = ["app", "create_app"]

VERSION = "2.0.0"


# --------------------------------------------------------------------------- #
# 请求 / 响应模型
# --------------------------------------------------------------------------- #
class SolveRequest(BaseModel):
    bg: str = Field(..., description="背景图：base64、data-url、本地路径或 URL")
    piece: Optional[str] = Field(None, description="拼图块小图（可选）")
    full_bg: Optional[str] = Field(None, description="无缺口完整背景图（可选，最准）")
    display_width: Optional[float] = Field(None, description="前端渲染宽度（CSS px）")
    slider_start_x: float = Field(0.0, description="滑块手柄初始中心偏移")
    site: str = Field("generic", description="站点档案，见 /v1/profiles")
    track_profile: str = Field("mouse", description="轨迹档案：mouse/touch/fast/natural")
    topk: int = Field(3, ge=1, le=8)
    seed: Optional[int] = None
    debug_image: bool = Field(False, description="是否返回标注后的图片 base64")


class TrackRequest(BaseModel):
    distance: float = Field(..., ge=0)
    profile: str = Field("mouse")
    site: str = Field("generic")
    seed: Optional[int] = None
    audit: bool = Field(True, description="是否用行为风控自检")


# --------------------------------------------------------------------------- #
# 鉴权 / 限流 / 指标
# --------------------------------------------------------------------------- #
class ApiKeys:
    def __init__(self, keys: Optional[str] = None, rate_per_min: int = 120):
        self.keys = {k.strip() for k in (keys or "").split(",") if k.strip()}
        self.rate = max(1, int(rate_per_min))
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.keys)

    def check(self, key: Optional[str]) -> None:
        if not self.enabled:
            return
        if not key or key not in self.keys:
            raise HTTPException(status_code=401, detail="invalid api key")
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.rate:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            q.append(now)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0
        self.errors = 0
        self.rejected = 0
        self.latencies: List[float] = []

    def record(self, ms: float, error: bool = False) -> None:
        with self._lock:
            self.total += 1
            if error:
                self.errors += 1
            self.latencies.append(ms)
            if len(self.latencies) > 2000:
                del self.latencies[:1000]

    def reject(self) -> None:
        with self._lock:
            self.rejected += 1

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            lat = sorted(self.latencies)
            n = len(lat)

            def q(p: float) -> float:
                return lat[min(n - 1, int(p * n))] if n else 0.0

            return {
                "total": self.total,
                "errors": self.errors,
                "rejected": self.rejected,
                "p50_ms": q(0.50),
                "p95_ms": q(0.95),
                "p99_ms": q(0.99),
            }


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    app = FastAPI(
        title="captcha-lab solver API",
        version=VERSION,
        description="滑块验证码识别服务：图片进，拖动距离 / 轨迹 / 提交载荷出。",
    )

    keys = ApiKeys(os.getenv("CAPTCHA_API_KEY"), int(os.getenv("CAPTCHA_RATE_PER_MIN", "120")))
    metrics = Metrics()
    sem = threading.BoundedSemaphore(int(os.getenv("CAPTCHA_MAX_CONCURRENCY", "8")))
    cache: Dict[tuple, SliderSolver] = {}
    cache_lock = threading.Lock()

    def _solver(site: str, profile: str, topk: int) -> SliderSolver:
        key = (site, profile, topk)
        with cache_lock:
            if key not in cache:
                cache[key] = SliderSolver(site=site, track_profile=profile, topk=topk)
            return cache[key]

    def auth(x_api_key: Optional[str] = Header(default=None)) -> None:
        keys.check(x_api_key)

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"ok": True, "version": VERSION}

    @app.get("/v1/profiles")
    def profiles() -> Dict[str, Any]:
        return {
            "sites": {k: {"answer_mode": v.answer_mode,
                          "track_format": v.track_format,
                          "tolerance_px": v.tolerance_px}
                      for k, v in SITE_PROFILES.items()},
            "tracks": {k: {"points": list(v.points),
                           "duration_base": v.duration_base,
                           "decimals": v.decimals} for k, v in PROFILES.items()},
        }

    @app.post("/v1/solve", dependencies=[Depends(auth)])
    def solve(req: SolveRequest) -> JSONResponse:
        t0 = time.perf_counter()
        try:
            solver = _solver(req.site, req.track_profile, req.topk)
        except KeyError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if not sem.acquire(blocking=False):
            metrics.reject()
            raise HTTPException(status_code=503, detail="busy, retry later")
        try:
            result = solver.solve(
                req.bg, piece=req.piece, full_bg=req.full_bg,
                display_width=req.display_width, slider_start_x=req.slider_start_x,
                topk=req.topk, seed=req.seed,
            )
            body = result.to_dict()
            if req.debug_image:
                try:
                    body["debug_image"] = _b64_solver_image(solver, req.bg, result)
                except Exception:
                    body["debug_image"] = None
            ms = (time.perf_counter() - t0) * 1000
            metrics.record(ms)
            body["elapsed_ms"] = round(ms, 1)
            return JSONResponse(body)
        except SolveError as e:
            metrics.record((time.perf_counter() - t0) * 1000, error=True)
            raise HTTPException(status_code=422, detail=str(e))
        except FileNotFoundError as e:
            metrics.record((time.perf_counter() - t0) * 1000, error=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:                      # 未预料的解码/算法异常
            metrics.record((time.perf_counter() - t0) * 1000, error=True)
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
        finally:
            sem.release()

    @app.post("/v1/track", dependencies=[Depends(auth)])
    def track(req: TrackRequest) -> Dict[str, Any]:
        try:
            site = SITE_PROFILES[req.site]
        except KeyError:
            raise HTTPException(status_code=422, detail=f"unknown site {req.site!r}")
        pts = generate_track(req.distance, profile=req.profile, seed=req.seed,
                             audit_risk=req.audit)
        return {
            "payload": site.build_payload(req.distance, pts),
            "track": [[round(x, 2), round(y, 2), round(t, 1)] for x, y, t in pts],
            "features": {k: (round(v, 6) if isinstance(v, float) else v)
                         for k, v in analyze(pts).items()},
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint() -> str:
        s = metrics.snapshot()
        lines = [
            "# HELP captcha_solve_total 识别请求总数",
            "# TYPE captcha_solve_total counter",
            f"captcha_solve_total {s['total']}",
            f"captcha_solve_errors {s['errors']}",
            f"captcha_solve_rejected {s['rejected']}",
            "captcha_solve_latency_ms{quantile=\"0.5\"} "
            f"{s['p50_ms']:.1f}",
            "captcha_solve_latency_ms{quantile=\"0.95\"} "
            f"{s['p95_ms']:.1f}",
            "captcha_solve_latency_ms{quantile=\"0.99\"} "
            f"{s['p99_ms']:.1f}",
        ]
        return "\n".join(lines) + "\n"

    return app


def _b64_solver_image(solver: SliderSolver, bg: Any, result: Any) -> str:
    import cv2
    from .engine import encode_image_b64

    vis = solver.annotate(bg, result)
    return encode_image_b64(vis, ".jpg")


app = create_app()


def main() -> None:  # pragma: no cover - CLI 入口
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(description="captcha-lab solver API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    uvicorn.run("solver.serve:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":  # pragma: no cover
    main()
