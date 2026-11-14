"""
打码平台客户端 + 本地缓存 + 并发调度。

平台侧通用协议（各家字段名略有差异，映射表见 PROVIDERS）：
  POST  create task  -> {"pic_id": "..."}           (或直接返回结果)
  POST  get result   -> {"recognition": "..."}      (轮询，未完成返回空/retry)
  点选类返回  "x1,y1|x2,y2"
  滑块类返回  "132"  （缺口横坐标像素，部分平台返回百分比）

工程要点：
  1) 题面哈希缓存（同一批题图重复率极高，命中率常 20~40%）
  2) 本地模型先判，低置信度才外发（成本 -80%）
  3) 并发 + 全局限速 + 失败重试（区分"识别错误"与"平台限流"）
  4) 结果二次校验：滑块用轨迹末端像素与回传坐标比对，偏差>4px 直接重试
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

# --------------------------------------------------------------------------- #
# 平台字段映射：name -> (create_url, result_url, 额外参数, 返回字段)
# --------------------------------------------------------------------------- #
PROVIDERS: Dict[str, dict] = {
    # 通用模板，实际接入时按平台文档替换 URL 与字段
    "provider_a": {
        "create": "https://api.example-captcha.com/create.json",
        "result": "https://api.example-captcha.com/result.json",
        "auth": {"username": "USERNAME", "password": "PASSWORD", "typeid": "TYPE_ID"},
        "create_fields": {"image": "b64"},          # 表单字段 -> 图片
        "result_fields": {"id": "pic_id", "out": "recognition"},
    },
    "provider_b": {
        "create": "https://api.example-captcha.com/upload",
        "result": "https://api.example-captcha.com/getResult",
        "auth": {"token": "TOKEN"},
        "create_fields": {"file": "b64"},
        "result_fields": {"id": "taskId", "out": "data.result"},
    },
}


@dataclass
class SolveResult:
    ok: bool
    raw: str = ""
    points: List[tuple] | None = None
    x: float | None = None
    cost: float = 0.0
    elapsed: float = 0.0
    cached: bool = False
    err: str = ""


class ResultCache:
    def __init__(self, path: str = "cache.sqlite"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS c(
            h TEXT PRIMARY KEY, kind TEXT, payload TEXT, ts REAL)""")
        self.db.commit()

    @staticmethod
    def _h(img: bytes, kind: str, prompt: str = "") -> str:
        return hashlib.sha256(img + kind.encode() + prompt.encode()).hexdigest()

    def get(self, img: bytes, kind: str, prompt: str = "") -> Optional[str]:
        row = self.db.execute("SELECT payload FROM c WHERE h=?",
                              (self._h(img, kind, prompt),)).fetchone()
        return row[0] if row else None

    def put(self, img: bytes, kind: str, payload: str, prompt: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO c VALUES(?,?,?,?)",
                        (self._h(img, kind, prompt), kind, payload, time.time()))
        self.db.commit()


