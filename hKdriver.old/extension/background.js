// background.js —— 扩展与本地服务（127.0.0.1:8765）之间的唯一通道。
// 为什么放在 background 而不是 content：
//   content script 的 fetch 受页面 CORS 限制；background 有 host_permissions，不受限。

const API = "http://127.0.0.1:8765";

// SW 顶层探针：文件执行到哪一步（MV3 顶层一旦抛错，后面的 listener 就不注册了）
postJSON("/status", { stage: "sw_top", msg: "background.js 顶层开始执行 shots=v3" }).catch(() => {});

function toB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

async function getJSON(path) {
  const r = await fetch(API + path, { cache: "no-store" });
  return await r.json();
}

async function postJSON(path, obj) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(obj)
  });
  return await r.json();
}

// ---------------------------------------------------------------- 每步截图
// 为什么放 background：content script 拍不了自己所在的页面，只有 tabs.captureVisibleTab 能拍。
// 什么时候拍：① content 每次 step()（点击/填表/上传/OTP/验证码…）② 阶段切换时。
// 节流：captureVisibleTab 有每秒 2 次的配额，这里 900ms 一次；
//       同一 label 最多留 3 张、整场最多 800 张，防止刷爆磁盘。
// 拍不到（标签不在前台 / 窗口最小化）就静默跳过，绝不影响主流程。
const LAST_STAGE = new Map();
const SHOT_AT = new Map();
const SHOT_N = new Map();
let SHOT_TOTAL = 0;

// 单独一次截图（给 /step 用）：失败返回 null，绝不阻塞主流程。
// activate=true 时若该标签不在前台，先切到前台再拍 —— 否则 captureVisibleTab
// 拍到的是别的标签页，或者直接报错（实测 23:33 那步就因此丢了图）。
function grabPngRaw(tabId, activate) {
  return new Promise((resolve) => {
    try {
      chrome.tabs.get(tabId, (tab) => {
        if (chrome.runtime.lastError || !tab) return resolve(null);
        const cap = () => chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }, (dataUrl) => {
          if (chrome.runtime.lastError || !dataUrl) return resolve(null);
          resolve(dataUrl);
        });
        if (tab.active === false) {
          if (!activate) return resolve(null);
          chrome.tabs.update(tabId, { active: true }, () => setTimeout(cap, 150));
        } else {
          cap();
        }
      });
    } catch (e) { resolve(null); }
  });
}

// 串行化：captureVisibleTab 有「每窗口每秒 2 次」的硬配额，超了直接报
// MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND（实测 23:35 那一步就丢了图）。
// 这里统一排队、间隔 600ms，失败再等 450ms 重试一次。
let SHOT_Q = Promise.resolve();
let LAST_CAP = 0;
function captureSerial(tabId, activate) {
  SHOT_Q = SHOT_Q.then(async () => {
    const wait = 600 - (Date.now() - LAST_CAP);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    LAST_CAP = Date.now();
    let du = await grabPngRaw(tabId, activate);
    if (!du) {
      await new Promise((r) => setTimeout(r, 450));
      LAST_CAP = Date.now();
      du = await grabPngRaw(tabId, activate);
    }
    return du;
  }).catch(() => null);
  return SHOT_Q;
}

function shoot(tabId, label, inst) {
  try {
    if (tabId < 0 || !label) return;
    if (SHOT_TOTAL < 6) {
      // 前几次把「进来了」也报一声：否则截图不落地时无法判断是没调用还是调用失败
      postJSON("/status", { stage: "shoot_try",
        msg: "尝试截图 tab=" + tabId + " label=" + label }).catch(() => {});
    }
    const now = Date.now();
    if (now - (SHOT_AT.get(tabId) || 0) < 900) return;
    if (SHOT_TOTAL >= 800) return;
    const k = tabId + "|" + label;
    if ((SHOT_N.get(k) || 0) >= 3) return;

    captureSerial(tabId, true).then((dataUrl) => {
      if (!dataUrl) {
        postJSON("/status", { stage: "shot_err",
          msg: "截图失败（未拿到 png）：" + label }).catch(() => {});
        return;
      }
      SHOT_AT.set(tabId, now);
      SHOT_N.set(k, (SHOT_N.get(k) || 0) + 1);
      SHOT_TOTAL++;
      if (SHOT_TOTAL < 4) {
        postJSON("/status", { stage: "shoot_ok",
          msg: "截图成功 " + label + " bytes=" + dataUrl.length }).catch(() => {});
      }
      chrome.tabs.get(tabId, (tab) => {
        postJSON("/shot", {
          label: label, inst: inst || "", stage: label,
          url: ((tab && tab.url) || "").slice(0, 200), png: dataUrl
        }).catch(() => {});
      });
    });
  } catch (e) { /* 截图失败不能影响抢号 */ }
}

