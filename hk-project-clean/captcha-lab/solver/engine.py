"""
求解引擎
========

一个入口把"图片"变成"可以直接提交的东西"：

    背景图 (+拼图块 / 完整背景)  ->  缺口候选 -> 拖动距离 -> 人化轨迹 -> 站点载荷

```python
from solver import SliderSolver

s = SliderSolver(site="generic", track_profile="natural")
r = s.solve("bg.png", piece="piece.png", display_width=340)
print(r.distance, r.confidence)
print(r.payload)          # {"x": 212.4, "track": [[...], ...]}
```

设计要点：

* **多候选**：`gap.locate` 给 top-k，`solve()` 把每个候选都算成一份可提交答案，
  `solve_until()` 按置信度依次尝试，失败自动换下一个 —— 这是把"一次命中率"
  变成"业务成功率"的关键。
* **口径分离**：识别只产出"缺口中心 x / 左边缘 x"，换算成各站点要的 `x`
  由 `SiteProfile.answer_mode` 决定，内核不掺站点逻辑。
* **轨迹可自检**：生成轨迹时可用 `defense.trajectory` 打分，低于阈值才返回。
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

from . import gap
from .gap import Candidate
from .profiles import SiteProfile, get_site
from .track import Point, generate_track, get_profile

__all__ = [
    "SolveError", "Answer", "SolveResult", "SliderSolver",
    "load_image", "encode_image_b64",
]

ImageLike = Union[np.ndarray, bytes, bytearray, str, Path]
_B64_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


class SolveError(RuntimeError):
    """识别失败（没找到任何候选缺口）。"""


# --------------------------------------------------------------------------- #
# 输入输出
# --------------------------------------------------------------------------- #
def load_image(src: ImageLike, timeout: float = 10.0) -> np.ndarray:
    """把 ndarray / bytes / 文件路径 / URL / data-url / base64 统一成图像数组。"""
    if isinstance(src, np.ndarray):
        return src
    if isinstance(src, (bytes, bytearray)):
        return _decode_bytes(bytes(src))
    if isinstance(src, Path):
        return gap.imread_any(str(src))
    if isinstance(src, str):
        s = src.strip()
        if s.startswith(("http://", "https://")):
            return _decode_bytes(_fetch_url(s, timeout=timeout))
        if s.startswith("data:") and "," in s:
            s = s.split(",", 1)[1]
        if len(s) < 4096 and os.path.exists(s):
            return gap.imread_any(s)
        if len(s) >= 64 and _B64_RE.match(s):
            try:
                return _decode_bytes(base64.b64decode(s, validate=False))
            except (binascii.Error, ValueError):
                pass
        raise FileNotFoundError(f"既不是存在的文件，也不是合法 base64：{s[:64]}...")
    raise TypeError(f"不支持的图片类型：{type(src)!r}")


def _fetch_url(url: str, timeout: float = 10.0) -> bytes:
    """直接抓图片 URL（站点把题图放在 CDN 上的常见形态）。"""
    try:
        import httpx
    except ImportError as e:                     # pragma: no cover - 可选依赖
        raise RuntimeError("URL 输入需要 httpx：pip install httpx") from e
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        })
        resp.raise_for_status()
        return resp.content


def _decode_bytes(raw: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("图片解码失败（不是常见的 png/jpg/webp/bmp）")
    return img


def encode_image_b64(img: np.ndarray, ext: str = ".png") -> str:
    """图像 -> base64（不含 data: 前缀），用于 HTTP 接口与日志。"""
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f"编码失败：{ext}")
    return base64.b64encode(buf.tobytes()).decode("ascii")


# --------------------------------------------------------------------------- #
# 结果对象
# --------------------------------------------------------------------------- #
@dataclass
class Answer:
    """一个缺口候选换算出的可提交答案。"""

    x: float                 # 缺口中心（背景图原始像素）
    y: float
    w: int
    h: int
    distance: float          # 按站点口径的拖动距离（CSS px）
    distance_left: float     # 拼图块左边缘口径的拖动距离
    confidence: float
    score: float
    method: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": round(self.x, 2), "y": round(self.y, 2),
            "w": self.w, "h": self.h,
            "distance": round(self.distance, 2),
            "distance_left": round(self.distance_left, 2),
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 4),
            "method": self.method,
        }


@dataclass
class SolveResult:
    """一次求解的完整产物。"""

    ok: bool
    answers: List[Answer]
    track: List[Point]
    payload: Dict[str, Any]
    candidates: List[Candidate]
    site: str
    track_profile: str
    scale: float
    image_size: Tuple[int, int]
    elapsed_ms: float
    meta: Dict[str, Any] = field(default_factory=dict)

    # 便捷属性：主答案
    @property
    def answer(self) -> Answer:
        return self.answers[0]

    @property
    def x(self) -> float:
        return self.answers[0].x

    @property
    def y(self) -> float:
        return self.answers[0].y

    @property
    def distance(self) -> float:
        return self.answers[0].distance

    @property
    def confidence(self) -> float:
        return self.answers[0].confidence

    def to_dict(self, with_track: bool = True) -> Dict[str, Any]:
        d = {
            "ok": self.ok,
            "x": round(self.answer.x, 2),
            "y": round(self.answer.y, 2),
            "distance": round(self.answer.distance, 2),
            "distance_left": round(self.answer.distance_left, 2),
            "confidence": round(self.answer.confidence, 4),
            "method": self.answer.method,
            "site": self.site,
            "track_profile": self.track_profile,
            "scale": round(self.scale, 4),
            "image_size": list(self.image_size),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "answers": [a.to_dict() for a in self.answers],
            "candidates": [
                {"x": c.x, "y": c.y, "w": c.w, "h": c.h,
                 "cx": round(c.cx, 2), "score": round(c.score, 3), "method": c.method}
                for c in self.candidates
            ],
            "meta": self.meta,
        }
        if with_track:
            d["track"] = [[round(x, 2), round(y, 2), round(t, 1)] for x, y, t in self.track]
            d["payload"] = self.payload
        return d


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
class SliderSolver:
    """滑块验证码求解器（无状态，可并发复用）。"""

    def __init__(self,
                 site: Union[str, SiteProfile] = "generic",
                 track_profile: str = "mouse",
                 topk: int = 3,
                 audit_track: bool = True,
                 risk_threshold: float = 0.5,
                 dedupe_px: float = 2.0):
        """
        site           : 站点档案名，见 solver.profiles.SITE_PROFILES
        track_profile  : 轨迹档案名，见 solver.track.PROFILES
        topk           : 保留多少个候选缺口用于重试
        audit_track    : 生成轨迹后用 defense.trajectory 自检
        dedupe_px      : 候选 x 相差小于该值视为同一个，避免浪费重试
        """
        self.site = get_site(site)
        self.track_profile = get_profile(track_profile).name
        self.topk = max(1, int(topk))
        self.audit_track = audit_track
        self.risk_threshold = risk_threshold
        self.dedupe_px = dedupe_px

    # ---------------- 主入口 ---------------- #
    def solve(self,
              bg: ImageLike,
              piece: Optional[ImageLike] = None,
              full_bg: Optional[ImageLike] = None,
              display_width: Optional[float] = None,
              slider_start_x: float = 0.0,
              track_profile: Optional[str] = None,
              site: Optional[Union[str, SiteProfile]] = None,
              topk: Optional[int] = None,
              seed: Optional[int] = None) -> SolveResult:
        """
        识别缺口并生成一份可直接提交的答案。

        bg            : 带缺口的背景图（路径/bytes/ndarray/base64）
        piece         : 拼图块小图（可选，有则模板匹配，显著提升准确率）
        full_bg       : 无缺口的完整背景图（可选，有则差分法，最准）
        display_width : 前端渲染宽度（CSS px）。不传则按原图宽度算
        slider_start_x: 滑块手柄初始中心相对图片左边缘的偏移
        track_profile : 覆盖构造时的轨迹档案
        site          : 覆盖构造时的站点档案
        """
        t0 = time.perf_counter()
        site_obj = get_site(site) if site is not None else self.site
        track_name = get_profile(track_profile).name if track_profile else self.track_profile

        bg_img = gap.to_bgr(load_image(bg))
        piece_img = load_image(piece) if piece is not None else None
        full_img = load_image(full_bg) if full_bg is not None else None

        H, W = bg_img.shape[:2]
        k = max(1, int(topk or self.topk))
        cands = gap.locate(bg_img, piece=piece_img, full_bg=full_img, topk=k)
        if not cands:
            raise SolveError("未找到任何缺口候选：检查图片是否裁剪正确、是否缺少拼图块")

        scale = site_obj.resolve_scale(W, display_width)
        display = float(display_width) if display_width else float(W)

        # 候选去重（按 x 距离）
        uniq: List[Candidate] = []
        for c in cands:
            if all(abs(c.cx - u.cx) > self.dedupe_px for u in uniq):
                uniq.append(c)

        confs = _softmax([max(0.0, c.score) for c in uniq])
        answers: List[Answer] = []
        for c, conf in zip(uniq, confs):
            center = c.cx * scale - slider_start_x
            left = c.x * scale - slider_start_x
            max_drag = max(0.0, display - c.w * scale)
            dist = center if site_obj.answer_mode == "center" else left
            answers.append(Answer(
                x=float(c.cx), y=float(c.cy), w=int(c.w), h=int(c.h),
                distance=round(_clamp(dist, 0.0, max_drag), 2),
                distance_left=round(_clamp(left, 0.0, max_drag), 2),
                confidence=float(conf), score=float(c.score), method=c.method,
            ))

        track = self.plan_track(answers[0].distance, track_name, seed=seed)
        payload = site_obj.build_payload(answers[0].distance, track)

        return SolveResult(
            ok=True, answers=answers, track=track, payload=payload,
            candidates=cands, site=site_obj.name, track_profile=track_name,
            scale=scale, image_size=(W, H),
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            meta={
                "display_width": display,
                "slider_start_x": slider_start_x,
                "answer_mode": site_obj.answer_mode,
                "tolerance_px": site_obj.tolerance_px,
                "seed": seed,
            },
        )

    def solve_bytes(self, bg: bytes, piece: Optional[bytes] = None, **kwargs: Any) -> SolveResult:
        """HTTP / 爬虫场景：直接吃 bytes。"""
        return self.solve(bg, piece=piece, **kwargs)

    def solve_files(self, bg_path: Union[str, Path],
                    piece_path: Optional[Union[str, Path]] = None, **kwargs: Any) -> SolveResult:
        return self.solve(str(bg_path), piece=str(piece_path) if piece_path else None, **kwargs)

    # ---------------- 轨迹 ---------------- #
    def plan_track(self, distance: float, profile: Optional[str] = None,
                   seed: Optional[int] = None) -> List[Point]:
        return generate_track(
            float(distance),
            profile=profile or self.track_profile,
            seed=seed,
            audit_risk=self.audit_track,
            risk_threshold=self.risk_threshold,
        )

    # ---------------- 带反馈的重试 ---------------- #
    def solve_until(self,
                    fetch: Callable[[int], Dict[str, Any]],
                    submit: Callable[[Answer, Dict[str, Any]], bool],
                    max_attempts: int = 3,
                    candidates_per_challenge: int = 2,
                    refresh: bool = True,
                    site: Optional[Union[str, SiteProfile]] = None,
                    track_profile: Optional[str] = None,
                    seed: Optional[int] = None) -> SolveResult:
        """
        识别 -> 提交 -> 失败重试的闭环。

        fetch(attempt)   : 返回最新一题的 solve() 关键字参数
                           （{"bg":..., "piece":..., "display_width":...}）
                           refresh=True 时每轮都重新取题
        submit(answer, payload) -> bool : 真正提交给站点，返回是否通过
        candidates_per_challenge : 同一题最多试几个候选（站点允许重试时才有意义）

        返回最后一次的 SolveResult；`ok` 表示是否成功。
        """
        site_obj = get_site(site) if site is not None else self.site
        last: Optional[SolveResult] = None
        for attempt in range(1, max_attempts + 1):
            challenge = fetch(1 if not refresh else attempt)
            res = self.solve(site=site_obj, track_profile=track_profile, seed=seed, **challenge)
            last = res
            for ans in res.answers[:max(1, candidates_per_challenge)]:
                track = self.plan_track(ans.distance, track_profile or self.track_profile,
                                        seed=seed)
                payload = site_obj.build_payload(ans.distance, track)
                if submit(ans, payload):
                    res.ok = True
                    res.track, res.payload = track, payload
                    res.answers = [ans] + [a for a in res.answers if a is not ans]
                    return res
                res.ok = False
        if last is None:
            raise SolveError("solve_until 没有拿到任何题目")
        last.ok = False
        return last

    # ---------------- 可视化 ---------------- #
    def annotate(self, bg: ImageLike, result: SolveResult) -> np.ndarray:
        """在主答案位置画框和竖线，用于排障与日志留档。"""
        vis = gap.to_bgr(load_image(bg)).copy()
        for i, ans in enumerate(result.answers):
            color = (0, 0, 255) if i == 0 else (0, 165, 255)
            x0, y0 = int(ans.x - ans.w / 2), int(ans.y - ans.h / 2)
            cv2.rectangle(vis, (x0, y0), (x0 + ans.w, y0 + ans.h), color, 2)
            cv2.line(vis, (int(ans.x), 0), (int(ans.x), vis.shape[0]), color, 1)
            cv2.putText(vis, f"{i} {ans.confidence:.2f}", (x0, max(12, y0 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        return vis

    def save_debug(self, bg: ImageLike, result: SolveResult, out_path: Union[str, Path]) -> None:
        vis = self.annotate(bg, result)
        ok, buf = cv2.imencode(Path(out_path).suffix or ".png", vis)
        if ok:
            buf.tofile(str(out_path))


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _softmax(scores: Sequence[float]) -> List[float]:
    """候选置信度：按最高分自适应的 softmax，输出可解释的相对概率。"""
    if not scores:
        return []
    arr = np.asarray(scores, dtype=np.float64)
    top = float(arr.max())
    temp = max(top * 0.18, 1e-6)
    e = np.exp((arr - top) / temp)
    return [float(v) for v in (e / e.sum())]
