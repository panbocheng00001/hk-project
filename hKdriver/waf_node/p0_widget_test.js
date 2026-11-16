// p0_widget_test.js —— 只测「widget JS 在 jsdom + canvas 里能不能跑起来」
// =====================================================================
// 与 probe.js 的区别：probe 用纯手写 dummy（会在函数自有属性上露馅），
// 这个用真实的 jsdom + @napi-rs/canvas + shims，更接近线上环境。
//
// 用法：
//   node p0_widget_test.js                       # 用默认 widget 路径
//   node p0_widget_test.js --widget <path> --html <shell.html>
//
// 输出：跑到哪一步、缺什么、有没有产出 cookie

const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const arg = (k, d = null) => {
  const i = args.indexOf(`--${k}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};

const WIDGET = arg("widget", path.resolve(__dirname, "..", "runs", "captcha_specimens",
  "waf20261116", "waf_widget_8qcykgusbcwc.5057567.js"));
const SHELL = arg("html", "");

let JSDOM, createCanvas;
try {
  ({ JSDOM } = require("jsdom"));
  ({ createCanvas } = require("@napi-rs/canvas"));
} catch (e) {
  console.error("[FATAL] 缺依赖：cd waf_node && npm i jsdom @napi-rs/canvas");
  process.exit(2);
}
const { buildShims } = require("./shims");

const SHELL_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>challenge</title></head>
<body><div id="root"></div></body></html>`;

const html = SHELL ? fs.readFileSync(SHELL, "utf8") : SHELL_HTML;

const dom = new JSDOM(html, {
  url: "https://abs3.td.gov.hk/?lang=sc",
  referrer: "https://www.gov.hk/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const w = dom.window;

// canvas 换成 @napi-rs/canvas
const origCreate = w.document.createElement.bind(w.document);
w.document.createElement = function (tag, ...rest) {
  const el = origCreate(tag, ...rest);
  if (String(tag).toLowerCase() === "canvas") {
    const cv = createCanvas(420, 428);
    Object.defineProperty(el, "width", { configurable: true, get: () => cv.width, set: (v) => { cv.width = v; } });
    Object.defineProperty(el, "height", { configurable: true, get: () => cv.height, set: (v) => { cv.height = v; } });
    el.getContext = (t, o) => cv.getContext(t, o);
    el.toDataURL = (...a) => cv.toDataURL(...a);
    el.__napi = cv;
  }
  return el;
};

buildShims(w);

// widget 第一行就是 `if ($_ts.cd) {...}` —— 这个全局由挑战页的内联引导脚本设置。
// 没有挑战页 HTML 时先用占位值，让脚本能进到下一层报错（这也是 P0 的进度指示）。
w.$_ts = { cd: "qE2Srcql", nsd: 78852, cc: 1, ck: 1 };
console.error("[init] $_ts 占位已注入（真实值来自挑战页内联段）");

// 记录 cookie 写入
const cookies = [];
let _ck = "";
Object.defineProperty(w.document, "cookie", {
  configurable: true,
  get: () => _ck,
  set: (v) => {
    const [nv] = String(v).split(";");
    const [k, ...r] = nv.split("=");
    cookies.push({ name: k.trim(), len: r.join("=").trim().length });
    _ck = _ck ? `${_ck}; ${nv}` : nv;
    console.error(`[cookie+] ${k.trim()} (${r.join("=").trim().length}B)`);
  },
});

// 记录网络
const net = [];
const realFetch = globalThis.fetch;
w.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : (input && input.url);
  net.push({ url, method: (init.method || "GET").toUpperCase(), body: init.body ? String(init.body).slice(0, 300) : null });
  console.error(`[fetch] ${init.method || "GET"} ${url}`);
  return { ok: true, status: 200, headers: { get: () => null, getSetCookie: () => [] },
           text: async () => "", json: async () => ({}) };
};
w.XMLHttpRequest = function () {
  const self = this;
  return {
    open(m, u) { self.__m = m; self.__u = u; },
    send(b) { net.push({ url: self.__u, method: self.__m, body: b ? String(b).slice(0, 300) : null });
              console.error(`[xhr] ${self.__m} ${self.__u}`); },
    setRequestHeader() {}, addEventListener() {}, getAllResponseHeaders: () => "",
    get responseText() { return ""; }, get status() { return 200; },
  };
};

// 执行 widget
let err = null;
try {
  const src = fs.readFileSync(WIDGET, "utf8");
  console.error(`[run] widget ${src.length} B`);
  w.eval(src);
  console.error("[run] widget 执行完成，无异常");
} catch (e) {
  err = String((e && e.message) || e);
  console.error(`[run] 抛出：${err}`);
}

setTimeout(() => {
  const out = {
    ok: !err,
    err,
    cookies: cookies.map((c) => `${c.name}:${c.len}`),
    net_count: net.length,
    net_sample: net.slice(0, 5),
    has_ts: typeof w.$_ts,
    canvas_ok: !!(w.document.createElement("canvas").getContext("2d")),
  };
  console.log(JSON.stringify(out, null, 2));
  try { w.__clearAllTimers && w.__clearAllTimers(); w.close(); } catch (e) {}
  process.exit(err ? 1 : 0);
}, 1200);

