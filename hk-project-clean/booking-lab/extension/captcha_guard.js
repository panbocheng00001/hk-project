// captcha_guard.js —— 验证码分类与交接（主文档 + 任意 iframe 各跑一份）
// ============================================================================
// 能力边界（写在这里是为了让读代码的人一眼知道它不做什么）：
//
//   做：
//     1) scan()      只读 DOM，判定当前是哪一类验证，返回类型 + 证据
//     2) waitToken() 隐形验证（只有 token 字段 + SDK 脚本）等 token 生成
//     3) clickOnce() 勾选框点一次，不做任何重试轰炸
//     4) solve()     按类型分派：能自动的自动，需要判断的立刻交人
//
//   不做：
//     · 不做图像识别、不推算滑块位移、不模拟拖动
//     · 不使用 chrome.debugger 之类能标出 isTrusted 的调试输入通道
//     · 不在失败后反复重试（重试即暴力尝试，对会话状态有害）
//
// 为什么这样切：
//     滑块/点选/图形码是"内容判断题"，正确答案只能由人给出。
//     用算法猜答案的实现在工程上也是劣的——猜错会污染会话，
//     在 20 分钟窗口里一次污染比直接喊人贵得多。
//
// 用法（content.js 里）：
//     const r = await window.__CAPGUARD.solve({ tries: 1 });
//     // r = { ok, type, needHuman, note, tries }
(() => {
  if (window.__CAPGUARD) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const send = (msg) => new Promise((res) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) res({ error: chrome.runtime.lastError.message });
        else res(r || {});
      });
    } catch (e) { res({ error: String(e) }); }
  });

  // ------------------------------------------------------------- 基础判定
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none" && st.opacity !== "0";
  };

  const tokenEl = () => document.querySelector(
    '#captchaToken, input[name="captchaToken"], input[name*="captcha"][type="hidden"]');

  const tokenLen = () => {
    const el = tokenEl();
    return el ? (el.value || "").length : -1;
  };

  const sdkPresent = () => {
    const srcs = [...document.querySelectorAll("script[src]")].map((s) => s.src).join(" ");
    return /recaptcha|hcaptcha|turnstile|mtcaptcha/i.test(srcs)
      // eslint-disable-next-line no-undef
      || !!(window.grecaptcha || window.turnstile || window.hcaptcha);
  };

  // ------------------------------------------------------------- 分类
  // 顺序即优先级：先用最硬的结构特征定型，软信号只做兜底。
  function scan() {
    const out = {
      type: "none", detail: [], inFrame: window.top !== window,
      tokenLen: tokenLen(), sdk: sdkPresent(), url: location.href.slice(0, 160)
    };
    const push = (t, d) => {
      if (out.type === "none") out.type = t;
      if (d) out.detail.push(d);
    };
    const q = (s) => document.querySelector(s);

    // 1) 图片点选类（bframe / hcaptcha / turnstile）
    if (q('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]')) {
      push("image_grid", "reCAPTCHA 图片挑战");
    }
    if (q('iframe[src*="hcaptcha.com"], .h-captcha')) push("image_grid", "hCaptcha");
    if (q('iframe[src*="challenges.cloudflare.com"], .cf-turnstile')) {
      push("image_grid", "Cloudflare Turnstile");
    }

    // 2) 滑块类：canvas + 拖动类文案
    const canvas = q("canvas");
    const text = (document.body && document.body.innerText || "").slice(0, 4000);
    if (canvas && /拖动|滑块|拼图|重合|按住|滑动/.test(text)) {
      push("slider", "canvas + 拖动文案");
    }
    if (q('[class*="mtcap"], iframe[src*="mtcaptcha"]')) push("slider", "MTCaptcha");

    // 3) 勾选框：anchor iframe 或容器，且没有 bframe（有 bframe 说明已升级成图片题）
    if (q('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"], .g-recaptcha')) {
      push("checkbox", "reCAPTCHA 勾选框");
    }

    // 4) 图形字符码
    for (const img of document.querySelectorAll("img")) {
      const sig = (img.getAttribute("src") || "") + " " + (img.id || "") + " " + (img.alt || "");
      if (!/captcha|verify|checkcode|validcode|randcode|yzm|authcode/i.test(sig)) continue;
      if (img.getBoundingClientRect().width > 30) { push("image_code", "图形码 img"); break; }
    }
    for (const el of document.querySelectorAll("input")) {
      const n = ((el.name || "") + " " + (el.id || "")).trim();
      if (el.type === "hidden" || !n) continue;
      if (/captcha|verifycode|checkcode|validcode|yzm|authcode/i.test(n)) {
        push("image_code", "图形码输入框"); break;
      }
    }

    // 5) 兜底：SDK 在、token 字段空、又没有可见挑战面 → 隐形验证
    if (out.type === "none" && out.sdk && out.tokenLen === 0) out.type = "token_only";
    return out;
  }

  // ------------------------------------------------------------- 自动路径
  // 只做一件事：等 token。不推断、不构造答案。
  async function waitToken(budgetMs) {
    const t0 = Date.now();
    budgetMs = budgetMs || 20000;
    while (Date.now() - t0 < budgetMs) {
      if (tokenLen() > 10) return { ok: true, waited: Date.now() - t0 };
      await sleep(1200);
    }
    return { ok: false, waited: Date.now() - t0 };
  }

  // 勾选框：点一次。点不到或点了没过就返回 false，由调用方交人。
  async function clickOnce() {
    const candidates = [
      'iframe[src*="recaptcha/api2/anchor"]',
      'iframe[src*="recaptcha/enterprise/anchor"]',
      ".g-recaptcha", ".cf-turnstile", ".h-captcha"
    ];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (!el) continue;
      try {
        // 同一文档内的容器可以点；跨域 iframe 内部点不到，交给人
        if (el.tagName === "IFRAME") {
          const r = el.getBoundingClientRect();
          el.dispatchEvent(new MouseEvent("click", {
            bubbles: true, clientX: r.x + r.width / 2, clientY: r.y + r.height / 2
          }));
          return { ok: true, how: "iframe-click", crossOrigin: true };
        }
        el.click();
        return { ok: true, how: "container-click", crossOrigin: false };
      } catch (e) { /* 继续试下一个 */ }
    }
    return { ok: false, how: "no-target" };
  }

  // ------------------------------------------------------------- 分派
  async function solve(opts) {
    opts = opts || {};
    const s = scan();
    const tries = [];

    if (s.type === "none") {
      // 无可见挑战面，但 token 字段空且 SDK 在 → 等一会儿（隐形验证）
      if (s.tokenLen === 0 && s.sdk) {
        const w = await waitToken(opts.tokenBudgetMs || 20000);
        tries.push("隐形验证等待 " + Math.round(w.waited / 1000) + "s → " +
                   (w.ok ? "token 已生成" : "token 未生成"));
        return { ok: w.ok, type: "token_only", tries };
      }
      return { ok: true, type: "none", note: "没有验证码", tries };
    }

    if (s.type === "token_only") {
      const w = await waitToken(opts.tokenBudgetMs || 20000);
      tries.push("隐形验证等待 " + Math.round(w.waited / 1000) + "s");
      return { ok: w.ok, type: s.type, tries };
    }

    if (s.type === "checkbox") {
      const c = await clickOnce();
      tries.push("勾选框：" + c.how);
      if (!c.ok) return { ok: false, type: s.type, needHuman: true, tries };
      await sleep(2500);
      const ok = scan().type === "none" && tokenLen() !== 0;
      if (ok) return { ok: true, type: s.type, how: c.how, tries };
      // 点了没过就是升级成图片题了，立刻交人，绝不第二次点
      return { ok: false, type: s.type, needHuman: true, note: "点击后未通过", tries };
    }

    // 需要内容判断的形态：直接交人，不做任何自动动作
    const needHuman = ["slider", "image_code", "image_grid"].includes(s.type);
    return {
      ok: false, type: s.type, needHuman: needHuman,
      note: "该形态需要人工完成", tries
    };
  }

  // 给调用方判断"现在该不该喊人"用
  function needsHuman() {
    const s = scan();
    return s.type !== "none" && s.type !== "token_only";
  }

  window.__CAPGUARD = {
    scan, solve, waitToken, clickOnce, needsHuman, tokenLen, sdkPresent
  };
})();