// ---------------------------------------------------------------- 网络记录
// 只观察、不拦截：把浏览器发出的每个请求（含表单请求体）记下来，
// 供事后逆向「完整流程到底调了哪些接口、带了什么参数」。
// 注意：webRequest 拿不到**响应体**，只能拿到状态码；响应内容靠 /session 的整页 HTML 补。
const NETQ = [];
const NET_URLS = [
  "*://abs1.td.gov.hk/*", "*://abs2.td.gov.hk/*",
  "*://abs3.td.gov.hk/*", "*://abs4.td.gov.hk/*",
  "*://www.gov.hk/*"
];

async function flushNet() {
  if (!NETQ.length) return;
  const batch = NETQ.splice(0, NETQ.length);
  try { await postJSON("/netlog", { events: batch }); } catch (e) { /* 离线也不影响主流程 */ }
}

function pushNet(rec) {
  NETQ.push(rec);
  if (NETQ.length >= 12) flushNet();
}

try {
  chrome.webRequest.onBeforeRequest.addListener(
    (d) => {
      const rec = {
        t: Date.now(), ph: "req", m: d.method, type: d.type,
        url: (d.url || "").slice(0, 300)
      };
      try {
        if (d.tabId >= 0) rec.tab = TAB_INST.get(d.tabId) || "";
      } catch (e) { /* ignore */ }
      if (d.requestBody) {
        if (d.requestBody.formData) {
          const f = {};
          for (const [k, v] of Object.entries(d.requestBody.formData)) {
            f[k] = String(Array.isArray(v) ? v[0] : v).slice(0, 300);
          }
          rec.form = f;
        } else if (d.requestBody.raw && d.requestBody.raw.length) {
          let n = 0;
          for (const r of d.requestBody.raw) n += r.bytes ? r.bytes.byteLength : 0;
          rec.rawLen = n;
        }
      }
      pushNet(rec);
    },
    { urls: NET_URLS },
    ["requestBody"]
  );

  chrome.webRequest.onCompleted.addListener(
    (d) => pushNet({
      t: Date.now(), ph: "done", s: d.statusCode, type: d.type,
      url: (d.url || "").slice(0, 300),
      tab: (() => { try { return TAB_INST.get(d.tabId) || ""; } catch (e) { return ""; } })()
    }),
    { urls: NET_URLS }
  );

  // 响应头：body 拿不到（webRequest 不给），但 content-type / length / location
  // 足以判断「服务端回了什么」—— 尤其是表单 POST 的 302 跳转目标。
  chrome.webRequest.onHeadersReceived.addListener(
    (d) => {
      const h = {};
      try {
        for (const x of d.responseHeaders || []) {
          const k = String(x.name || "").toLowerCase();
          if (["content-type", "content-length", "location", "content-encoding",
               "cache-control", "x-frame-options", "set-cookie"].includes(k)) {
            h[k] = (k === "set-cookie") ? "***" : String(x.value || "").slice(0, 300);
          }
        }
      } catch (e) { /* ignore */ }
      return pushNet({
        t: Date.now(), ph: "hdr", s: d.statusCode, type: d.type,
        url: (d.url || "").slice(0, 300), hdr: h
      });
    },
    { urls: NET_URLS },
    ["responseHeaders"]
  );

  chrome.webRequest.onErrorOccurred.addListener(
    (d) => pushNet({
      t: Date.now(), ph: "err", e: d.error, type: d.type,
      url: (d.url || "").slice(0, 300)
    }),
    { urls: NET_URLS }
  );
} catch (e) {
  console.warn("[TDABS] webRequest unavailable:", String(e && e.message ? e.message : e));
}
postJSON("/status", { stage: "sw_net", msg: "webRequest 三个监听已注册" }).catch(() => {});

// ---------------------------------------------------------------- 可信输入
// 为什么需要：扩展合成的事件 isTrusted=false，行为验证码可以拿这个字段判机器人。
// chrome.debugger 的 Input 域发的是浏览器级输入，isTrusted=true，且**不需要开
// --remote-debugging-port**（页面侧看不到 CDP 端口，只有标签页顶部一条提示条）。
// 代价：attach 期间标签页显示「正在调试此浏览器」，介意就设
//       localStorage.tdabs_trusted_input = "0" 关掉，退回纯合成事件。
let DBG_TAB = -1;

