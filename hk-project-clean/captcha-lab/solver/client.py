"""
对接层：把求解器接到真实的采集与提交链路上
==========================================

三种常见工程形态，按侵入程度从低到高：

1. **已有请求链路**（requests/httpx/自有 SDK）：实现 `fetch()` 和 `submit()`
   两个回调，用 `SliderFlow` 串起来，重试逻辑由框架负责。
2. **标准 JSON 接口**（下发题目 → 提交 x/track）：用 `JsonSliderFlow`，
   只需要写一个 `EndpointConfig` 描述字段。
3. **真实浏览器**（页面渲染、风控看鼠标事件）：用 `PlaywrightSlider`，
   截图取图 + 真实 `mouse.move/down/up` 事件流。

三者的共同点：**识别归识别，驱动归驱动**。求解器只产出
`(距离, 轨迹点)`，怎么发出去由这一层决定，换站点不动内核。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Tuple, Union
from urllib.parse import urljoin

from .engine import Answer, SolveResult, SliderSolver, load_image
from .profiles import SiteProfile, get_site

__all__ = [
    "HttpxClient", "SliderFlow", "JsonSliderFlow", "EndpointConfig",
    "PlaywrightSlider", "BrowserSelectors",
]

try:  # pragma: no cover - 可选依赖
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# HTTP 基础
# --------------------------------------------------------------------------- #
class HttpxClient:
    """带重试、代理、统一头的 httpx 封装（会话保持 cookie）。"""

    def __init__(self,
                 base_url: str = "",
                 headers: Optional[Dict[str, str]] = None,
                 cookies: Optional[Dict[str, str]] = None,
                 proxy: Optional[str] = None,
                 timeout: float = 15.0,
                 retries: int = 2,
                 verify: bool = True):
        if httpx is None:
            raise RuntimeError("需要安装 httpx：pip install httpx")
        self.base_url = base_url.rstrip("/") + "/" if base_url else ""
        self.retries = max(0, retries)
        kwargs: Dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "verify": verify,
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        }
        if headers:
            kwargs["headers"].update(headers)
        if cookies:
            kwargs["cookies"] = cookies
        if proxy:
            kwargs["proxy"] = proxy
        self.client = httpx.Client(**kwargs)

    # -- 基础请求 ----------------------------------------------------------- #
    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url, path.lstrip("/"))

    def request(self, method: str, path: str, **kwargs: Any) -> "httpx.Response":
        last: Optional[Exception] = None
        for i in range(self.retries + 1):
            try:
                resp = self.client.request(method, self._url(path), **kwargs)
                if resp.status_code < 500:
                    return resp
                last = RuntimeError(f"HTTP {resp.status_code}")
            except Exception as e:          # 网络抖动 / 代理失败
                last = e
            if i < self.retries:
                time.sleep(0.4 * (2 ** i))
        raise RuntimeError(f"请求失败 {method} {path}: {last}")

    def get_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("GET", path, **kwargs).json()

    def post_json(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.request("POST", path, **kwargs).json()

    def get_bytes(self, url: str) -> bytes:
        return self.request("GET", url).content

    def cookies_dict(self) -> Dict[str, str]:
        return dict(self.client.cookies)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "HttpxClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# 回调式流程
# --------------------------------------------------------------------------- #
class SliderFlow:
    """
    最通用的编排：`fetch()` 拿题，`submit()` 交卷，失败自动重试。

    ```python
    def fetch(attempt):
        data = api.get_challenge()
        return {"bg": data["bg"], "piece": data["piece"],
                "display_width": data["width"], "_token": data["token"]}

    def submit(answer, payload):
        # answer: Answer 对象；payload: 站点载荷
        return api.verify(token=current_token, **payload)["ok"]

    flow = SliderFlow(SliderSolver(site="geetest"), fetch, submit)
    result = flow.run(max_attempts=3)
    ```
    """

    def __init__(self,
                 solver: SliderSolver,
                 fetch: Callable[[int], Dict[str, Any]],
                 submit: Callable[[Answer, Dict[str, Any]], bool],
                 refresh: bool = True,
                 on_attempt: Optional[Callable[[int, Answer, bool], None]] = None):
        self.solver = solver
        self.fetch = fetch
        self.submit = submit
        self.refresh = refresh
        self.on_attempt = on_attempt

    def run(self, max_attempts: int = 3,
            candidates_per_challenge: int = 2) -> SolveResult:
        def _fetch(attempt: int) -> Dict[str, Any]:
            challenge = dict(self.fetch(1 if not self.refresh else attempt))
            # 下划线开头的键是流程自己用的元数据，不传给识别器
            return {k: v for k, v in challenge.items() if not k.startswith("_")}

        def _submit(answer: Answer, payload: Dict[str, Any]) -> bool:
            ok = bool(self.submit(answer, payload))
            if self.on_attempt:
                self.on_attempt(0, answer, ok)
            return ok

        return self.solver.solve_until(
            _fetch, _submit,
            max_attempts=max_attempts,
            candidates_per_challenge=candidates_per_challenge,
            refresh=self.refresh,
        )


# --------------------------------------------------------------------------- #
# 标准 JSON 接口
# --------------------------------------------------------------------------- #
@dataclass
class EndpointConfig:
    """描述一个"下发图片 + 校验 x/track"的 JSON 接口。"""

    challenge_path: str
    verify_path: str
    challenge_method: str = "POST"
    verify_method: str = "POST"
    # 题目响应里的字段名
    bg_field: str = "bg"
    piece_field: str = "piece"
    token_field: str = "token"
    width_field: str = "width"
    # 校验请求里的字段名
    distance_field: str = "x"
    track_field: str = "track"
    token_param: str = "token"
    # 判定成功的字段与真值
    success_field: str = "success"
    success_value: Any = True
    # 图片是 URL 还是 base64/裸字节
    images_are_urls: bool = False
    extra_challenge_params: Dict[str, Any] = field(default_factory=dict)
    extra_verify_params: Dict[str, Any] = field(default_factory=dict)


class JsonSliderFlow:
    """配置驱动的 JSON 接口流程，覆盖大多数自研站点的提交协议。"""

    def __init__(self, solver: SliderSolver, client: HttpxClient,
                 config: EndpointConfig,
                 site: Union[str, SiteProfile, None] = None):
        self.solver = solver
        self.client = client
        self.cfg = config
        self.site = get_site(site) if site is not None else solver.site
        self.token: Optional[str] = None

    # -- 内部 --------------------------------------------------------------- #
    def _image(self, value: Any) -> Any:
        if value is None:
            return None
        if self.cfg.images_are_urls:
            return self.client.get_bytes(str(value))
        return load_image(value)

    def _fetch(self, attempt: int) -> Dict[str, Any]:
        cfg = self.cfg
        resp = self.client.request(
            cfg.challenge_method, cfg.challenge_path,
            json=cfg.extra_challenge_params or None,
        ).json()
        self.token = resp.get(cfg.token_field)
        kwargs: Dict[str, Any] = {"bg": self._image(resp.get(cfg.bg_field))}
        piece = self._image(resp.get(cfg.piece_field))
        if piece is not None:
            kwargs["piece"] = piece
        if resp.get(cfg.width_field):
            kwargs["display_width"] = float(resp[cfg.width_field])
        return kwargs

    def _submit(self, answer: Answer, payload: Dict[str, Any]) -> bool:
        cfg = self.cfg
        body = dict(cfg.extra_verify_params)
        body[cfg.token_param] = self.token
        body[cfg.distance_field] = payload.get(self.site.fields.get("distance", "x"),
                                               round(answer.distance, 2))
        body[cfg.track_field] = payload.get(self.site.fields.get("track", "track"))
        data = self.client.request(cfg.verify_method, cfg.verify_path, json=body).json()
        return data.get(cfg.success_field) == cfg.success_value

    def run(self, max_attempts: int = 3, candidates_per_challenge: int = 2) -> SolveResult:
        flow = SliderFlow(self.solver, self._fetch, self._submit, refresh=True)
        return flow.run(max_attempts=max_attempts,
                        candidates_per_challenge=candidates_per_challenge)


# --------------------------------------------------------------------------- #
# 浏览器驱动
# --------------------------------------------------------------------------- #
@dataclass
class BrowserSelectors:
    """页面上和滑块相关的元素选择器。"""

    bg: str                                   # 背景图元素
    handle: str                               # 滑块手柄
    piece: Optional[str] = None               # 拼图块元素（有则提高准确率）
    track_bar: Optional[str] = None           # 轨道（用于计算可拖动范围）
    display_width: Optional[float] = None     # 覆盖自动读取的元素宽度


class PlaywrightSlider:
    """
    真实浏览器驱动：元素截图 -> 识别 -> 鼠标事件回放。

    只依赖 Playwright 的公共 API，不注入任何 JS 补丁，事件流与真人操作同源。

    ```python
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        page = p.chromium.launch(headless=False).new_page()
        page.goto(URL)
        PlaywrightSlider(SliderSolver(site="generic")).solve_page(
            page, BrowserSelectors(bg=".captcha-bg", handle=".slider-handle"))
    ```
    """

    def __init__(self, solver: SliderSolver,
                 site: Union[str, SiteProfile, None] = None,
                 settle_ms: int = 300,
                 human_offset: Tuple[float, float] = (0.0, 0.0)):
        self.solver = solver
        self.site = get_site(site) if site is not None else solver.site
        self.settle_ms = settle_ms
        self.human_offset = human_offset       # 手柄上的落点偏移，避免每次点同一像素

    # -- 取图 --------------------------------------------------------------- #
    def grab(self, page: Any, sel: BrowserSelectors) -> Dict[str, Any]:
        bg_el = page.query_selector(sel.bg)
        if bg_el is None:
            raise RuntimeError(f"找不到背景图元素：{sel.bg}")
        box = bg_el.bounding_box() or {}
        width = sel.display_width or box.get("width")
        kwargs: Dict[str, Any] = {
            "bg": bg_el.screenshot(),
            "display_width": float(width) if width else None,
        }
        if sel.piece:
            piece_el = page.query_selector(sel.piece)
            if piece_el is not None:
                kwargs["piece"] = piece_el.screenshot()
        return kwargs

    # -- 回放 --------------------------------------------------------------- #
    def drag(self, page: Any, handle_selector: str,
             track: Sequence[Tuple[float, float, float]],
             settle_ms: Optional[int] = None) -> None:
        handle = page.query_selector(handle_selector)
        if handle is None:
            raise RuntimeError(f"找不到滑块手柄：{handle_selector}")
        box = handle.bounding_box() or {"x": 0, "y": 0, "width": 0, "height": 0}
        x0 = box["x"] + box["width"] / 2 + self.human_offset[0]
        y0 = box["y"] + box["height"] / 2 + self.human_offset[1]

        page.mouse.move(x0, y0)
        page.wait_for_timeout(40)              # 真人按下前会有短暂停留
        page.mouse.down()

        last_t = 0.0
        for x, y, t in track:
            delay = max(0.0, min(t - last_t, 120.0))
            last_t = t
            if delay >= 1:
                page.wait_for_timeout(delay)
            page.mouse.move(x0 + x, y0 + y, steps=1)

        page.mouse.up()
        page.wait_for_timeout(settle_ms if settle_ms is not None else self.settle_ms)

    # -- 一键完成 ------------------------------------------------------------ #
    def solve_page(self, page: Any, sel: BrowserSelectors,
                   attempts: int = 2) -> SolveResult:
        last: Optional[SolveResult] = None
        for _ in range(max(1, attempts)):
            challenge = self.grab(page, sel)
            result = self.solver.solve(site=self.site, **challenge)
            self.drag(page, sel.handle, result.track)
            last = result
            if self._passed(page):
                result.ok = True
                return result
            result.ok = False
            page.wait_for_timeout(800)         # 等题目刷新
        if last is None:
            raise RuntimeError("没有拿到任何题目")
        return last

    @staticmethod
    def _passed(page: Any, timeout_ms: int = 0) -> bool:
        """默认判定：页面出现 success/通过类元素。可按站点覆盖。"""
        try:
            el = page.query_selector(
                ".geetest_success_radar_tip, .nc-lang-cnt, .tcaptcha-success, "
                ".yidun--success, [class*=success]"
            )
            return el is not None
        except Exception:
            return False
