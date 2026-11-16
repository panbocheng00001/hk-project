// content.js —— 页面内状态机（全程不经过 CDP）
// 思路：每 1.2 秒判断一次「现在在哪一步」，执行该步动作，然后等页面自己跳。
// 所有导航都由页面自身的点击/跳转完成，扩展不调用任何调试协议。

(() => {
  // 可能被注入多次（tab 更新/SW 唤醒），只跑一份
  if (window.__tdabsInjected) return;
  window.__tdabsInjected = true;

  const API = "http://127.0.0.1:8765";
  const TICK = 1200;

  // 基础工具提前定义，避免被后面函数引用时出现时序问题
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const sleep2 = sleep;
  const rand = (a, b) => a + Math.random() * (b - a);

  const state = {
    cfg: null,
    done: new Set(),      // 去重：同一步同一动作只做一次
    lastStage: "",
    lastBeat: 0,
    queueStart: Date.now(),
    enterClicked: false,
    captchaReported: false,
    captchaTries: 0,
    captchaCooldown: 0,
    humanAsked: false,
    mtReported: false,
    busy: false,
    // reload 会重建 state：从 sessionStorage 恢复重载计数，避免无限刷新
    reloadTries: (() => {
      try { return parseInt(sessionStorage.getItem("tdabs_reload_tries") || "0", 10) || 0; }
      catch (e) { return 0; }
    })(),
    lastReload: 0,
    reloadGaveUp: false,
    lastProbe: 0,
    probeTries: 0,
    probeFast: false,
    probeHit: false,
    inst: (() => {
      try {
        const m = /inst=([A-Za-z0-9_-]+)/.exec(location.hash || "");
        return m ? m[1] : "";
      } catch (e) { return ""; }
    })(),
    stopped: false,
    logged: new Set(),
    // ── country 阶段：SelectCountry 是 React 卡片，document_start 注入时 body 还是空的
    countrySince: 0,
    countryRendered: false,
    countryWaitLogged: false,
    countryPickAt: 0,
    countryPicked: 0,
    countryMissLogged: false,
    // country 空壳 reload（sessionStorage 续命，reload 后还能记住次数）
    countryReloads: (() => {
      try { return parseInt(sessionStorage.getItem("tdabs_country_reloads") || "0", 10) || 0; }
      catch (e) { return 0; }
    })(),
    countryReloadAt: 0,
    countryGiveUp: false,
    // ── 步骤序号：每一步都入账本 + 让后台截图
    stepNo: 0
  };

  // ------------------------------------------------------------ 基础工具
  const send = (msg) => new Promise((res) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) res({ error: chrome.runtime.lastError.message });
        else res(r || {});
      });
    } catch (e) { res({ error: String(e) }); }
  });

  // ------------------------------------------------------------ 每步状态快照
  // 截图只能「看」，状态才能「分析」：这一步 URL 是什么、页面给没给表单、
  // 每个控件的 name/value 是什么、有哪些可点元素、iframe 几个、滚动在哪。
  // 统一走 {type:"step"} → background 补 cookie + 截图 → POST /step → runs/runlog/。
  function collectControls() {
    const out = [];
    for (const el of document.querySelectorAll("input,select,textarea")) {
      const t = (el.type || el.tagName).toLowerCase();
      let val = String(el.value == null ? "" : el.value).slice(0, 300);
      if (t === "file") {
        const f = el.files && el.files[0];
        val = f ? (f.name + " (" + f.size + "B)") : "";
      }
      out.push({
        tag: el.tagName.toLowerCase(), type: t,
        name: el.name || "", id: el.id || "",
        value: val,
        checked: ("checked" in el) ? !!el.checked : null,
        disabled: !!el.disabled, visible: visible(el),
        label: (el.closest("label")?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 60)
      });
      if (out.length >= 200) break;
    }
    return out;
  }

  function collectInteractive() {
    const out = [];
    for (const el of document.querySelectorAll(
        "button,a,[role=button],input[type=button],input[type=submit],li,label,td")) {
      if (!visible(el)) continue;
      const t = (el.innerText || el.value || "").replace(/\s+/g, " ").trim();
      if (!t) continue;
      out.push({ tag: el.tagName.toLowerCase(), text: t.slice(0, 60),
                 id: el.id || "", cls: String(el.className || "").slice(0, 60) });
      if (out.length >= 120) break;
    }
    return out;
  }

  function collectState() {
    const body = document.body;
    return {
      stage: stageName(),
      url: location.href,
      title: document.title,
      ready: document.readyState,
      scroll: [Math.round(window.scrollX), Math.round(window.scrollY)],
      viewport: [window.innerWidth, window.innerHeight],
      bodyChildren: body ? body.childElementCount : -1,
      textLen: body ? (body.innerText || "").length : 0,
      text: body ? (body.innerText || "").replace(/\s+/g, " ").slice(0, 4000) : "",
      controls: collectControls(),
      interactive: collectInteractive(),
      iframes: [...document.querySelectorAll("iframe")].map((f) => ({
        src: String(f.src || "").slice(0, 200), w: f.clientWidth, h: f.clientHeight
      })),
      inst: state.inst || ""
    };
  }

  const LAST_STATE_AT = {};
  function reportState(label, opts) {
    const o = opts || {};
    const now = Date.now();
    // 同一 label 3 秒内只记一次（tick 每秒都在跑，不然会写成几万条）
    if (!o.force && now - (LAST_STATE_AT[label] || 0) < 3000) return;
    LAST_STATE_AT[label] = now;
    let st;
    try { st = collectState(); } catch (e) { return; }
    st.step = label;
    st.stepNo = state.stepNo || 0;
    if (o.dom) {
      try { st.dom = document.documentElement.outerHTML.slice(0, 600000); } catch (e) { /* ignore */ }
    }
    send({ type: "step", data: st }).catch(() => {});
  }

  async function log(msg, stage) {
    const s = stage || stageName();
    const key = s + "|" + msg;
    if (state.logged.has(key)) return;
    state.logged.add(key);
    send({ type: "status", data: { stage: s, msg, url: location.href.slice(0, 120) } });
  }

  // 「第 N 步」——每一步做两件事：入账本（journal）+ 让后台截一张图。
  // 后台看到 step 字段就抓当前页面存 runs/steps/日期/，事后能逐步回放；
  // 这也是「中途验证码要人验证」时最先看的东西（最后一张图就是现场）。
  const STEP_SEEN = new Set();
  function step(name, extra) {
    const s = stageName();
    const key = s + "|" + name;
    if (STEP_SEEN.has(key)) return;
    STEP_SEEN.add(key);
    state.stepNo = (state.stepNo || 0) + 1;
    send({ type: "status", data: Object.assign({
      stage: s,
      msg: "第 " + state.stepNo + " 步：" + name,
      step: String(name).slice(0, 60),
      stepNo: state.stepNo,
      url: location.href.slice(0, 160)
    }, extra || {}) });
    // 同一个动作同时留一份结构化状态（含表单控件值/DOM），供事后逐步分析
    reportState("step:" + name, {
      dom: /country|form|confirm|otp|calendar|mtcaptcha|waf|填|上传/i.test(String(name))
    });
  }

  const once = (key) => {
    if (state.done.has(key)) return false;
    state.done.add(key);
    return true;
  };

  const text = () => (document.body ? document.body.innerText : "");

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return !!(r.width && r.height) && getComputedStyle(el).visibility !== "hidden";
  }

  function click(el, why) {
    if (!el) return false;
    try {
      el.scrollIntoView({ block: "center" });
      el.click();
      if (why) { log("点击：" + why); step("点击 " + why); }
      return true;
    } catch (e) { return false; }
  }

  function findByText(selectors, labels) {
    const want = labels.map((s) => s.replace(/\s+/g, "").toUpperCase());
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        const t = (el.innerText || el.value || "").replace(/\s+/g, "").trim();
        if (!t) continue;
        const up = t.toUpperCase();
        if (want.some((w) => up === w || up.includes(w))) {
          if (visible(el)) return el;
        }
      }
    }
    return null;
  }

  function setNativeValue(el, value) {
    const proto = el.tagName === "TEXTAREA"
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fieldHint(el) {
    const lab = el.closest("label")?.innerText
      || (el.id && document.querySelector(`label[for="${el.id}"]`)?.innerText)
      || "";
    const holder = el.closest(".MuiFormControl-root") || el.closest("tr") || el.parentElement;
    return [lab, holder ? holder.innerText : "", el.name || "", el.id || "",
            el.placeholder || "", el.getAttribute("aria-label") || ""]
      .join(" ").toLowerCase();
  }

  // ------------------------------------------------------------ 阶段识别
  // MTCaptcha：业务页的第三道验证。它可能是「无感」（后台静默生成 captchaToken，
  // 不需要人操作），也可能弹出点选/滑块。这里只做识别与上报，不擅自点击——
  // 形态未知时贸然操作反而会被判异常。
  function hasMTCaptcha() {
    try {
      for (const s of document.querySelectorAll("script[src]")) {
        if (/mtcaptcha/i.test(s.src || "")) return true;
      }
      for (const f of document.querySelectorAll("iframe[src]")) {
        if (/mtcaptcha/i.test(f.src || "")) return true;
      }
      for (const el of document.querySelectorAll("[class*=mtcap], [id*=mtcap]")) {
        const r = el.getBoundingClientRect();
        if (r.width > 40 && r.height > 20) return true;
      }
    } catch (e) { /* ignore */ }
    return false;
  }

  function stageName() {
    const u = location.href;
    // 华为 WAF 的行为验证：16:30 高峰期实测对所有客户端都会弹，不通过就进不了队。
    // 判据一：出现「拖动滑块」类文案（中/英）。
    // 判据二（2026-11-16 17:19 补）：挑战页 lang=en，innerText 里未必有上面那些词，
    //   但页面一定有且只有一个大 canvas —— 业务页没有 canvas，所以按 canvas 兜底最稳。
    if (/调整滑块|拖动滑块|拖曳滑块|滑动滑块|adjust\s+the\s+slider|drag\s*the\s*slider|slide\s*to\s*verify|two\s+shapes/i.test(text())) {
      return "waf_captcha";
    }
    try {
      const cv = document.querySelector("canvas");
      if (cv) {
        const r = cv.getBoundingClientRect();
        if (r.width > 150 && r.height > 100) return "waf_captcha";
      }
    } catch (e) { /* ignore */ }
    if (u.includes("abs4.td.gov.hk")) return "queue";
    if (hasMTCaptcha()) return "mtcaptcha";
    if (document.querySelector('input[name="conditionsCheck"]')) return "entry";
    if (document.querySelector('input[type="radio"][value="withOutIas"]')) return "auth";
    if (u.includes("SelectCountry")) return "country";
    if (document.querySelector("#continueBtn1")) return "form";
    if (u.includes("AdminServlet") || u.includes("AdminServlet_tchinese")) return "form";
    if (findOtpInput()) return "otp";
    if (findByText(["button", "input[type=button]", "input[type=submit]"],
                   ["确认", "遞交", "递交", "提交", "CONFIRM", "SUBMIT"])) return "confirm";
    if (document.querySelector("button.MuiPickersDay-root") || /\d{1,2}[:：]\d{2}/.test(text())) return "calendar";
    return "unknown";
  }

  function findOtpInput() {
    for (const el of document.querySelectorAll('input[type=text],input[type=tel],input:not([type])')) {
      const h = fieldHint(el);
      if (/otp|一次性|驗證碼|验证码|password|密碼|密码/.test(h) && visible(el)) return el;
    }
    return null;
  }

  // ------------------------------------------------------------ 各阶段动作
  async function doEntry() {
    const cb = document.querySelector('input[name="conditionsCheck"]');
    if (cb && !cb.checked && once("entry:agree")) {
      await sleep(rand(280, 900));            // 先看一眼再勾，别 0ms 反应
      click(cb, "勾选同意条款");
      await sleep(rand(450, 1300));
    }
    if (once("entry:book")) {
      await sleep(rand(300, 900));
      const btn = findByText(["button", "a", "[role=button]"],
                             ["预约", "預約", "MAKEANAPPOINTMENT"]);
      if (btn) click(btn, "预约");
    }
  }

  async function doAuth() {
    const r = document.querySelector('input[type="radio"][value="withOutIas"]');
    if (r && !r.checked && once("auth:pick")) {
      await sleep(rand(350, 1100));
      click(r, "不使用智方便");
      await sleep(rand(300, 800));
    }
    if (once("auth:next")) {
      await sleep(rand(250, 900));
      const btn = findByText(["button", "input[type=button]", "input[type=submit]"],
                             ["继续", "繼續", "CONTINUE", "NEXT"]);
      if (btn) click(btn, "继续(认证)");
    }
  }

  // ------------------------------------------------------------ 签发地（SelectCountry）
  // 2026-11-11 死在这：document_start 注入时 React 还没渲染，body 是空的，
  // 于是快照存成 <body></body>（39 字节），文本兜底也只跑一次、且跑在渲染之前 ——
  // 之后每 8 秒「重试本阶段动作」复用的还是那份空 DOM，等于永远点不到「内地」。
  // 现在：等渲染 → 渲染后再存快照 → 每 1.5s 重试文本点击，直到页面自己跳走。
  function interactiveCount() {
    try {
      return document.querySelectorAll(
        "input,button,label,a,li,[role],[onclick],[class*=card],[class*=item],td,p").length;
    } catch (e) { return 0; }
  }

  function countryRendered() {
    if (!document.body || document.body.childElementCount === 0) return false;
    return interactiveCount() > 8 && (document.body.innerText || "").trim().length > 4;
  }

  function countryCandidates() {
    const out = [];
    for (const el of document.querySelectorAll(
        "label,button,li,a,div,span,td,p,h1,h2,h3,h4,[role=radio],[role=option],[role=button]")) {
      if (!visible(el)) continue;
      const t = (el.innerText || "").replace(/\s+/g, " ").trim();
      if (!t || t.length > 18) continue;      // 最内层命中，不要包住整块的容器
      out.push({ el, t });
    }
    return out;
  }

  async function doCountry() {
    // ① 等渲染
    if (!state.countryRendered) {
      if (!state.countrySince) state.countrySince = Date.now();
      if (!countryRendered()) {
        const waited = Date.now() - state.countrySince;
        if (waited > 30000 && !state.countryWaitLogged) {
          state.countryWaitLogged = true;
          log("country 页 30s 仍未渲染（body 孩子数="
              + (document.body ? document.body.childElementCount : -1)
              + "，可交互元素=" + interactiveCount() + "），继续等（main.js 约 290KB，慢时需 25s+）", "country");
        }
        // 空壳/挑战：SelectCountry 是 SPA，正常 2 秒内渲染完毕。
        // 2026-11-12 16:32 实测：高峰期这一页会先给一个 0 子元素的空壳（WAF 挑战刚过/未过），
        // 旧逻辑只会干等，窗口就钉死在这里。现在按 6 秒节奏 reload（最多 8 次），
        // 与 runbook 的「6 秒重试一次，别连按 F5」一致。
        const emptyShell = document.readyState === "complete"
          && (!document.body || document.body.childElementCount === 0);
        // 2026-11-13 22:47 实测：SelectCountry 的 main.fca89098.js 有 290 KB，
        // 夜间直连下载要 25 秒（白天更快，所以 11-11 没暴露）。旧逻辑 30 秒无条件 reload，
        // 正好在 JS 下到一半时把它 ERR_ABORTED 掉 —— 每 15/30 秒重来一次，永远下不完。
        // 现在：空壳给 45 秒、非空壳给 90 秒，让 290 KB 有足够时间落盘并执行。
        // 判据也加上「#root 是否仍为空」——SPA 骨架到了但 root 空 = JS 还在跑，不算失败。
        const rootEmpty = (() => {
          try {
            const r = document.getElementById("root");
            return !r || r.childElementCount === 0;
          } catch (e) { return false; }
        })();
        const jsStillLoading = emptyShell || rootEmpty;
        if ((waited > 45000 && jsStillLoading) || waited > 90000) {
          const n = (state.countryReloads || 0) + 1;
          state.countryReloads = n;
          try { sessionStorage.setItem("tdabs_country_reloads", String(n)); } catch (e) { /* ignore */ }
          if (n <= 4 && Date.now() - (state.countryReloadAt || 0) > 20000) {
            state.countryReloadAt = Date.now();
            log(`country 页空壳（孩子=${document.body ? document.body.childElementCount : -1}），`
                + `第 ${n} 次 reload`, "country");
            setTimeout(() => location.reload(), 250);
          } else if (n > 8 && !state.countryGiveUp) {
            state.countryGiveUp = true;
            log("country 页 reload 4 次仍未渲染 —— 停手，避免把 IP 信誉刷没", "country");
          }
        }
        return;                                  // 下一 tick 再看
      }
      state.countryRendered = true;
      const all = countryCandidates();
      log("country 已渲染：可点元素 " + all.length + " 个 → "
          + all.slice(0, 12).map((c) => c.t).join(" | "), "country");
      let html = "";
      try { html = document.documentElement.outerHTML.slice(0, 400000); } catch (e) { /* ignore */ }
      send({ type: "snapshot", data: { stage: "country", url: location.href,
                                       fields: {}, html: html, inst: state.inst || "" } });
    }

    // ② 每 1.5s 试一次「点内地」，直到页面跳走或试满 12 次（试满就停手等人）
    if (Date.now() - (state.countryPickAt || 0) > 1500
        && (state.countryPicked || 0) < 12) {
      state.countryPickAt = Date.now();
      const cands = countryCandidates()
        .filter((c) => /中国内地|中國內地|内地|內地|mainland/i.test(c.t))
        .sort((a, b) => a.t.length - b.t.length);
      if (cands.length) {
        const hit = cands[0];
        state.countryPicked = (state.countryPicked || 0) + 1;
        try { hit.el.scrollIntoView({ block: "center" }); } catch (e) { /* ignore */ }
        click(hit.el, "签发地=" + hit.t);
        // React 卡片有时只听 pointer 事件：第二次起补一轮完整指针序列
        if (state.countryPicked === 2) {
          try {
            for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
              hit.el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
            }
            hit.el.click();
            log("已补 pointer 序列再点一次「" + hit.t + "」", "country");
          } catch (e) { /* ignore */ }
        }
        await sleep(rand(350, 700));
      } else if (!state.countryMissLogged) {
        state.countryMissLogged = true;
        const all = countryCandidates();
        log("没找到「内地」；当前可点元素 " + all.length + " 个："
            + all.slice(0, 20).map((c) => c.t).join(" | "), "country");
        log("country 页面前 200 字：" + text().replace(/\s+/g, " ").slice(0, 200), "country");
        let html = "";
        try { html = document.documentElement.outerHTML.slice(0, 400000); } catch (e) { /* ignore */ }
        send({ type: "snapshot", data: { stage: "country_miss", url: location.href,
                                         fields: {}, html: html, inst: state.inst || "" } });
      }
    }

    // ③ 选中之后的「继续」
    if (state.countryPicked && once("country:next")) {
      await sleep(rand(500, 1100));
      const btn = findByText(["button", "input[type=button]", "input[type=submit]"],
                             ["继续", "繼續", "CONTINUE"]);
      if (btn) { click(btn, "继续(签发地)"); await sleep(rand(800, 1500)); }
    }
  }

  function buildMapping() {
    const a = (state.cfg && state.cfg.applicant) || {};
    return [
      { keys: ["身份证明文件号码", "證件號碼", "identitydocumentnumber"], val: a.doc_no },
      { keys: ["驾驶执照号码", "駕駛執照號碼", "drivinglicence", "drivinglicense"], val: a.dl_no },
      { keys: ["电邮", "電郵", "email"], val: a.email },
      { keys: ["中文姓名", "chinesename"], val: a.name_cn }
    ];
  }

  async function doForm() {
    const t = text();

    // 一进入 JSP 表单页就先留证据（**不管有没有名额**）：
    // cookie + URL + 所有 hidden 字段 + 页面 HTML。
    // 有名额时要它做参数逆向，没名额时也要它验证「门票到底带不带得动接口」。
    if (once("form:snapshot")) {
      const fields = {};
      for (const el of document.querySelectorAll("input,select,textarea")) {
        if (el.name) fields[el.name] = el.value;
      }
      let html = "";
      try { html = document.documentElement.outerHTML.slice(0, 400000); } catch (e) { html = ""; }
      const noQuota = t.includes("没有可供预约的时段") || /no available/i.test(t);
      send({
        type: "snapshot",
        data: {
          stage: noQuota ? "form_noquota" : "form_enter",
          url: location.href,
          title: document.title,
          fields,
          html,
          inst: state.inst || ""
        }
      }).then((r) => {
        log("快照结果：" + JSON.stringify(r || {}).slice(0, 120), "form");
      });
      log("已保存会话快照（cookie + 表单 + HTML）", "form");
    }

    if (t.includes("没有可供预约的时段") || /no available/i.test(t)) {
      if (once("form:noquota")) {
        log("系统提示：现时没有可预约时段", "form");
        send({ type: "result", data: { stage: "no_quota", url: location.href } });
      }
      // 2026-11-16 18:20 实测：无名额结果页本身带「查询各牌照事务处预约情况」按钮，
      // 点它只发一次 POST（cmdGetOfficeQuotaDIAction），不重新导航、不吃 WAF 票。
      // 回流号是零散放的，所以按 75s 间隔重查（低于 throttle 的硬下限，不会被判刷）。
      const nowTs = Date.now();
      if (!state.requeryAt || nowTs - state.requeryAt > 75000) {
        const qbtn = findByText(["a", "button", "input[type=submit]", "input[type=button]"],
                                ["查询各牌照", "查詢各牌照", "预约情况", "預約情況"]);
        if (qbtn) {
          state.requeryAt = nowTs;
          log("无名额 → 重新查询名额（75s 一次，等回流）", "form");
          try { qbtn.click(); } catch (e) { /* ignore */ }
          send({ type: "status", data: { stage: "requery", msg: "重查名额", url: location.href } });
        }
      }
      return;
    }

    if (!once("form:fill")) return;      // 填表只做一轮，避免反复覆盖

    const mapping = buildMapping().filter((m) => m.val);
    let filled = 0;
    for (const el of document.querySelectorAll("input,textarea,select")) {
      if (!visible(el) || ["hidden", "file", "checkbox", "radio", "button", "submit"].includes(el.type)) continue;
      const h = fieldHint(el);
      const hit = mapping.find((m) => m.keys.some((k) => h.includes(k.toLowerCase())));
      if (!hit || !hit.val) continue;
      if (el.tagName === "SELECT") {
        for (const opt of el.options) {
          if (opt.text.includes(hit.val) || opt.value === hit.val) {
            el.value = opt.value;
            el.dispatchEvent(new Event("change", { bubbles: true }));
            filled++;
            break;
          }
        }
      } else {
        setNativeValue(el, hit.val);
        filled++;
      }
      log("填入 " + hit.keys[0] + " → " + hit.val);
      step("填入 " + hit.keys[0]);
      await sleep(220);
    }
    log("已填字段数：" + filled, "form");

    // 图片：从本地服务取字节，用 DataTransfer 塞进 input[type=file]
    await uploadAll();

    // OTP（若这一页就要）
    await tryOtp();

    await sleep(600);
    if (once("form:next")) {
      const btn = findByText(["button", "input[type=button]", "input[type=submit]"],
                             ["继续", "繼續", "CONTINUE", "NEXT"]);
      if (btn) click(btn, "继续(提交资料)");
    }
  }

  async function uploadAll() {
    const a = (state.cfg && state.cfg.applicant) || {};
    const files = [a.doc_file, a.dl_file].filter(Boolean);
    const inputs = [...document.querySelectorAll('input[type="file"]')].filter(visible);
    if (!inputs.length) { log("本页没有文件上传框", "form"); return; }
    for (let i = 0; i < Math.min(inputs.length, files.length); i++) {
      const name = files[i];
      const r = await send({ type: "file", name });
      if (!r || !r.ok) { log("取图失败：" + name + " " + (r && r.error ? r.error : ""), "form"); continue; }
      const bin = atob(r.b64);
      const arr = new Uint8Array(bin.length);
      for (let k = 0; k < bin.length; k++) arr[k] = bin.charCodeAt(k);
      const fname = name.split("/").pop();
      try {
        const f = new File([arr], fname, { type: r.ctype || "image/jpeg" });
        const dt = new DataTransfer();
        dt.items.add(f);
        inputs[i].files = dt.files;
        inputs[i].dispatchEvent(new Event("change", { bubbles: true }));
        log("已上传 " + fname + "（" + Math.round(arr.length / 1024) + " KB）", "form");
        step("上传 " + fname);
      } catch (e) {
        log("上传失败 " + fname + "：" + e.message, "form");
      }
      await sleep(350);
    }
  }

  async function tryOtp() {
    const box = findOtpInput();
    if (!box) return;
    const r = await send({ type: "otp", who: state.inst || "" });
    if (r && r.code) {
      setNativeValue(box, r.code);
      log("已填入 OTP（" + (r.file || "otp") + "）", "otp");
      step("填入 OTP");
    } else {
      log("在等 OTP（" + (r && r.file ? r.file : "otp") + " 还没出现）", "otp");
    }
  }

  async function doCalendar() {
    // 办事处（按优先序）
    if (once("cal:bureau")) {
      const pref = (state.cfg && state.cfg.bureau_preference) || [];
      for (const name of pref) {
        const el = findByText(["button", "a", "[role=button]", "label", "div"], [name]);
        if (el && visible(el)) { click(el, "办事处：" + name); await sleep(1200); break; }
      }
    }
    // 最早可选日期
    if (once("cal:day")) {
      const days = [...document.querySelectorAll("button.MuiPickersDay-root,[role=gridcell] button,td button")]
        .filter((d) => visible(d) && !d.disabled && /^\d{1,2}$/.test((d.innerText || "").trim()));
      days.sort((x, y) => {
        const ax = x.getAttribute("aria-label") || "", ay = y.getAttribute("aria-label") || "";
        return ax.localeCompare(ay);
      });
      if (days.length) { click(days[0], "最早日期 " + (days[0].innerText || "").trim()); await sleep(1200); }
    }
    // 最早时段
    if (once("cal:slot")) {
      const slots = [...document.querySelectorAll("button,label,li")].filter((el) => {
        if (!visible(el) || el.disabled) return false;
        return /^\d{1,2}[:：]\d{2}/.test((el.innerText || "").trim());
      });
      if (slots.length) { click(slots[0], "最早时段 " + (slots[0].innerText || "").trim()); await sleep(800); }
      else {
        const radios = [...document.querySelectorAll('input[type="radio"]')].filter((r) => !r.disabled);
        if (radios.length) { click(radios[0], "时段(radio)"); await sleep(600); }
      }
    }
    if (once("cal:next")) {
      const btn = findByText(["button", "input[type=button]", "input[type=submit]"],
                             ["继续", "繼續", "CONTINUE", "NEXT"]);
      if (btn) click(btn, "继续(选时段)");
    }
  }

  async function doConfirm() {
    // 递交前再存一次快照：此时 cookie 可能已经轮换（JSESSIONID / WAF token 都会变），
    // 后面用接口接管必须用最新的那一份。
    if (once("confirm:snapshot")) {
      const fields = {};
      for (const el of document.querySelectorAll("input,select,textarea")) {
        if (el.name) fields[el.name] = el.value;
      }
      let html = "";
      try { html = document.documentElement.outerHTML.slice(0, 400000); } catch (e) { html = ""; }
      send({ type: "snapshot", data: { stage: "confirm", url: location.href, fields, html, inst: state.inst || "" } });
      send({ type: "flushnet" });     // 把还没落盘的网络记录刷一次
      log("已保存递交前的会话快照（cookie 会一并导出）", "confirm");
    }
    await tryOtp();
    if (once("confirm:submit")) {
      const btn = findByText(["button", "input[type=button]", "input[type=submit]", "a"],
                             ["确认", "確認", "递交", "遞交", "提交", "确定", "CONFIRM", "SUBMIT"]);
      if (btn) click(btn, "确认递交");
    }
    const t = text();
    if (/(确认编号|预约编号|確認編號|已成功|Confirmation|Reference)/.test(t) && once("confirm:done")) {
      log("★ 疑似预约成功", "confirm");
      send({ type: "result", data: { stage: "done", url: location.href, text: t.slice(0, 600) } });
    }
  }

  // ------------------------------------------------------------ 滑块验证
  // 华为 WAF 行为验证：「拖动滑块，让实心图形和空心图形完全重合」。
  // 流程：截图 canvas → Python 用 OpenCV 算 dx → 这里模拟拖动 → 再截图校验 → 失败重试。
  // 说明：扩展内合成的事件 isTrusted=false；若服务端严格校验，
  //       会把坐标回传给 Python 侧改走 SendInput（见 slider.py 的 plan B）。
  function pickHandle() {
    const sels = ["input[type=range]", ".slider-handle", "[class*=slider-handle]",
                  "[class*=handle]", "[class*=slider]"];
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) {
        const r = el.getBoundingClientRect();
        if (r.width > 8 && r.height > 8 && r.width < 120) return el;
      }
    }
    return null;
  }

  function dragBy(el, dx) { return humanDrag(el, dx); }

  function fireEv(el, type, x, y, buttons) {
    try {
      el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, composed: true,
        clientX: x, clientY: y, pointerId: 1, isPrimary: true,
        button: 0, buttons: buttons, pointerType: "mouse"
      }));
    } catch (e) { /* ignore */ }
    const m = type === "pointerdown" ? "mousedown"
            : type === "pointerup" ? "mouseup" : "mousemove";
    try {
      el.dispatchEvent(new MouseEvent(m, {
        bubbles: true, cancelable: true, composed: true,
        clientX: x, clientY: y, button: 0, buttons: buttons
      }));
    } catch (e) { /* ignore */ }
  }

  // 像人一样拖滑块。真人拖动的可测特征是：先移过去→悬停→按下→停顿→
  // 加速-匀速-减速→到位微调→再停顿→松手。整段耗时通常 0.6–1.6 秒。
  async function humanDrag(el, dx) {
    const r = el.getBoundingClientRect();
    const x0 = r.x + r.width / 2;
    const y0 = r.y + r.height / 2;
    const dir = dx >= 0 ? 1 : -1;

    // ① 从一侧把指针移过来（真人不会凭空出现在滑块上）
    const fromX = x0 - dir * rand(90, 240);
    const fromY = y0 + rand(-45, 45);
    await sleep2(rand(150, 380));
    const approach = 5 + Math.floor(rand(0, 4));
    for (let i = 1; i <= approach; i++) {
      const t = i / approach, e = t * t * (3 - 2 * t);
      fireEv(el, "pointermove", fromX + (x0 - fromX) * e, fromY + (y0 - fromY) * e, 0);
      await sleep2(rand(11, 26));
    }

    // ② 悬停，再按下；按下后不是立刻动
    await sleep2(rand(200, 560));
    fireEv(el, "pointerdown", x0, y0, 1);
    await sleep2(rand(90, 280));

    // ③ 主轨迹：加速—匀速—减速；每一步之间的间隔本身也是抖动的
    const totalMs = rand(560, 1150);
    const steps = Math.max(26, Math.min(96, Math.round(totalMs / 15)));
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const k = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // ease-in-out
      let x = x0 + dx * k;
      const y = y0 + Math.sin(t * Math.PI * 2.1) * rand(0.4, 1.2) + rand(-1.5, 1.5);
      if (t > 0.1 && t < 0.55 && Math.random() < 0.06) x -= dir * rand(1, 3);  // 偶发回拉
      fireEv(el, "pointermove", x, y, 1);
      await sleep2(Math.max(7, (totalMs / steps) * rand(0.65, 1.45)));
      if (Math.random() < 0.07) await sleep2(rand(35, 110));                  // 中途顿一下
    }

    // ④ 到位后小幅微调，再停顿，最后松手
    const finalX = x0 + dx;
    for (let i = 0; i < 2 + Math.floor(rand(0, 3)); i++) {
      fireEv(el, "pointermove", finalX + rand(-1.6, 1.6), y0 + rand(-1.2, 1.2), 1);
      await sleep2(rand(22, 70));
    }
    fireEv(el, "pointermove", finalX, y0, 1);
    await sleep2(rand(150, 430));
    fireEv(el, "pointerup", finalX, y0, 0);
    try {
      el.dispatchEvent(new MouseEvent("click", {
        bubbles: true, cancelable: true, clientX: finalX, clientY: y0
      }));
    } catch (e) { /* ignore */ }
    return true;
  }

  // 像人一样点按钮：移过去 → 悬停 → 按下 → 抬起 → click
  async function humanClick(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const x = r.x + r.width * rand(0.35, 0.65);
    const y = r.y + r.height * rand(0.35, 0.65);
    for (let i = 1; i <= 4 + Math.floor(rand(0, 3)); i++) {
      const t = i / 6, e = t * t * (3 - 2 * t);
      fireEv(el, "pointermove", x + rand(-30, 30) * (1 - e), y + rand(-18, 18) * (1 - e), 0);
      await sleep2(rand(12, 28));
    }
    await sleep2(rand(120, 340));
    fireEv(el, "pointerdown", x, y, 1);
    await sleep2(rand(55, 140));
    fireEv(el, "pointerup", x, y, 0);
    return true;
  }

  async function doWafCaptcha() {
    // 统一求解器（captcha_solver.js）在场时，识别/求 dx/拖动全部交给它：
    // 主文档和 iframe 用同一套逻辑，且它会自己决定什么时候把合成事件升级成
    // chrome.debugger 的可信事件。这里只负责节流、上报、失败后喊人。
    if (window.__CAPSOLVER) {
      if (Date.now() < (state.captchaCooldown || 0)) return;
      if (!state.captchaReported) {
        state.captchaReported = true;
        let s = { type: "waf_slider", detail: [], canvas: null };
        try { s = window.__CAPSOLVER.scan(); } catch (e) { /* ignore */ }
        const info = {
          stage: "waf_captcha",
          msg: "⚠ 出现验证码（" + (s.detail[0] || s.type) + "），自动求解中",
          url: location.href.slice(0, 140), type: s.type, canvas: s.canvas
        };
        log(info.msg, "waf_captcha");
        send({ type: "status", data: info });
      }
      if (state.captchaTries === 0) await sleep2(rand(1200, 2600));   // 先看一眼再动手
      state.captchaTries++;

      let r = { ok: false, note: "solver 异常" };
      try {
        r = await window.__CAPSOLVER.solve({ tries: 3 });
      } catch (e) {
        r = { ok: false, note: "solver 抛错：" + (e && e.message ? e.message : e) };
      }
      send({ type: "status", data: {
        stage: r.ok ? "captcha_solved" : "waf_captcha",
        msg: (r.ok ? "✅ 验证码已自动通过" : "✗ 自动求解未过（" + (r.note || "") + "）")
             + (r.tries && r.tries.length ? " | " + r.tries.slice(-3).join(" ; ") : ""),
        url: location.href.slice(0, 140), how: r.how || ""
      } });

      if (r.ok) {
        state.captchaTries = 0;
        state.captchaReported = false;
        state.captchaCooldown = 0;
        return;
      }
      if (state.captchaTries >= 2) {
        state.captchaTries = 0;
        state.captchaCooldown = Date.now() + rand(25000, 40000);   // 换个节奏再来，别死循环
        await askHuman();
      } else {
        state.captchaCooldown = Date.now() + rand(8000, 14000);
      }
      return;
    }

    // 旧路径（没有 solver 时）：两次尝试之间留出观察时间，别 1 秒内连拖 3 次
    if (Date.now() < (state.captchaCooldown || 0)) return;
    const c = document.querySelector("canvas");
    const handle = pickHandle();
    if (!c) return;

    // 先上报一次（无论能否自动解，人要知道）
    if (!state.captchaReported) {
      state.captchaReported = true;
      const info = { stage: "waf_captcha", msg: "⚠ 出现滑块验证，尝试自动处理", url: location.href };
      try {
        const r = c.getBoundingClientRect();
        info.canvas = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
        if (handle) {
          const r2 = handle.getBoundingClientRect();
          info.handle = { x: Math.round(r2.x), y: Math.round(r2.y), w: Math.round(r2.width), h: Math.round(r2.height) };
        }
        info.window = { sx: window.screenX, sy: window.screenY, dpr: window.devicePixelRatio };
      } catch (e) { /* ignore */ }
      log(info.msg, "waf_captcha");
      send({ type: "status", data: info });
    }

    // 第一次处理前先「看一看」：真人不会页面一出现验证码就立刻精准操作
    if (state.captchaTries === 0) {
      await sleep2(rand(1600, 3600));
    }

    // 自动解：最多试 3 轮
    if (state.captchaTries >= 3) return;
    state.captchaTries++;

    let dataUrl = "";
    try { dataUrl = c.toDataURL("image/png"); } catch (e) { dataUrl = ""; }
    if (!dataUrl) {
      log("canvas 读取失败（可能被跨域图片污染），转人工", "waf_captcha");
      await askHuman();
      return;
    }

    const rect = c.getBoundingClientRect();
    // 关键：toDataURL 返回的是 canvas 的**位图**尺寸（c.width/c.height），
    // 而 CSS 显示尺寸可能因 DPR 缩放与之不同（例如位图 800、显示 400）。
    // dx 是在位图上量出来的，所以必须按位图宽度换算成滑块行程，否则会差一倍。
    const resp = await send({
      type: "captcha",
      data: {
        dataUrl,
        bmpW: c.width, bmpH: c.height,          // 位图（dx 所在的坐标系）
        cssW: Math.round(rect.width), cssH: Math.round(rect.height),
        dpr: window.devicePixelRatio,
        inFrame: (window.top !== window),
        inst: state.inst || "",
        url: location.href
      }
    });
    if (!resp || !resp.ok || typeof resp.dx !== "number") {
      log("识别失败：" + (resp && resp.error ? resp.error : "no dx") + "，转人工", "waf_captcha");
      await askHuman();
      return;
    }
    log(`识别到偏移 dx=${resp.dx.toFixed(1)}（第 ${state.captchaTries} 次尝试），开始拖动`, "waf_captcha");

    if (!handle) { await askHuman(); return; }
    // canvas 位图像素 → 滑块轨道像素：按「轨道可用行程 / canvas 位图宽度」等比换算。
    // 注意用 bmpW（位图宽），不是 CSS 宽：toDataURL 的坐标系是位图。
    const hr = handle.getBoundingClientRect();
    const track = handle.parentElement;
    const trackW = track ? track.getBoundingClientRect().width : hr.width;
    const range = Math.max(10, trackW - hr.width);
    const bmpW = c.width || Math.max(1, rect.width);
    const scale = range / bmpW;
    let sliderDx = resp.dx * scale;
    // 第 2、3 次尝试时轻微扰动，避免总是落在同一个错误点
    if (state.captchaTries === 2) sliderDx *= 1.06;
    if (state.captchaTries === 3) sliderDx *= 0.94;
    log(`换算：range=${range.toFixed(0)} scale=${scale.toFixed(3)} → 拖动 ${sliderDx.toFixed(1)}px`, "waf_captcha");
    await humanDrag(handle, sliderDx);
    await sleep2(rand(900, 1600));

    // 点「提交」也用人类点击（移过去→悬停→按下→抬起）
    const btn = findByText(["button", "input[type=button]"], ["提交", "確定", "确定", "SUBMIT", "Submit"]);
    if (btn) await humanClick(btn);

    await sleep2(rand(1200, 2200));
    if (stageName() !== "waf_captcha") {
      log("✅ 滑块验证已通过", "waf_captcha");
      state.captchaTries = 0;
      state.captchaReported = false;
      state.captchaCooldown = 0;
    } else if (state.captchaTries >= 3) {
      log("自动处理 3 次仍失败，转人工", "waf_captcha");
      state.captchaCooldown = Date.now() + 30000;   // 半分钟后再试，别死循环
      await askHuman();
    } else {
      state.captchaCooldown = Date.now() + rand(9000, 16000);   // 重试间隔随机化，别踩固定节拍
    }
  }

  async function askHuman() {
    if (state.humanAsked) return;
    state.humanAsked = true;
    send({ type: "status", data: { stage: "need_human", msg: "请手动拖动滑块", inst: state.inst || "", url: location.href } });
    await new Promise((r) => setTimeout(r, 5000));
    state.humanAsked = false;   // 5 秒后允许再次提醒（避免每秒刷屏，又不会永久静音）
  }

  // MTCaptcha 只观察不操作：记录它的形态，供人工判断是否需要接管
  async function doMTCaptcha() {
    if (state.mtReported) return;
    state.mtReported = true;
    const info = { stage: "mtcaptcha", msg: "⚠ 检测到 MTCaptcha（第三道验证）", inst: state.inst || "", url: location.href };
    try {
      const frames = [...document.querySelectorAll("iframe")].map((f) => (f.src || "").slice(0, 160)).filter(Boolean);
      const scripts = [...document.querySelectorAll("script[src]")].map((s) => (s.src || "").slice(0, 160))
        .filter((s) => /mtcaptcha/i.test(s));
      const tok = document.querySelector('input[name="captchaToken"], #captchaToken');
      info.frames = frames.slice(0, 6);
      info.mtScripts = scripts.slice(0, 4);
      info.tokenLen = tok ? (tok.value || "").length : -1;
      // 顺手存一份快照（cookie + 表单 + HTML），MTCaptcha 的细节全在里面
      const fields = {};
      for (const el of document.querySelectorAll("input,select,textarea")) {
        if (el.name) fields[el.name] = el.value;
      }
      let html = "";
      try { html = document.documentElement.outerHTML.slice(0, 400000); } catch (e) { html = ""; }
      send({ type: "snapshot", data: { stage: "mtcaptcha", url: location.href, fields, html, inst: state.inst || "" } });
      send({ type: "status", data: info });
      log("已上报 MTCaptcha 形态并存档", "mtcaptcha");
    } catch (e) { /* ignore */ }
  }

  // 入口还没就绪（16:00–16:30 维护、412/502/空白页）。
  // 关键优化：不做整页 reload（要重下 1.9MB SPA），而是先用后台 fetch 轻量探测，
  // 确认「维护已结束」再一次性进去。这样 16:30:00–16:30:30 这半分钟能试十几次，
  // 而不是原来 3.5–6 秒一次只试五六次。
  const ENTRY_PROBE = "https://abs3.td.gov.hk/?lang=sc";
  const ENTRY_WWW = "https://www.gov.hk/sc/apps/tddiabs.htm";

  async function doRecover() {
    // abs 上的 unknown 有九成是 React 首屏还没渲染完（初始 HTML 只是个壳）。
    // 之前一判 unknown 就跳回 gov.hk，形成 abs3 ⇄ gov.hk 死循环（实测 10:07 每 2 秒一次，
    // 一小时能刷上千次，既打自己也可能被判异常）。
    // 现在的策略：先给 25 秒自己渲染；真有内容就只记心跳；只有空白页才 reload，且最多 3 次。
    let isAbs = false;
    try { isAbs = /^abs[1-4]\.td\.gov\.hk$/i.test(location.hostname); } catch (e) { return; }

    const now = Date.now();
    if (!state.unknownSince) state.unknownSince = now;

    if (!isAbs) {
      // 入口页（gov.hk）本该 3 秒 meta refresh 跳到 abs3。实测带 #inst= 时有时不跳，
      // 以前这里直接 return 就干等了。给 8 秒，不走就自己跳。
      if (now - state.unknownSince > 8000) {
        state.unknownSince = now;
        log("入口页未自动跳转，直接进 abs3", "recover");
        location.href = ENTRY_PROBE;
      }
      return;
    }

    // 维护页：16:00–16:30 官方把 abs3 整个指向 /QitMaint（**有正文**，但永远不是业务阶段）。
    // 旧逻辑把它当「中间态：有内容却没匹配到阶段 → 只观察不导航」，于是 16:30 站点都恢复了，
    // 窗口还钉在维护页上（2026-11-12 16:30 实测：bodyLen 上百字，心跳打了 3 分钟没动作）。
    const _path = location.pathname + location.search;
    const isMaint = /QitMaint/i.test(_path)
      || /暂停服务|暫停服務|系统维护|系統維護|例行维修|例行維修|maintenance/i.test(text());
    if (isMaint) {
      if (now - (state.maintProbe || 0) < 12000) return;
      state.maintProbe = now;
      state.maintTries = (state.maintTries || 0) + 1;
      if (state.maintTries > 60) {
        if (now - (state.lastBeat || 0) > 30000) {
          state.lastBeat = now;
          log(`维护页仍在（已探测 ${state.maintTries - 1} 次），继续等官方`, "recover");
        }
        return;
      }
      const d = new Date();
      const afterWindow = (d.getHours() > 16) || (d.getHours() === 16 && d.getMinutes() >= 30);
      if (afterWindow) {
        log(`维护页第 ${state.maintTries} 次探测：已过 16:30，重新请求 abs3`, "recover");
        location.href = ENTRY_PROBE + "&_r=" + Date.now();   // 带随机参数绕开缓存
      } else if (!state.maintLogged) {
        state.maintLogged = true;
        log("官方维护窗口（16:00–16:30），到点自动重试", "recover");
      }
      return;
    }

    // 2026-11-13 22:50 实测：abs3 的 main.fca89098.js 有 290 KB，实测下载 25.0 秒
    // （约 11.6 KB/s，站点对静态资源明显限速）。旧宽限 25 秒正好卡在「差一点下完」，
    // 于是每 26 秒 reload 一次、每次都被 ERR_ABORTED，永远下不完 —— 首页就永远空白。
    // 放到 60 秒：第一次能落盘进缓存，后续就快了。
    if (now - state.unknownSince < 60000) return;          // SPA 渲染宽限期

    let bodyLen = 0;
    try { bodyLen = (document.body ? document.body.innerText : "").trim().length; } catch (e) { bodyLen = 0; }

    // WAF 挑战页：body 有 5–6KB，但它是 $_ts / nsd 混淆脚本，永远等不到阶段。
    // 以前这里只打心跳 → 窗口就卡死在挑战页（16:52 实测 4 个窗口全卡在这）。
    // 正确处理：识别到挑战特征就 reload。挑战 JS 在导航时才有机会执行并通过。
    let isChallenge = false;
    try {
      const head = (document.documentElement.outerHTML || "").slice(0, 6000);
      isChallenge = head.indexOf("$_ts") >= 0 || head.indexOf("nsd=") >= 0;
    } catch (e) { isChallenge = false; }

    if (isChallenge) {
      if (now - (state.lastProbe || 0) < 12000) return;
      state.lastProbe = now;
      state.probeTries = (state.probeTries || 0) + 1;
      if (state.probeTries > 5) {
        if (now - (state.lastBeat || 0) > 30000) {
          state.lastBeat = now;
          log(`WAF 挑战页 reload ${state.probeTries - 1} 次仍未放行，等人工 F5`, "recover");
        }
        return;
      }
      log(`WAF 挑战页（第 ${state.probeTries} 次 reload）`, "recover");
      location.reload();
      return;
    }

    if (bodyLen >= 40) {
      // 有内容却没匹配到阶段：中间态，只观察不导航
      if (now - (state.lastBeat || 0) > 15000) {
        state.lastBeat = now;
        log(`页面已加载但阶段未识别（${bodyLen} 字），继续观察`, "recover");
      }
      return;
    }

    if (now - (state.lastProbe || 0) < 12000) return;
    state.lastProbe = now;
    state.probeTries = (state.probeTries || 0) + 1;
    const tryNo = state.probeTries;
    if (tryNo > 6) {
      if (now - (state.lastBeat || 0) > 30000) {
        state.lastBeat = now;
        log(`空白页 reload ${tryNo - 1} 次仍空白，停手等人工`, "recover");
      }
      return;
    }
    // 给 probe 加兜底超时：background 侧 fetch 已限 6 秒，这里再兜 8 秒，
    // 保证无论网络怎么挂，tick 都能继续（否则 busy 锁死、整个状态机停摆）。
    const r = await Promise.race([
      send({ type: "probe", url: ENTRY_PROBE }),
      new Promise((res) => setTimeout(() => res({ ok: false, status: 0, error: "probe timeout" }), 8000))
    ]);
    const desc = r && r.status ? `HTTP ${r.status}${r.challenge ? ' 挑战页' : ''}` : "无响应";
    log(`abs 页空白（${desc}），第 ${tryNo} 次 reload`, "recover");
    try { sessionStorage.setItem("tdabs_reload_tries", String(tryNo)); } catch (e) { /* ignore */ }
    location.reload();
  }

  // ------------------------------------------------------------ 主循环
  async function tick() {
    // 加了 await 之后，setInterval 会在上一步还没走完时再次进入，
    // 导致同一步骤被并发执行（实测出现过「第 3 次尝试」其实是三次抢跑）。
    if (state.busy) return;
    state.busy = true;
    try {
      await tickInner();
    } catch (e) {
      log("动作异常：" + (e && e.message ? e.message : e), stageName());
    } finally {
      state.busy = false;
    }
  }

  async function tickInner() {
    if (state.stopped) return;
    const cmds = await send({ type: "commands" });
    if (cmds && cmds.stop) { state.stopped = true; log("收到停止指令", "stop"); return; }

    if (!state.cfg) {
      const c = await send({ type: "config", who: state.inst || "" });
      if (c && c.applicant) {
        state.cfg = c;
        log("已读取配置：" + (c.applicant.name_cn || "?") +
            "（窗口 " + (c.who || "w1") + " / 共 " + (c.total || 1) + " 人）", "init");
      }
    }

    const s = stageName();
    if (s !== state.lastStage) {
      log("进入阶段：" + s, s);
      state.lastStage = s;
      state.lastBeat = 0;
      state.unknownSince = 0;      // 阶段一旦变化，unknown 计时归零
      state.stageSince = Date.now();
      state.stageRetry = 0;
      // 阶段级状态快照：业务页带完整 DOM，滚动/空白页只记状态
      reportState("stage:" + s, {
        force: true,
        dom: ["entry", "auth", "country", "form", "confirm", "otp", "calendar",
              "mtcaptcha", "waf_captcha", "queue", "unknown"].includes(s)
      });
    }
    if (s === "queue") {
      // 排队页一到就把 cookie 推一份给本地服务：
      // ① 票池能立刻 --ingest（哪怕只有 WAF 层）；② 后面 --watch-queue 要用 ticket。
      if (!state.queueSnap) {
        state.queueSnap = true;
        send({ type: "snapshot", data: { stage: "queue", url: location.href,
                                         fields: {}, html: "" } });
        log("排队页 cookie 已推送本地服务（可用于 --ingest / 队列轮询）", "queue");
      }
      const t = text();

      // ① Queue-it 的排队号只有 20 分钟有效期（实测：18:47 排队，22:10 才放行，
      //    放行后必须在 20 分钟内进入系统，否则页面变成
      //    "Your queue number is no longer valid ... click the button below to get a new place"）。
      if (/no longer valid|has now expired|queue number[^.]*expired/i.test(t)) {
        if (once("queue:expired")) {
          log("⚠ 排队号已过期（20 分钟窗口已过），尝试重新排队", "queue");
          send({ type: "result", data: { stage: "queue_expired", url: location.href } });
          const again = findByText(["a", "button", "[role=button]"],
                                   ["GET A NEW PLACE", "新的排队", "重新排隊", "重新排队", "REJOIN"]);
          if (again) click(again, "重新排队");
        }
        return;
      }

      // ② 有的批次放行后不是自动跳转，而是要人点一下「进入/继续」。
      //    以前这里只打心跳，碰上这种就得干等到 20 分钟过期。
      if (!state.enterClicked) {
        const enter = findByText(["button", "a", "[role=button]", "input[type=button]"],
                                 ["ENTER", "PROCEED", "进入系统", "進入系統",
                                  "CONTINUE", "继续", "繼續"]);
        if (enter) {
          state.enterClicked = true;
          click(enter, "放行按钮");
          log("已点击放行按钮，等待进入系统", "queue");
          return;
        }
      }

      const now = Date.now();
      if (now - state.lastBeat > 20000) {
        state.lastBeat = now;
        const mins = Math.round((now - state.queueStart) / 60000);
        log(`排队中 ${mins} 分钟（号有效 20 分钟，放行后会立刻接管）`, "queue");
      }
      return;
    }

    // React 的跳转有时要再点一次才生效（实测 auth → 排队 这一段会停住）。
    // 只在提交前的阶段做"重复动作"，form/confirm/otp 一律不动，防止重复提交。
    try {
      if (["entry", "auth", "country"].includes(s)
          && Date.now() - (state.stageSince || Date.now()) > 8000
          && (state.stageRetry || 0) < 5) {
        state.stageRetry = (state.stageRetry || 0) + 1;
        state.stageSince = Date.now();
        state.done = new Set();          // 清空去重标记，让本阶段动作再走一遍
        log(`阶段 ${s} 停留过久，重试本阶段动作（第 ${state.stageRetry} 次）`, s);
      }
    } catch (e) { /* ignore */ }

    try {
      if (s === "entry") await doEntry();
      else if (s === "auth") await doAuth();
      else if (s === "country") await doCountry();
      else if (s === "form") await doForm();
      else if (s === "otp") { await tryOtp(); }
      else if (s === "calendar") await doCalendar();
      else if (s === "confirm") await doConfirm();
      else if (s === "waf_captcha") await doWafCaptcha();
      else if (s === "mtcaptcha") await doMTCaptcha();
      else if (s === "unknown") await doRecover();
    } catch (e) {
      log("动作异常：" + (e && e.message ? e.message : e), s);
    }
  }

  // ------------------------------------------------------------ iframe 模式
  // Queue-it 的排队/放行界面常常整个跑在 iframe 里，跨域 iframe 的 DOM 从主文档碰不到，
  // 所以 background 用 allFrames:true 也往里注入一份。iframe 里只做一件事：
  // 找到「进入/继续」类按钮并点掉（其余状态机留给主文档）。
  const IS_FRAME = (() => {
    try { return window.top !== window; } catch (e) { return true; }
  })();

  if (IS_FRAME) {
    let clicked = false;
    let capSeen = false;
    state.frameHello = false;
    let lastReport = 0;
    let lastSolve = 0;
    let busy = false;
    const FRAME_WANT = ["ENTER", "PROCEED", "CONTINUE", "ACCEPT", "OK",
                        "进入", "進入", "继续", "繼續", "接受", "确定", "確定"];
    const frameTick = () => {
      try {
        // 自检：本帧到底注入了没有、solver 在不在。这两条是排查「iframe 里脚本不动」的第一现场。
        if (!state.frameHello) {
          state.frameHello = true;
          send({ type: "status", data: {
            stage: "frame_init",
            msg: "iframe 注入：" + location.href.slice(0, 90)
                 + " | solver=" + (window.__CAPSOLVER ? "有" : "无"),
            url: location.href.slice(0, 140)
          } });
        }
        // 把本帧的偏移广播给子帧：跨域 iframe 里拿不到 frameElement，
        // 而 chrome.debugger 的 Input 要用**顶层视口坐标**才落得准。
        if (window.__CAPSOLVER) {
          try { window.__CAPSOLVER.broadcastToChildFrames(); } catch (e) { /* ignore */ }
        }

        // ① 验证码：以前这里只上报「请手动完成」，结果就是脚本看着不动、人得自己拖。
        //    现在本帧就地求解（captcha_solver.js 随帧注入，canvas 在本帧同源，读得到）。
        if (window.__CAPSOLVER && !busy) {
          let s = { type: "none" };
          try { s = window.__CAPSOLVER.scan(); } catch (e) { /* ignore */ }
          if (s.type !== "none" && s.type !== "mtcaptcha" && !capSeen) {
            capSeen = true;
            // 先报一声「看见了」——这条就是钉钉会响的那条，别再静默
            send({ type: "status", data: {
              stage: "waf_captcha_frame",
              msg: "⚠ iframe 内发现验证码（" + (s.detail[0] || s.type) + "），本帧开始自动求解",
              url: location.href.slice(0, 140), type: s.type
            } });
          }
          if (s.type !== "none" && s.type !== "mtcaptcha") {
            const now = Date.now();
            if (now - lastSolve > 7000) {
              lastSolve = now;
              busy = true;
              (async () => {
                let r = { ok: false, note: "" };
                try { r = await window.__CAPSOLVER.solve({ tries: 2 }); }
                catch (e) { r = { ok: false, note: String(e) }; }
                busy = false;
                if (r.ok) {
                  send({ type: "status", data: {
                    stage: "captcha_solved", msg: "✅ iframe 内验证码已自动通过",
                    url: location.href.slice(0, 140), how: r.how || ""
                  } });
                  lastReport = 0;
                } else if (Date.now() - lastReport > 45000) {
                  // 45 秒最多吵一次：上报失败细节（ext_server 会推到钉钉）
                  lastReport = Date.now();
                  send({ type: "status", data: {
                    stage: "waf_captcha_frame",
                    msg: "⚠ iframe 内验证码自动求解失败：" + (r.note || "?")
                         + (r.tries && r.tries.length ? " | " + r.tries.slice(-2).join(" ; ") : ""),
                    url: location.href.slice(0, 140), type: s.type
                  } });
                }
              })();
            }
          } else if (s.type === "mtcaptcha") {
            // MTCaptcha 自己的 iframe：点它的勾选框/验证按钮
            const now = Date.now();
            if (now - lastSolve > 5000) {
              lastSolve = now;
              window.__CAPSOLVER.solveMt().then((r) => {
                if (r && r.ok) {
                  send({ type: "status", data: {
                    stage: "captcha_solved", msg: "✅ MTCaptcha 勾选已点（" + (r.note || "") + "）",
                    url: location.href.slice(0, 140)
                  } });
                }
              }).catch(() => {});
            }
          }
        }

        if (clicked) return;
        // ② 放行按钮
        for (const el of document.querySelectorAll(
            "button,a,[role=button],input[type=button],input[type=submit]")) {
          const txt = (el.innerText || el.value || "").replace(/\s+/g, "").toUpperCase();
          if (!txt) continue;
          if (FRAME_WANT.some((w) => txt === w || txt.includes(w))) {
            el.click();
            clicked = true;
            send({ type: "status", data: { stage: "queue_frame", msg: "iframe 内点击放行：" + txt, url: location.href.slice(0, 120) } });
            return;
          }
        }
      } catch (e) { /* 跨域访问失败的静默 */ }
    };
    setInterval(frameTick, 1000);
    frameTick();
    return;
  }

  setInterval(tick, TICK);
  log("扩展已注入（无 CDP）", "init");
  // 自检：只在本地测试页改标题，方便不开 CDP 也能确认注入成功
  if (location.port === "8765") {
    document.title = "[EXT-OK] " + document.title;
  }
})();