function dbgCmd(tabId, method, params) {
  return new Promise((res, rej) => {
    chrome.debugger.sendCommand({ tabId }, method, params, (r) => {
      if (chrome.runtime.lastError) rej(new Error(chrome.runtime.lastError.message));
      else res(r || {});
    });
  });
}

function dbgAttach(tabId) {
  return new Promise((res, rej) => {
    chrome.debugger.attach({ tabId }, "1.3", () => {
      if (chrome.runtime.lastError) {
        const m = chrome.runtime.lastError.message || "";
        if (/already attached/i.test(m)) return res();
        return rej(new Error(m));
      }
      res();
    });
  });
}

try {
  chrome.debugger.onDetach.addListener((src) => {
    if (src && src.tabId === DBG_TAB) DBG_TAB = -1;
  });
} catch (e) { /* ignore */ }

async function trustedDrag(tabId, d) {
  const from = d.from || [0, 0], to = d.to || [0, 0];
  const pts = Array.isArray(d.points) && d.points.length ? d.points : [[to[0], to[1]]];
  await dbgAttach(tabId);
  DBG_TAB = tabId;
  const M = (type, x, y, extra) => dbgCmd(tabId, "Input.dispatchMouseEvent",
    Object.assign({ type, x, y, button: "none", buttons: 0, clickCount: 0 }, extra || {}));

  // ① 移过去（真人会先把指针移上来，不会凭空出现在滑块上）
  await M("mouseMoved", from[0] - 120, from[1] + 20);
  await new Promise((r) => setTimeout(r, 120));
  await M("mouseMoved", from[0], from[1]);
  await new Promise((r) => setTimeout(r, 180));

  // ② 按下
  await M("mousePressed", from[0], from[1], { button: "left", buttons: 1, clickCount: 1 });
  await new Promise((r) => setTimeout(r, 120));

  // ③ 沿轨迹移动，逐点带 jitter（坐标由 content 侧算好，这里只负责发）
  for (const p of pts) {
    await M("mouseMoved", p[0], p[1], { button: "left", buttons: 1 });
    await new Promise((r) => setTimeout(r, 8 + Math.random() * 16));
  }

  // ④ 到位停顿再松手
  await new Promise((r) => setTimeout(r, d.holdMs || 200));
  await M("mouseReleased", to[0], to[1], { button: "left", buttons: 0, clickCount: 1 });
  return { ok: true, points: pts.length };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg && msg.type === "flushnet") {
        await flushNet();
        sendResponse({ ok: true });
        return;
      }
      // 给所有上报补上窗口代号，本地服务端就能分辨是哪个窗口
      // 优先用注入时记下的 tabId→inst；跳转后 hash 会丢，所以再用 tab.url 兜一次
      let inst = "";
      if (sender && sender.tab) {
        inst = TAB_INST.get(sender.tab.id) || instOf(sender.tab.url || "") || "";
      }
      if (msg && msg.data && inst && !msg.data.inst) msg.data.inst = inst;

      switch (msg.type) {
        case "status": {
          const d = msg.data || {};
          await postJSON("/status", d);
          const tabId = sender && sender.tab ? sender.tab.id : -1;
          const changed = LAST_STAGE.get(tabId) !== d.stage;
          LAST_STAGE.set(tabId, d.stage);
          // 带 step 的 status 不再单独截图：同一次动作还会发一条 {type:"step"}，
          // 那条里带截图 + DOM；重复拍只会浪费 captureVisibleTab 每秒 2 次的配额。
          const label = (!d.step && changed) ? "stage-" + (d.stage || "?") : "";
          if (label) shoot(tabId, label, d.inst || inst);
          sendResponse({ ok: true });
          break;
        }

        case "step": {
          // 每一步的完整证据包：状态 + DOM + cookie + 截图 → runs/runlog/<日期>/
          const d = msg.data || {};
          const tabId = sender && sender.tab ? sender.tab.id : -1;
          let cookies = [];
          try {
            const url = d.url || (sender.tab && sender.tab.url) || "";
            if (url) cookies = await chrome.cookies.getAll({ url });
            for (const host of ["abs1.td.gov.hk", "abs2.td.gov.hk", "abs3.td.gov.hk", "abs4.td.gov.hk"]) {
              const cs = await chrome.cookies.getAll({ domain: host });
              for (const c of cs) {
                if (!cookies.find((x) => x.name === c.name && x.domain === c.domain)) cookies.push(c);
              }
            }
          } catch (e) { /* ignore */ }
          d.cookies = (cookies || []).map((c) => ({
            domain: c.domain, name: c.name, value: c.value, path: c.path,
            httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite,
            expirationDate: c.expirationDate
          }));
          d.tabId = tabId;
          if (tabId >= 0) d.png = await captureSerial(tabId, true);
          sendResponse(await postJSON("/step", d));
          break;
        }

        case "config": {
          // 关键：跳到 abs3 之后 URL 上的 #inst=w2 会丢，content 的 state.inst 变空，
          // 直接用空 who 请求会返回**第一个人**的资料 → w2 拿 w1 身份提交 = 两个人的预约全废。
          // 所以这里用 tabId→inst 映射兜底。
          const who = (msg && msg.who) ? msg.who : inst;
          sendResponse(await getJSON("/config?who=" + encodeURIComponent(who || "")));
          break;
        }

        case "probe": {
          // 轻量探测入口是否已恢复。
          // 注意：abs3 是 React SPA，初始 HTML 里**没有** conditionsCheck（那是 JS 渲染后才有的），
          // 所以判定不能用它，只能看「200 + 不是 WAF 挑战页 + 长度像正常骨架」。
          try {
            // 必须带超时：abs3 在高峰会「连上但不回包」，fetch 默认能挂几十秒，
            // 而 content 的 doRecover 是 await 这条消息的 —— 一挂就把整个状态机锁死
            // （2026-11-12 16:46 实测：卡住后 4 分钟一条日志都没有）。
            const ctl = new AbortController();
            const timer = setTimeout(() => ctl.abort(), 6000);
            let r, t;
            try {
              r = await fetch(msg.url, { cache: "no-store", credentials: "include", signal: ctl.signal });
              t = await r.text();
            } finally {
              clearTimeout(timer);
            }
            const challenge = /\$_ts|nsd=|HWWAF/.test(t);
            sendResponse({
              ok: r.ok, status: r.status, len: t.length, challenge: challenge,
              ready: r.ok && !challenge && t.length > 300
            });
          } catch (e) {
            sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
          }
          break;
        }

        case "otp":
          sendResponse(await getJSON("/otp?who=" + encodeURIComponent(msg.who || "")));
          break;

        case "commands":
          sendResponse(await getJSON("/commands"));
          break;

        case "file": {
          const r = await fetch(`${API}/file?name=${encodeURIComponent(msg.name)}`);
          if (!r.ok) { sendResponse({ ok: false, status: r.status }); break; }
          const buf = await r.arrayBuffer();
          sendResponse({
            ok: true,
            b64: toB64(buf),
            ctype: r.headers.get("content-type") || "image/jpeg",
            size: buf.byteLength
          });
          break;
        }

        case "snapshot": {
          // 会话快照：cookie（含 HttpOnly，必须走 chrome.cookies）+ 页面 HTML + 表单字段。
          // 用途：离线逆向出「浏览器过门之后，replay.py 需要带什么才能继续」。
          const tabId = sender && sender.tab ? sender.tab.id : -1;
          let cookies = [];
          try {
            const url = (msg.data && msg.data.url) || (sender.tab && sender.tab.url) || "";
            cookies = url ? await chrome.cookies.getAll({ url }) : [];
            // 同时把相关域名的 cookie 全拿到（abs1/abs2/abs3/abs4 各有一套）
            for (const host of ["abs1.td.gov.hk", "abs2.td.gov.hk", "abs3.td.gov.hk", "abs4.td.gov.hk"]) {
              const cs = await chrome.cookies.getAll({ domain: host });
              for (const c of cs) {
                if (!cookies.find((x) => x.name === c.name && x.domain === c.domain)) cookies.push(c);
              }
            }
          } catch (e) { /* ignore */ }
          const payload = Object.assign({}, msg.data, {
            tabId, cookies: cookies.map((c) => ({
              domain: c.domain, name: c.name, value: c.value,
              path: c.path, httpOnly: c.httpOnly, secure: c.secure,
              sameSite: c.sameSite, expirationDate: c.expirationDate
            })),
            savedAt: Date.now()
          });
          sendResponse(await postJSON("/session", payload));
          break;
        }

        case "captcha": {
          // canvas 截图较大，单独走一个接口；返回 Python 算好的 dx
          const r = await fetch(API + "/captcha", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(msg.data || {})
          });
          sendResponse(await r.json());
          break;
        }

        case "trustedDrag": {
          const tabId = sender && sender.tab ? sender.tab.id : -1;
          if (tabId < 0) { sendResponse({ ok: false, error: "no tab" }); break; }
          try {
            sendResponse(await trustedDrag(tabId, msg.data || {}));
          } catch (e) {
            sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
          }
          break;
        }

        case "captcha_spec": {
          // 验证码样本：位图 + 几何 + DOM，落 runs/captcha_specimens/
          sendResponse(await postJSON("/captcha_spec", msg.data || {}));
          break;
        }

        case "result":
          await postJSON("/result", msg.data || {});
          sendResponse({ ok: true });
          break;

        case "net": {
          // 接口记录：顺手把当时的 td.gov.hk cookie（含 HttpOnly）一起带上，
          // 这样 replay.py 不用碰 Chrome 的加密 cookie 库就能重放会话。
          const data = Object.assign({}, msg.data || {});
          try {
            const list = await chrome.cookies.getAll({ domain: "td.gov.hk" });
            data.cookies = (list || []).map((c) => ({
              name: c.name, value: c.value, domain: c.domain, path: c.path,
              secure: c.secure, httpOnly: c.httpOnly,
              sameSite: c.sameSite, expirationDate: c.expirationDate
            }));
          } catch (e) {
            data.cookies = [];
            data.cookieError = String(e && e.message ? e.message : e);
          }
          await postJSON("/net", data);
          sendResponse({ ok: true });
          break;
        }

        default:
          sendResponse({ error: "unknown type: " + msg.type });
      }
    } catch (e) {
      sendResponse({ error: String(e && e.message ? e.message : e) });
    }
  })();
  return true; // 异步 response
});
postJSON("/status", { stage: "sw_msg", msg: "onMessage 监听已注册" }).catch(() => {});

