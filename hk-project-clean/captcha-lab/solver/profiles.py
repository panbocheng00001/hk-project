"""
站点适配层
==========

同一套识别内核要对接不同厂商的前端，差异只有三处：

1. **答案口径**：有的站点要"缺口中心 x"，有的要"拼图块左边缘 x"。
2. **轨迹格式**：裸数组 / 整数化 / 带 passtime 的对象，字段名各不相同。
3. **归一化尺度**：背景图原始像素 vs 页面 CSS 像素（`display_width / natural_width`）。

这里把差异收敛成 `SiteProfile` 数据，内核只认"距离（CSS px）"和"轨迹点"，
换站点就是换一个 profile，不改识别代码。

下面内置的是公开文档/前端代码里能见到的字段习惯，作为**起点**；
站点改版后通常只需要改 `fields` 与 `answer_mode` 两个字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Sequence

from .track import Point, to_payload

__all__ = ["SiteProfile", "SITE_PROFILES", "get_site", "build_payload"]


@dataclass(frozen=True)
class SiteProfile:
    """一个站点的提交约定。"""

    name: str
    # center: 提交缺口中心；left: 提交拼图块左边缘
    answer_mode: str = "center"
    # 轨迹格式，见 track.to_payload
    track_format: str = "xyt"
    # 位置容差（px）。多数站点 5~8px，越小越需要亚像素精修
    tolerance_px: float = 5.0
    # 提交字段名。None 表示该字段不提交
    fields: Dict[str, str] = field(default_factory=lambda: {
        "distance": "x", "track": "track",
    })
    # 是否需要把距离按图片缩放折算（多数前端按 CSS px 提交，需要）
    scale_by_display: bool = True
    # 额外常量字段（版本号、固定参数等）
    constants: Dict[str, Any] = field(default_factory=dict)

    def resolve_scale(self, natural_width: int, display_width: Optional[float]) -> float:
        if not self.scale_by_display or not display_width or natural_width <= 0:
            return 1.0
        return float(display_width) / float(natural_width)

    def build_payload(self, distance: float,
                      track: Sequence[Point],
                      extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_payload(self, distance, track, extra)

    def with_overrides(self, **kwargs: Any) -> "SiteProfile":
        return replace(self, **kwargs)


# --------------------------------------------------------------------------- #
# 内置档案
# --------------------------------------------------------------------------- #
SITE_PROFILES: Dict[str, SiteProfile] = {
    # 通用自研站点：背景图 + 独立拼图块，提交 x 与 track
    "generic": SiteProfile(name="generic"),

    # 极验 v3/v4：x 为拼图块左侧偏移，track 为整数三元组，另带 passtime
    "geetest": SiteProfile(
        name="geetest", answer_mode="left", track_format="geetest",
        tolerance_px=5.0,
        fields={"distance": "x", "track": "track", "passtime": "passtime"},
        constants={"gt": "", "challenge": ""},
    ),

    # 腾讯 tcaptcha：x 为中心偏移，track 为整数三元组
    "tencent": SiteProfile(
        name="tencent", answer_mode="center", track_format="tencent",
        tolerance_px=6.0,
        fields={"distance": "x", "track": "track"},
    ),

    # 网易易盾：提交滑动距离 + 轨迹字符串
    "netease": SiteProfile(
        name="netease", answer_mode="left", track_format="xyt",
        tolerance_px=5.0,
        fields={"distance": "x", "track": "track", "passtime": "passtime"},
    ),

    # 阿里云 nc：按 CSS px 提交，图片常被缩放
    "aliyun": SiteProfile(
        name="aliyun", answer_mode="center", track_format="xyt",
        tolerance_px=6.0,
        fields={"distance": "x", "track": "track"},
    ),

    # 顶象：x 为拼图块偏移
    "dingxiang": SiteProfile(
        name="dingxiang", answer_mode="left", track_format="xyt",
        tolerance_px=5.0,
        fields={"distance": "x", "track": "track"},
    ),
}


def get_site(name: str | SiteProfile) -> SiteProfile:
    if isinstance(name, SiteProfile):
        return name
    try:
        return SITE_PROFILES[name]
    except KeyError as e:
        raise KeyError(f"未知站点档案 {name!r}，可选：{', '.join(SITE_PROFILES)}") from e


def build_payload(site: SiteProfile, distance: float,
                  track: Sequence[Point],
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按站点约定组装提交体。"""
    payload: Dict[str, Any] = dict(site.constants)
    f = site.fields

    if f.get("distance"):
        value = round(float(distance), 2)
        payload[f["distance"]] = value

    if f.get("track"):
        payload[f["track"]] = to_payload(track, site.track_format)

    if f.get("passtime"):
        span = (track[-1][2] - track[0][2]) if len(track) >= 2 else 0.0
        payload[f["passtime"]] = int(round(span))

    if extra:
        payload.update(extra)
    return payload
