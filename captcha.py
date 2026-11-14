# -*- coding: utf-8 -*-
"""captcha.py —— 验证码识别与处理（中途冒出验证码时不慌）
=====================================================================
系统里已经确认存在 `captchaToken` 隐藏字段，说明官方有验证码机制。
没名额时走不到那一步，所以这里按「宽检测 + 保守处理」设计：

    检测到验证码  →  截图留档  →  能自动点的先点  →  点不了就响铃喊人

四种类型，处理方式不同：
    recaptcha_v3 / 隐形   token 会自动填入，只需等 captchaToken 有值再提交
    recaptcha_checkbox    「我不是机器人」勾选框，自动点一下，多数情况就过
    recaptcha_image       弹图片挑战（选红绿灯那种），只能人工
    image_code            图形验证码（4–6 位字母数字图），只能人工
    hcaptcha / turnstile  同上，弹了就喊人

用法（grab.py 已在关键提交点调用）：
    import captcha
    captcha.guard(page, "w1", shot=self.shot, say=log)
"""
from __future__ import annotations

import time

DETECT_JS = r"""
() => {
  const out = {type: 'none', detail: [], tokenLen: -1};
  const push = (t, d) => { if (out.type === 'none') out.type = t; out.detail.push(d); };

  const sel = (s) => document.querySelector(s);

  // Cloudflare Turnstile
  if (sel('iframe[src*="challenges.cloudflare.com"], .cf-turnstile'))
    push('turnstile', 'Cloudflare Turnstile');
  // hCaptcha
  if (sel('iframe[src*="hcaptcha.com"], .h-captcha'))
    push('hcaptcha', 'hCaptcha');
  // reCAPTCHA 图片挑战（bframe）
  if (sel('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]'))
    push('recaptcha_image', 'reCAPTCHA 图片挑战 iframe');
  // reCAPTCHA 勾选框（anchor）/ 容器
  if (sel('.g-recaptcha, iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"], iframe[title*="reCAPTCHA"]'))
    push('recaptcha_checkbox', 'reCAPTCHA 勾选框');

  // captchaToken：有值=已通过，空=还没验证
  const tk = sel('#captchaToken') || sel('input[name="captchaToken"]');
  if (tk) out.tokenLen = (tk.value || '').length;

  // 页面是否真的部署了验证码脚本。JSP 页常常只留一个空的 captchaToken 字段，
  // 并没有挂 reCAPTCHA —— 这种情况绝对不能傻等 token。
  out.v3 = !!(sel('script[src*="recaptcha"], script[src*="gstatic.com/recaptcha"]')
              || sel('[data-sitekey]') || window.grecaptcha || window.turnstile);

  // 图形验证码：图片或输入框
  document.querySelectorAll('img').forEach(img => {
    const s = (img.getAttribute('src') || '') + ' ' + (img.id || '') + ' ' + (img.alt || '');
    const box = img.getBoundingClientRect();
    if (/captcha|verify|checkcode|validcode|randcode|yzm|authcode/i.test(s) && box.width > 30) {
      push('image_code', '图形验证码图片 ' + ((img.id || img.getAttribute('src')) || '').slice(0, 50));
    }
  });
  document.querySelectorAll('input').forEach(el => {
    const n = ((el.name || '') + ' ' + (el.id || '')).trim();
    if (el.type !== 'hidden' && /captcha|verifycode|checkcode|validcode|yzm|authcode/i.test(n)) {
      push('image_code', '验证码输入框 ' + n.slice(0, 50));
    }
  });

  out.title = document.title;
  out.url = location.href;
  return out;
}
"""

# 这些类型必须人工介入
NEED_HUMAN = ("recaptcha_image", "image_code", "hcaptcha", "turnstile")


def beep(times: int = 6) -> None:
    try:
        import winsound
        for _ in range(times):
            winsound.Beep(1400, 160)
            time.sleep(0.07)
    except Exception:
        print("\a" * times, end="", flush=True)


def detect(page) -> dict:
    try:
        return page.evaluate(DETECT_JS)
    except Exception:
        return {"type": "none", "detail": [], "tokenLen": -1, "error": True}


