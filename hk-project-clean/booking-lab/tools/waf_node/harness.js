// harness.js —— A 路线主流程骨架（P0→P3 的落点）
// =====================================================================
// 依赖：npm i jsdom @napi-rs/canvas
// 用法：
//   node harness.js --url https://abs3.example.test/?lang=sc [--proxy http://user:pass@host:port]
//
// 阶段开关（按 P0→P3 逐步打开）：
//   --stage p0   只求「环境能跑 + 拿到 cookie」
//   --stage p1   多了「hook 绘制，输出矢量题面」
//   --stage p2   多了「hook 提交，打印格式」
//   --stage p3   完整：自己算 dx 并提交
//
// ⚠ 本文件是骨架：jsdom/canvas 未安装时会明确报错并退出，不会静默失败。

const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const arg = (k, d = null) => {
  const i = args.indexOf(`--${k}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};
const STAGE = (arg("stage", "p0") || "p0").toLowerCase();
const TARGET = arg("url", "https://abs3.example.test/?lang=sc");
const PROXY = arg("proxy", "");

const OUT = path.resolve(__dirname, "..", "runs", "node_harness");
fs.mkdirSync(OUT, { recursive: true });

// ---------------------------------------------------------------- 依赖检查
let JSDOM, createCanvas;
try {
  ({ JSDOM } = require("jsdom"));
} catch (e) {
  console.error("[FATAL] 缺 jsdom —— 先执行：cd waf_node && npm i jsdom @napi-rs/canvas");
  process.exit(2);
}
try {
  ({ createCanvas } = require("@napi-rs/canvas"));
} catch (e) {
  console.error("[FATAL] 缺 @napi-rs/canvas —— 同上安装");
  process.exit(2);
}

const { buildShims } = require("./shims");

// ---------------------------------------------------------------- L1 会话
// 说明：这里用 Node 原生 fetch（Node 24 自带 undici）。
// 若需要更接近 Chrome 的 TLS 指纹，改用 Python 侧 curl_cffi 拿 HTML 再喂进来：
//   把 --html 参数指向 curl_cffi 抓下来的挑战页文件即可。
const jar = new Map();
function cookieHeader() {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
}
function absorbSetCookie(res) {
  const list = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  for (const line of list) {
    const [nv] = line.split(";");
    const [k, ...rest] = nv.split("=");
    jar.set(k.trim(), rest.join("=").trim());
  }
  return list;
}

async function fetchChallenge() {
  const htmlPath = arg("html");
  if (htmlPath) {
    console.error(`[L2] 用本地挑战页：${htmlPath}`);
    return fs.readFileSync(htmlPath, "utf8");
  }
  console.error(`[L2] GET ${TARGET}`);
  const res = await fetch(TARGET, {
    headers: buildHeaders(),
    redirect: "manual",
  });
  const sc = absorbSetCookie(res);
  console.error(`[L2] HTTP ${res.status}  set-cookie: ${sc.length} 条`);
  let body = await res.text();
  fs.writeFileSync(path.join(OUT, "challenge.html"), body);
  console.error(`[L2] 挑战页已存 ${body.length} B → runs/node_harness/challenge.html`);
  return body;
}

function buildHeaders() {
  const h = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9",
    "sec-ch-ua": '"Chromium";v="144", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "upgrade-insecure-requests": "1",
  };
  if (jar.size) h.cookie = cookieHeader();
  return h;
}

// ---------------------------------------------------------------- L3/L4 执行 + Hook
const shapes = [];      // P1：矢量题面
const submits = [];     // P2：提交记录
const cookies = [];     // P0：cookie 写入记录

function makeDom(html) {
  const dom = new JSDOM(html, {
    url: TARGET,
    referrer: "https://portal.example.test/",
    contentType: "text/html",
    runScripts: "outside-only",     // 不让 jsdom 自己跑，我们按顺序 eval（挑战页有先后依赖）
    resources: undefined,
    pretendToBeVisual: true,
  });
  const w = dom.window;

  // ---- canvas 替换成 @napi-rs/canvas（jsdom 默认没有 2D 上下文）
  const origCreate = w.document.createElement.bind(w.document);
  w.document.createElement = function (tag, ...rest) {
    const el = origCreate(tag, ...rest);
    if (String(tag).toLowerCase() === "canvas") {
      const cv = createCanvas(420, 428);
      // 把核心属性/方法挂到 jsdom 的 element 上
      Object.defineProperty(el, "width", { get: () => cv.width, set: (v) => { cv.width = v; } });
      Object.defineProperty(el, "height", { get: () => cv.height, set: (v) => { cv.height = v; } });
      el.getContext = (t, o) => cv.getContext(t, o);
      el.toDataURL = (...a) => cv.toDataURL(...a);
    }
    return el;
  };

  buildShims(w);                    // navigator/screen/performance/定时器/事件 等
  hookCanvas(w);                    // P1
  hookNetwork(w);                   // P2
  hookCookie(w);                    // P0
  return { dom, w };
}

function hookCanvas(w) {
  if (!w.CanvasRenderingContext2D) {
    console.error("[L4] 没有 CanvasRenderingContext2D —— canvas 挂钩不可用");
    return;
  }
  const P = w.CanvasRenderingContext2D.prototype;
  let cur = null;
  const wrap = (name, kind) => {
    const orig = P[name];
    if (typeof orig !== "function") return;
    P[name] = function (...a) {
      if (kind === "begin") { cur = { op: name, pts: [] }; shapes.push(cur); }
      else if (kind === "pt" && cur) cur.pts.push(a.slice(0, 2));
      else if (kind === "paint" && cur) cur.paint = name;
      else if (kind === "style" && cur) cur[name] = a[0];
      return orig.apply(this, a);
    };
  };
  wrap("beginPath", "begin");
  for (const m of ["arc", "rect", "moveTo", "lineTo", "ellipse"]) wrap(m, "pt");
  wrap("closePath", "pt");
  wrap("fill", "paint");
  wrap("stroke", "paint");
  for (const m of ["fillStyle", "strokeStyle", "lineWidth", "font"]) wrap(m, "style");
}

function hookNetwork(w) {
  const realFetch = w.fetch || ((...a) => globalThis.fetch(...a));
  w.fetch = async (input, init = {}) => {
    const rec = {
      kind: "fetch",
      url: typeof input === "string" ? input : input && input.url,
      method: (init.method || "GET").toUpperCase(),
      body: init.body ? String(init.body).slice(0, 4000) : null,
      headers: init.headers || {},
      at: Date.now(),
    };
    submits.push(rec);
    console.error(`[L4] fetch ${rec.method} ${rec.url}`);
    return realFetch(input, init);
  };

  const XHR = w.XMLHttpRequest;
  if (XHR) {
    const origOpen = XHR.prototype.open;
    const origSend = XHR.prototype.send;
    XHR.prototype.open = function (m, u, ...rest) { this.__rec = { kind: "xhr", method: m, url: u }; return origOpen.call(this, m, u, ...rest); };
    XHR.prototype.send = function (body) {
      if (this.__rec) { this.__rec.body = body ? String(body).slice(0, 4000) : null; this.__rec.at = Date.now(); submits.push(this.__rec); console.error(`[L4] xhr ${this.__rec.method} ${this.__rec.url}`); }
      return origSend.call(this, body);
    };
  }
}

function hookCookie(w) {
  let _c = "";
  Object.defineProperty(w.document, "cookie", {
    get: () => _c,
    set: (v) => {
      const line = String(v);
      const [nv] = line.split(";");
      const [k, ...rest] = nv.split("=");
      jar.set(k.trim(), rest.join("=").trim());
      cookies.push({ name: k.trim(), value: rest.join("=").trim(), at: Date.now() });
      console.error(`[L4] cookie+ ${k.trim()} (${rest.join("=").trim().length}B)`);
      _c = cookieHeader();
    },
  });
}

// ---------------------------------------------------------------- L5 求解 + 提交
function solveFromShapes() {
  // 矢量版：实心（fill）与空心（stroke）取中心，dx = hollow.x - solid.x
  const center = (s) => {
    const xs = s.pts.map((p) => p[0]).filter((v) => typeof v === "number");
    const ys = s.pts.map((p) => p[1]).filter((v) => typeof v === "number");
    if (!xs.length || !ys.length) return null;
    return { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2,
             w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  };
  const solids = shapes.filter((s) => s.paint === "fill").map((s) => ({ ...s, c: center(s) })).filter((s) => s.c && s.c.w > 8);
  const hollows = shapes.filter((s) => s.paint === "stroke").map((s) => ({ ...s, c: center(s) })).filter((s) => s.c && s.c.w > 8);
  if (!solids.length || !hollows.length) return { ok: false, err: "没 hook 到实心/空心", solids: solids.length, hollows: hollows.length };
  // 取与实心 y 最接近的空心
  const s = solids[0];
  hollows.sort((a, b) => Math.abs(a.c.y - s.c.y) - Math.abs(b.c.y - s.c.y));
  const t = hollows[0];
  return { ok: true, dx: +(t.c.x - s.c.x).toFixed(1), solid: [s.c.x, s.c.y], hollow: [t.c.x, t.c.y], shape_wh: [s.c.w, s.c.h] };
}

// ---------------------------------------------------------------- 主流程
(async () => {
  const html = await fetchChallenge();

  // 抽出内联脚本
  const inlines = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((m) => m[1]).filter((s) => s.trim());
  const externals = [...html.matchAll(/<script[^>]+src=["']([^"']+)["']/gi)].map((m) => m[1]);
  console.error(`[L2] 内联 ${inlines.length} 段 / 外链 ${externals.length} 个`);

  const { dom, w } = makeDom(html);

  // 依次执行：内联引导 → 外链 widget
  let err = null;
  for (let i = 0; i < inlines.length && !err; i++) {
    try { w.eval(inlines[i]); } catch (e) { err = `inline#${i}: ${e.message}`; }
  }
  for (const src of externals) {
    if (err) break;
    const u = src.startsWith("http") ? src : new URL(src, TARGET).href;
    const code = await (await fetch(u, { headers: buildHeaders() })).text();
    fs.writeFileSync(path.join(OUT, "widget.js"), code);
    try { w.eval(code); } catch (e) { err = `widget(${src}): ${e.message}`; }
  }

  const report = {
    stage: STAGE,
    executed: !err,
    err,
    cookies: cookies.map((c) => c.name),
    shapes_count: shapes.length,
    submits_count: submits.length,
  };

  if (STAGE !== "p0") report.solve = solveFromShapes();
  if (STAGE === "p2" || STAGE === "p3") report.submits = submits.slice(0, 5);

  console.log(JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));

  // p3 的提交在这里接：把 solve.dx 按 submits[0] 的格式发出去（待 P2 定型后补）
  if (STAGE === "p3" && report.solve && report.solve.ok && submits.length) {
    console.error("[L5] p3 提交逻辑待 P2 定型后接：见 harness.js 顶部注释");
  }

  try { w.close(); } catch (e) {}
  process.exit(err ? 1 : 0);
})().catch((e) => {
  console.error("[FATAL]", e && e.stack || e);
  process.exit(3);
});
