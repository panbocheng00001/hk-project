# -*- coding: utf-8 -*-
"""detect.py —— 只读 DOM 探测，输出 ChallengeInfo

分工：本模块**只回答"页面上有什么"**，不点、不等、不判断通过。
所有动作在 session.py，判断在 policy.py。

判定顺序即优先级：越靠后越模糊，先用特征最硬的信号定型。
"""
from __future__ import annotations

from .model import CaptchaKind, ChallengeInfo

# 注入到页面里执行的探针。返回纯 JSON-able dict。
# 设计要点：
#   1) 只做 querySelector / 正则，不改 DOM、不发请求
#   2) 一旦发现硬特征就 break，避免被低置信信号覆盖
#   3) 对 iframe 用 src 关键字，不跨域访问内容（跨域也访问不到）
DETECT_JS = r"""
() => {
  const out = {kind: 'none', detail: [], tokenLen: -1, hasSdk: false,
               url: location.href, title: document.title};
  const q = (s) => document.querySelector(s);
  const push = (k, d) => {
    if (out.kind === 'none') out.kind = k;
    if (d) out.detail.push(d);
  };

  // ---- 1) 隐形验证：只挂 token 字段 + SDK 脚本，没有可见挑战面
  const scripts = [...document.querySelectorAll('script[src]')].map(s => s.src).join(' ');
  out.hasSdk = !!(/recaptcha|hcaptcha|turnstile|mtcaptcha/i.test(scripts)
                  || window.grecaptcha || window.turnstile || window.hcaptcha);

  const tokenEl = q('#captchaToken') || q('input[name="captchaToken"]')
                || q('input[name*="captcha"][type="hidden"]');
  if (tokenEl) out.tokenLen = (tokenEl.value || '').length;

  // ---- 2) 可见挑战面：按特征强度从硬到软
  if (q('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]'))
    push('image_grid', 'recaptcha bframe');
  if (q('iframe[src*="hcaptcha.com"] iframe, .h-captcha'))
    push('image_grid', 'hcaptcha');
  if (q('iframe[src*="challenge.example.test"], .cf-turnstile'))
    push('image_grid', 'turnstile');
  if (q('iframe[src*="mtcaptcha"], [class*="mtcap"]'))
    push('slider', 'mtcaptcha');

  // canvas + 拖动类文案 = 拖动验证
  const canvas = q('canvas');
  const bodyText = (document.body && document.body.innerText || '').slice(0, 4000);
  if (canvas && /拖动|滑块|拼图|重合|按住|滑动/.test(bodyText))
    push('slider', 'canvas+拖动文案');

  // 勾选框：有 anchor iframe 或容器，且没有 bframe（bframe=已升级成图片题）
  if (q('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"], .g-recaptcha'))
    push('checkbox', 'recaptcha anchor');

  // 图形码：src/id/alt 命中关键字，且尺寸像图
  for (const img of document.querySelectorAll('img')) {
    const sig = (img.getAttribute('src') || '') + ' ' + (img.id || '') + ' ' + (img.alt || '');
    if (!/captcha|verify|checkcode|validcode|randcode|yzm|authcode/i.test(sig)) continue;
    const box = img.getBoundingClientRect();
    if (box.width > 30) { push('image_code', 'img ' + sig.slice(0, 40)); break; }
  }
  for (const el of document.querySelectorAll('input')) {
    const n = ((el.name || '') + ' ' + (el.id || '')).trim();
    if (el.type === 'hidden' || !n) continue;
    if (/captcha|verifycode|checkcode|validcode|yzm|authcode/i.test(n)) {
      push('image_code', 'input ' + n.slice(0, 40)); break;
    }
  }

  // ---- 3) 兜底：SDK 在、token 空、但没有任何可见面 → 视为隐形验证
  if (out.kind === 'none' && out.hasSdk && out.tokenLen === 0)
    out.kind = 'token_only';

  return out;
}
"""

# 判断是否已通过的补充探针（与 detect 分开：语义不同，别混用）
SOLVED_JS = r"""
() => {
  const q = (s) => document.querySelector(s);
  const tokenEl = q('#captchaToken') || q('input[name="captchaToken"]')
                || q('input[name*="captcha"][type="hidden"]');
  const visible = !!(q('iframe[src*="bframe"]') || q('iframe[src*="hcaptcha.com"]')
                     || q('.cf-turnstile') || q('[class*="mtcap"]'));
  return {tokenLen: tokenEl ? (tokenEl.value || '').length : -1, visible};
}
"""


def detect_from_payload(payload: dict | None) -> ChallengeInfo:
    """把 DETECT_JS 的返回值转成 ChallengeInfo。纯函数，便于单测。"""
    if not payload:
        return ChallengeInfo(kind=CaptchaKind.UNKNOWN, detail=("empty payload",))
    kind_raw = payload.get("kind", "none")
    try:
        kind = CaptchaKind(kind_raw)
    except ValueError:
        kind = CaptchaKind.UNKNOWN
    return ChallengeInfo(
        kind=kind,
        detail=tuple(payload.get("detail") or ()),
        token_len=int(payload.get("tokenLen", -1)),
        has_sdk=bool(payload.get("hasSdk", False)),
        url=payload.get("url", "") or "",
        title=payload.get("title", "") or "",
    )


def detect(page: PageAdapter) -> ChallengeInfo:  # noqa: F821  (仅类型注释)
    """对当前页面跑一次探测。探测本身失败时返回 UNKNOWN，不抛。"""
    try:
        payload = page.evaluate(DETECT_JS)
    except Exception as exc:  # 页面正在跳转/DOM 被拆 都会走到这
        return ChallengeInfo(kind=CaptchaKind.UNKNOWN,
                             detail=(f"probe error: {type(exc).__name__}",))
    return detect_from_payload(payload)


def solved(page: PageAdapter) -> bool:  # noqa: F821
    """是否已通过。规则：没有可见挑战面，且 token 有值或页面压根没有 token 字段。"""
    try:
        st = page.evaluate(SOLVED_JS)
    except Exception:
        return False
    if st.get("visible"):
        return False
    token_len = int(st.get("tokenLen", -1))
    return token_len > 10 or token_len == -1