// 子框架补注入：tabs.onUpdated 只在主文档完成时触发，**iframe 是之后才创建/加载的**，
// 那一份脚本就永远进不去（2026-11-10 实战：验证码整个在 iframe 里，脚本看不见）。
// 所以监听 webNavigation 的每个 frame，谁加载就给谁补一份。
// 主帧刚提交（还没 load）就给业务 JSP 页装接口记录器。
// netrec 只在 AdminServlet 页装钩子，装到别的页面会立刻 return，所以这里按域名预筛。
async function injectNetEarly(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, frameIds: [0] }, files: ["netrec.js"],
      world: "MAIN", injectImmediately: true
    }).catch(() => {});
    await chrome.scripting.executeScript({
      target: { tabId, frameIds: [0] }, files: ["netrelay.js"],
      injectImmediately: true
    }).catch(() => {});
  } catch (e) { /* 导航中注入失败正常，complete 时还会再注一次 */ }
}

async function injectFrame(tabId, frameId) {  const errs = [];
  for (const f of ["captcha_solver.js", "content.js"]) {
    try {
      await chrome.scripting.executeScript({
        target: { tabId, frameIds: [frameId] },
        files: [f]
      });
    } catch (e) {
      errs.push(f + ":" + String(e && e.message ? e.message : e).slice(0, 90));
    }
  }
  try {
    await postJSON("/status", {
      stage: "bg_frame",
      msg: "帧注入 tab=" + tabId + " frame=" + frameId + (errs.length ? " 失败 " + errs.join(" | ") : " 成功"),
      url: "frame#" + frameId
    });
  } catch (e) { /* ignore */ }
}