def click_checkbox(page) -> bool:
    """试着点 reCAPTCHA 的「我不是机器人」。跨域 iframe 也能点。"""
    for frame_sel in ('iframe[title*="reCAPTCHA"]', 'iframe[src*="recaptcha/api2/anchor"]',
                      'iframe[src*="recaptcha/enterprise/anchor"]'):
        try:
            fl = page.frame_locator(frame_sel)
            fl.locator("#recaptcha-anchor, .recaptcha-checkbox-border, "
                       "span.recaptcha-checkbox").first.click(timeout=4000)
            return True
        except Exception:
            continue
    for css in (".g-recaptcha", ".cf-turnstile", ".h-captcha"):
        try:
            page.click(css, timeout=2500)
            return True
        except Exception:
            continue
    return False


def solved(page) -> bool:
    """判断验证是否已通过：没有挑战组件了，且 captchaToken 有值（若有该字段）。"""
    d = detect(page)
    if d["type"] != "none":
        return False
    return d.get("tokenLen", 0) > 10 or d.get("tokenLen", -1) == -1


def guard(page, who: str, shot=None, say=print, timeout: int = 180,
          on_captcha=None) -> str:
    """在提交类操作前调用。
    返回：none（没有验证码）/ solved（已通过）/ timeout（需要你重试）。

    on_captcha(page, info, note)：验证码取证回调，recorder.captcha_pack 会把它存成专项证据包。
    """
    def pack(page_obj, info, note=""):
        if on_captcha is not None:
            try:
                on_captcha(page_obj, info, note)
            except Exception:
                pass

    d = detect(page)
    if d["type"] == "none":
        # 只有「确实部署了验证码脚本」且 token 还是空的时候才值得等。
        # 官方 JSP 页面上那个空的 captchaToken 字段不等于有验证码，等它就白白浪费 20 秒。
        if d.get("tokenLen", -1) == 0 and d.get("v3"):
            say(f"{who} 页面挂了验证码脚本、token 尚未生成，等待自动完成…")
            pack(page, d, "v3 隐形验证：token 未生成")
            t0 = time.time()
            while time.time() - t0 < 20:
                if detect(page).get("tokenLen", 0) > 10:
                    say(f"{who} captchaToken 已生成（{int(time.time()-t0)}s）")
                    pack(page, detect(page), "v3 隐形验证：token 已生成")
                    return "solved"
                time.sleep(1.2)
            say(f"{who} ⚠ captchaToken 20 秒仍未生成，仍继续提交（可能被拒）")
        return "none"

    say(f"{who} ⚠⚠ 出现验证码：{d['type']}　{'; '.join(d['detail'])[:140]}")
    pack(page, d, f"验证码出现：{d['type']}")
    if shot:
        try:
            shot(page, "captcha", f"验证码出现：{d['type']}")
        except Exception:
            pass

    if d["type"] == "recaptcha_checkbox":
        if click_checkbox(page):
            say(f"{who} 已自动点「我不是机器人」，看是否直接通过…")
            time.sleep(3)
            d2 = detect(page)
            if d2["type"] == "none" and solved(page):
                say(f"{who} 验证码已通过（自动）")
                pack(page, d2, "勾选框自动通过")
                return "solved"
            pack(page, d2, "自动点击后仍未通过，转人工")
            say(f"{who} 自动点击后仍需交互，转人工")

    beep(6)
    say("=" * 60)
    say(f"{who} ★★★ 需要你操作：在浏览器窗口里完成验证码（{timeout} 秒内）★★★")
    say("=" * 60)

    t0 = time.time()
    last_kind = d["type"]
    while time.time() - t0 < timeout:
        cur = detect(page)
        # 形态变了（勾选框 → 图片挑战）也留一帧，方便事后看它是怎么升级的
        if cur["type"] not in (last_kind, "none") and cur["type"] != last_kind:
            last_kind = cur["type"]
            pack(page, cur, f"验证码形态变为：{cur['type']}")
            say(f"{who} 验证码形态变为 {cur['type']}，已留档")
        if solved(page):
            say(f"{who} 验证码已通过（人工，用时 {int(time.time()-t0)}s）")
            pack(page, cur, f"人工通过，用时 {int(time.time()-t0)}s")
            if shot:
                try:
                    shot(page, "captcha_ok", "验证码通过后")
                except Exception:
                    pass
            return "solved"
        time.sleep(1.5)
    say(f"{who} ✗ 验证码 {timeout} 秒未通过，放弃本轮")
    pack(page, detect(page), "验证码超时未通过")
    if shot:
        try:
            shot(page, "captcha_timeout", "验证码超时")
        except Exception:
            pass
    return "timeout"