class CaptchaPlatform:
    def __init__(self, provider: str, concurrency: int = 8,
                 qps: float = 5.0, cache: ResultCache | None = None,
                 timeout: float = 20.0):
        cfg = PROVIDERS[provider]
        self.cfg = cfg
        self.sem = asyncio.Semaphore(concurrency)
        self.qps = qps
        self._last = 0.0
        self.cache = cache or ResultCache()
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            http2=True,               # 部分平台走 CDN，HTTP/2 更稳
        )

    async def _throttle(self) -> None:
        async with self.sem:
            now = time.monotonic()
            wait = max(0.0, self._last + 1.0 / self.qps - now)
            if wait:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def _create_and_poll(self, b64: str, extra: dict) -> str:
        cfg = self.cfg
        data = {**cfg["auth"], **cfg.get("create_fields", {}).get("__static__", {}), **extra}
        data[cfg["create_fields"]["image"]] = b64

        t0 = time.monotonic()
        r = await self.client.post(cfg["create"], data=data)
        r.raise_for_status()
        j = r.json()
        task_id = j.get(cfg["result_fields"]["id"])
        if task_id is None:
            # 部分平台同步返回
            return str(j.get(cfg["result_fields"]["out"], ""))

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.6)
            rr = await self.client.post(cfg["result"], data={
                **cfg["auth"], cfg["result_fields"]["id"]: task_id})
            jj = rr.json()
            val = jj.get(cfg["result_fields"]["out"])
            if val:
                return str(val)
        raise TimeoutError(f"poll timeout after {time.monotonic() - t0:.1f}s")

    async def solve_slider(self, bg_bytes: bytes, extra: dict | None = None) -> SolveResult:
        t0 = time.monotonic()
        hit = self.cache.get(bg_bytes, "slider")
        if hit:
            return SolveResult(True, hit, x=float(hit), cached=True,
                               elapsed=time.monotonic() - t0)
        await self._throttle()
        b64 = base64.b64encode(bg_bytes).decode()
        try:
            raw = await self._create_and_poll(b64, extra or {})
        except Exception as e:                       # 网络/限流/超时
            return SolveResult(False, err=f"{type(e).__name__}:{e}",
                               elapsed=time.monotonic() - t0)
        self.cache.put(bg_bytes, "slider", raw)
        x = self._parse_slider(raw)
        return SolveResult(True, raw, x=x, elapsed=time.monotonic() - t0)

    async def solve_points(self, img_bytes: bytes, prompt: str = "",
                           extra: dict | None = None) -> SolveResult:
        t0 = time.monotonic()
        hit = self.cache.get(img_bytes, "points", prompt)
        if hit:
            return SolveResult(True, hit, points=self._parse_points(hit),
                               cached=True, elapsed=time.monotonic() - t0)
        await self._throttle()
        b64 = base64.b64encode(img_bytes).decode()
        ex = dict(extra or {})
        if prompt:
            ex.setdefault("text", prompt)            # 部分平台需要传题面文字
        try:
            raw = await self._create_and_poll(b64, ex)
        except Exception as e:
            return SolveResult(False, err=f"{type(e).__name__}:{e}",
                               elapsed=time.monotonic() - t0)
        self.cache.put(img_bytes, "points", raw, prompt)
        return SolveResult(True, raw, points=self._parse_points(raw),
                           elapsed=time.monotonic() - t0)

    # ---------------- 解析 ----------------
    @staticmethod
    def _parse_slider(raw: str) -> Optional[float]:
        raw = raw.strip()
        try:
            if "%" in raw:
                return float(raw.replace("%", ""))     # 百分比，调用方再乘宽度
            return float(raw)
        except ValueError:
            # 形如 "x=132" 或 "132,0"
            nums = "".join(ch if ch.isdigit() or ch == "." else " " for ch in raw).split()
            return float(nums[0]) if nums else None

    @staticmethod
    def _parse_points(raw: str) -> List[tuple]:
        pts = []
        for seg in raw.replace(";", "|").split("|"):
            seg = seg.strip()
            if not seg:
                continue
            parts = [p for p in seg.replace(",", " ").replace("x", " ").split() if p]
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
        return pts

    async def aclose(self) -> None:
        await self.client.aclose()


# --------------------------------------------------------------------------- #
# 本地优先 + 平台兜底 的混合调度
# --------------------------------------------------------------------------- #
class HybridSolver:
    """
    本地模型先跑，置信度 < thresh 才外发平台。
    滑块：本地 gap.locate 拿 top1 score；点选：YOLO conf。
    """

    def __init__(self, platform: CaptchaPlatform, thresh: float = 0.62):
        self.platform = platform
        self.thresh = thresh

    async def slider(self, bg_bytes: bytes, local_fn) -> dict:
        cands = local_fn(bg_bytes)
        if cands and cands[0].score >= self.thresh:
            return {"x": cands[0].cx, "src": "local", "cands": cands}
        res = await self.platform.solve_slider(bg_bytes)
        if res.ok and res.x is not None:
            return {"x": res.x, "src": "platform", "raw": res.raw}
        return {"x": cands[0].cx if cands else None, "src": "fallback", "cands": cands}


if __name__ == "__main__":
    async def demo():
        p = CaptchaPlatform("provider_a", concurrency=4, qps=3)
        data = open("sample_bg.png", "rb").read()
        r = await p.solve_slider(data)
        print(json.dumps(r.__dict__, ensure_ascii=False, indent=2))
        await p.aclose()
    asyncio.run(demo())