try {
  postJSON("/status", { stage: "bg_init", msg: "SW 启动 webNavigation=" + (!!chrome.webNavigation) + " debugger=" + (!!chrome.debugger) + " shots=v3" }).catch(() => {});
  const onFrame = (d) => {
    if (!d) return;
    if (d.frameId === 0) {
      // 主帧一提交就给业务 JSP 页装接口记录器：页面的 XHR（如框架初始化调用）
      // 常常在 load 之前就发出，等 tabs.onUpdated=complete 再注入就晚了。
      // netrec 自己只在 AdminServlet 页装钩子，注入到 abs3/abs4 时会立刻 return。
      let h0 = "";
      try { h0 = new URL(d.url).hostname; } catch (e) { return; }
      if (/^abs[12]\.td\.gov\.hk$/.test(h0)) injectNetEarly(d.tabId);
      return;
    }
    let host = "";
    try { host = new URL(d.url).hostname; } catch (e) { return; }
    if (!TARGET_RE.test(host)) return;
    injectFrame(d.tabId, d.frameId);
  };
  chrome.webNavigation.onCommitted.addListener(onFrame);
  chrome.webNavigation.onDOMContentLoaded.addListener(onFrame);
} catch (e) {
  console.warn("[TDABS] webNavigation unavailable:", String(e && e.message ? e.message : e));
}

