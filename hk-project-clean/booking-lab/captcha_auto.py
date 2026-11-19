# -*- coding: utf-8 -*-
"""captcha_auto.py —— 滑块自动求解 + 自动拖动（可选能力）

定位
----
这是 captcha_flow 的**前置加速器**，不是它的替代：
- captcha_flow 的四条决策原则一行不改（默认仍交人）；
- 只有 `captcha.guard()` 显式调用本模块，且只在 `CAPTCHA_AUTO != "0"` 时；
- 关掉开关（`CAPTCHA_AUTO=0`）后行为 100% 等于原实现；
- `FakePage` 之类的测试替身没有 `mouse`，会被 `hasattr(page, "mouse")` 挡掉，
  所以离线测试不受影响。

链路
----
    canvas 位图 ──> solver.overlap.solve_canvas ──> dx / slider_dx（CSS px）
              ──> solver.track.generate_track ──> 拟人轨迹 [(x, y, t), ...]
              ──> Playwright mouse（CDP 真实输入，isTrusted=True）──> 复核

复核优先读页面自身给出的判定（`window.__grade.pass`，复刻题服务会写），
其次看 canvas 是否消失 / token 字段是否已生成。

任何一步失败都返回 `{ok: False, note: ...}`，由调用方回落到人工交接，
不在本模块里静默重试到死。
"""
from __future__ import annotations

import os
import random
from typing import Any, Dict, List, Optional

__all__ = ["solve_page", "auto_enabled", "probe", "PROBE_JS", "GRADE_JS"]


# 页面探测：找最大 canvas + 滑块手柄 + 轨道宽度，并顺手带回页面自己的判分结果
PROBE_JS = r"""
(() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 20; };
  const canvases = [...document.querySelectorAll('canvas')].filter(vis);
  canvases.sort((a, b) => (b.width * b.height) - (a.width * a.height));
  const c = canvases[0] || null;

  const pickHandle = () => {
    const sels = ['#handle', '[id*="handle" i]', '[class*="handle" i]',
                  '[class*="slider-btn" i]', '[class*="drag-btn" i]', '[role="slider"]'];
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) {
        const r = el.getBoundingClientRect();
        if (r.width > 8 && r.height > 8 && r.width < 140 && r.height < 140) return { el, r };
      }
    }
    return null;
  };

  const h = pickHandle();
  const track = h ? h.el.parentElement : null;
  const cr = c ? c.getBoundingClientRect() : null;

  return {
    ok: !!(c && h),
    dataUrl: c ? c.toDataURL('image/png') : '',
    bmpW: c ? c.width : 0,
    bmpH: c ? c.height : 0,
    cssW: cr ? cr.width : 0,
    cssH: cr ? cr.height : 0,
    handle: h ? { x: h.r.x + h.r.width / 2, y: h.r.y + h.r.height / 2,
                  w: h.r.width, h: h.r.height } : null,
    trackW: track ? track.getBoundingClientRect().width : (cr ? cr.width : 0),
    grade: (window.__grade || null),
  };
})()
"""

# 判定是否已通过：页面给了 pass 就用它；否则看 canvas 是否消失 / token 是否生成
GRADE_JS = r"""
(() => {
  if (window.__grade && typeof window.__grade.pass === 'boolean') {
    return { done: true, ok: !!window.__grade.pass, how: 'page-grade',
             detail: window.__grade };
  }
  const c = document.querySelector('canvas');
  const visible = !!(c && c.getBoundingClientRect().height > 20);
  if (!visible) return { done: true, ok: true, how: 'canvas-gone' };
  const tok = document.querySelector('input[name*="token" i], #captchaToken, [name="geetest_challenge"]');
  if (tok && (tok.value || '').length > 8) return { done: true, ok: true, how: 'token' };
  return { done: false, ok: false, how: 'pending' };
})()
"""


def auto_enabled() -> bool:
    """CAPTCHA_AUTO 缺省开启；显式设成 0/false/off/no 时关闭。"""
    return str(os.environ.get("CAPTCHA_AUTO", "1")).strip().lower() not in ("0", "false", "off", "no")


def probe(page) -> Dict[str, Any]:
    """读取页面上的画布 / 手柄 / 轨道几何。没有可识别的滑块时 ok=False。"""
    raw = page.evaluate(PROBE_JS)
    return dict(raw or {})


def _bridge():
    import captcha_lab_bridge as bridge
    return bridge