chrome.runtime.onInstalled.addListener(() => {
  console.log("[TDABS] extension installed");
});

// ---------------------------------------------------------------- 主动注入
// 为什么不用 manifest 的 content_scripts：
//   实测 Chrome 156 + --load-extension 时 content_scripts 没有生效（profile 里能看到
//   service worker，但页面拿不到脚本）。改成由 background 在 tab 加载完成时主动注入，
//   绕开匹配规则，且便于控制「只在需要的域名注入」。
//
// allFrames: true 的原因：Queue-it 的排队/放行界面可能整个跑在 iframe 里，
// 而跨域 iframe 的 DOM 从主文档根本碰不到，必须往里也注入一份。
const TARGET_RE = /(^|\.)(td\.gov\.hk|gov\.hk|127\.0\.0\.1|queue-it\.net|queue-it\.com|mtcaptcha\.com|mtcaptcha\.net)$/;

// 每个窗口的代号（w1 / w2 …），来自启动 URL 的 #inst=w2。
// 放进 tabId 映射里，因为跳转后 URL 上的 hash 会丢，而 tabId 不变。
const TAB_INST = new Map();
function instOf(url) {
  try {
    const u = new URL(url);
    const m = /(?:^|[#&])inst=([A-Za-z0-9_-]+)/.exec(u.hash || "") ||
              /(?:^|[#&])inst=([A-Za-z0-9_-]+)/.exec(u.search || "");
    return m ? m[1] : "";
  } catch (e) { return ""; }
}

async function inject(tabId, url) {
  const inst = instOf(url);
  if (inst) {
    TAB_INST.set(tabId, inst);
    // service worker 会被回收，存一份到 session storage（只在本浏览器会话内）
    try {
      const s = await chrome.storage.session.get("tabInst");
      const m = s.tabInst || {};
      m[tabId] = inst;
      await chrome.storage.session.set({ tabInst: m });
    } catch (e) { /* storage.session 不可用时忽略 */ }
  }
  try {
    // 求解器先注入：content.js 里的状态机要在任意帧里都能调到 window.__CAPSOLVER
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["captcha_solver.js"]
    }).catch((e) => console.warn("[TDABS] solver inject failed:", String(e && e.message ? e.message : e)));
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["content.js"]
    });
    // 接口记录器：netrec 必须跑在页面主世界才有用（要拿到页面自己的 form/XHR/fetch）
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["netrec.js"],
      world: "MAIN"
    }).catch((e) => console.warn("[TDABS] netrec inject failed:", String(e && e.message ? e.message : e)));
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["netrelay.js"]
    }).catch((e) => console.warn("[TDABS] netrelay inject failed:", String(e && e.message ? e.message : e)));
    console.log("[TDABS] injected into", url, inst ? "(inst=" + inst + ")" : "");
    postJSON("/status", { stage: "bg_inject", msg: "主文档注入 ok " + String(url).slice(0, 80) }).catch(() => {});
  } catch (e) {
    console.warn("[TDABS] inject failed:", String(e && e.message ? e.message : e));
    postJSON("/status", { stage: "bg_inject", msg: "主文档注入失败 " + String(e && e.message ? e.message : e).slice(0, 90) }).catch(() => {});
  }
}

// service worker 冷启动时把 tabId→inst 恢复回来
(async () => {
  try {
    const s = await chrome.storage.session.get("tabInst");
    for (const [k, v] of Object.entries(s.tabInst || {})) TAB_INST.set(Number(k), v);
  } catch (e) { /* ignore */ }
})();

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status !== "complete") return;
  const url = tab && tab.url ? tab.url : "";
  if (!url) return;
  let host = "";
  try { host = new URL(url).hostname; } catch (e) { return; }
  if (!TARGET_RE.test(host)) return;
  inject(tabId, url);
});

// service worker 被唤醒时，把已开着的目标页也补注入一次
chrome.tabs.query({}, (tabs) => {
  for (const t of tabs || []) {
    if (!t.url) continue;
    let host = "";
    try { host = new URL(t.url).hostname; } catch (e) { continue; }
    if (TARGET_RE.test(host)) inject(t.id, t.url);
  }
});