def solve_image(data: str, track_w: float = 400.0, handle_w: float = 34.0,
                bmp_w: Optional[float] = None,
                profile: str = "natural") -> Dict[str, Any]:
    """纯图像解法：位图 dataURL → dx / slider_dx / 轨迹。

    给 HTTP 服务（ext_server /solve_slider）复用；不碰浏览器，不做拖动。
    内核不可用时返回 ok=False 并带 error，不抛异常。
    """
    bridge = _bridge()
    res = bridge.solve_canvas(data, track_w=float(track_w), handle_w=float(handle_w),
                              bmp_w=float(bmp_w) if bmp_w else None)
    out: Dict[str, Any] = {
        "ok": bool(res.get("ok")),
        "dx": res.get("dx"),
        "slider_dx": res.get("slider_dx"),
        "pairs": res.get("pairs"),
        "level": res.get("level"),
        "error": res.get("error", ""),
    }
    if not out["ok"]:
        return out
    pts = bridge.make_track(float(out["slider_dx"]), profile=profile)
    out["track"] = [[float(x), float(y), float(t)] for x, y, t in pts]
    out["points"] = len(pts)
    try:
        out["risk"] = bridge.analyze_track(pts).get("risk")
    except Exception:
        out["risk"] = None
    return out


def _wait(page, ms: int) -> None:
    if ms > 0:
        page.wait_for_timeout(int(min(ms, 120)))


def _drag(page, geo: Dict[str, Any], dist: float, profile: str, log) -> None:
    """按拟人轨迹把鼠标从手柄中心拖到 x + dist。真实输入，不是 DOM 合成事件。"""
    h = geo["handle"]
    x0, y0 = float(h["x"]), float(h["y"])
    pts: List[Any] = _bridge().make_track(float(dist), profile=profile)

    page.mouse.move(x0, y0)
    _wait(page, random.randint(90, 220))

    page.mouse.down()
    t_prev = 0.0
    for px, py, t in pts:
        dt = float(t) - t_prev
        if dt > 0:
            page.wait_for_timeout(int(dt))
        t_prev = float(t)
        page.mouse.move(x0 + float(px), y0 + float(py))
    page.wait_for_timeout(random.randint(120, 260))
    page.mouse.up()
    log(f"[captcha_auto] 拖动完成：{dist:.2f}px / {len(pts)} 点")


def _graded(page, wait_ms: int = 2500) -> Dict[str, Any]:
    """轮询页面判定，最多等 wait_ms。"""
    step = 120
    waited = 0
    last: Dict[str, Any] = {"done": False, "ok": False, "how": "pending"}
    while waited <= wait_ms:
        try:
            last = dict(page.evaluate(GRADE_JS) or {})
        except Exception:
            last = {"done": False, "ok": False, "how": "eval-error"}
        if last.get("done"):
            return last
        page.wait_for_timeout(step)
        waited += step
    return last


def solve_page(page, who: str = "", max_tries: int = 3,
               profile: str = "natural", log=print) -> Dict[str, Any]:
    """识别 + 拖动 + 复核，最多 max_tries 轮。

    返回
        {ok, dx, slider_dx, tries, note, history}
        ok=False 时调用方应回落到人工交接（guard 里就是这么接的）。
    """
    if not hasattr(page, "mouse"):
        return {"ok": False, "note": "页面对象没有 mouse（非 Playwright），跳过", "tries": 0}

    bridge = _bridge()
    history: List[Dict[str, Any]] = []

    for i in range(1, max_tries + 1):
        geo = probe(page)
        if not geo.get("ok"):
            history.append({"try": i, "stage": "probe", "ok": False,
                            "note": "页面没有可识别的 canvas/手柄"})
            return {"ok": False, "note": "没有可识别的滑块", "tries": i, "history": history}

        # 页面已经自己判过了（上一轮拖动其实过了）
        grade = geo.get("grade") or {}
        if grade.get("pass") is True:
            return {"ok": True, "note": "页面已判定通过", "tries": i - 1,
                    "slider_dx": grade.get("sliderDx"), "history": history}

        try:
            res = solve_image(geo["dataUrl"],
                              track_w=float(geo.get("trackW") or 400.0),
                              handle_w=float(geo["handle"]["w"]),
                              bmp_w=float(geo.get("bmpW") or 0) or None,
                              profile=profile)
        except Exception as e:
            history.append({"try": i, "stage": "solve", "ok": False,
                            "note": f"{type(e).__name__}: {e}"})
            return {"ok": False, "note": f"识别内核不可用：{e}", "tries": i, "history": history}

        rec = {"try": i, "dx": res.get("dx"), "slider_dx": res.get("slider_dx"),
               "pairs": res.get("pairs"), "level": res.get("level")}
        history.append(rec)
        if not res.get("ok"):
            rec["note"] = res.get("error") or "识别失败"
            continue

        dist = float(res["slider_dx"])
        if dist <= 0:
            rec["note"] = "位移为 0，跳过"
            continue

        log(f"[captcha_auto] 第 {i} 轮：dx={float(res['dx']):.2f} "
            f"slider_dx={dist:.2f} pairs={res.get('pairs')}")
        try:
            _drag(page, geo, dist, profile, log)
        except Exception as e:
            rec["note"] = f"拖动异常：{type(e).__name__}: {e}"
            continue

        verdict = _graded(page)
        rec["grade"] = verdict
        if verdict.get("ok"):
            return {"ok": True, "dx": res.get("dx"), "slider_dx": dist,
                    "tries": i, "note": verdict.get("how"), "history": history}

    return {"ok": False, "note": "多轮尝试仍未通过", "tries": max_tries, "history": history}
