// probe.js —— P0 探针：把 WAF 的 widget JS 丢进 Node，输出「缺什么环境」
// =====================================================================
// 用法：
//   node probe.js --widget <path>            # 只测 widget
//   node probe.js --page <challenge.html> --widget <path>   # 先跑内联引导段再跑 widget
//
// 输出：{ ok, err, missing: [...], touched: [...] }
//   missing = 脚本访问了但你没给的属性（补环境的待办清单）
//
// 设计原则：**报错驱动**。一次只补被点名的那一个，补完立刻重跑。

const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const arg = (k, d = null) => {
  const i = args.indexOf(`--${k}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};

const widgetPath = arg("widget");
const pagePath = arg("page");
if (!widgetPath) {
  console.error("用法: node probe.js --widget <widget.js> [--page <challenge.html>]");
  process.exit(2);
}

// ---------------------------------------------------------------- 记录器
const touched = new Map();   // "obj.prop" -> 次数
const missing = new Set();   // 访问了但对象上不存在的属性
const calls = [];            // 被调用的函数名（截断）

function note(obj, key) {
  const id = `${obj}.${key}`;
  touched.set(id, (touched.get(id) || 0) + 1);
}

// ---------------------------------------------------------------- 万能垫片
// 关键点：dummy 之间互相引用，任何「深挖」都不会崩，从而把报错推到真正需要的语义处。
function makeDummy(label, overrides = {}) {
  const target = function () {};
  const base = Object.assign({
    // 基础转换，避免数学运算/拼接直接崩
    toString: () => "",
    valueOf: () => 0,
    length: 0,
    // 常见 DOM 字段
    nodeType: 1,
    readyState: "complete",
  }, overrides);

  const proxy = new Proxy(target, {
    get(t, k) {
      if (k === Symbol.toPrimitive) return (hint) => (hint === "number" ? 0 : "");
      if (k === Symbol.iterator) return function* () {};
      if (k === Symbol.toStringTag) return label;
      if (k === "then") return undefined;          // 别被误当 thenable
      if (k in t) return t[k];
      note(label, String(k));
      if (!(k in base)) missing.add(`${label}.${String(k)}`);
      const child = makeDummy(`${label}.${String(k)}`);
      t[k] = child;
      return child;
    },
    set(t, k, v) { t[k] = v; return true; },
    apply() { calls.push(`${label}()`); return makeDummy(`${label}()`); },
    construct() { calls.push(`new ${label}`); return makeDummy(`new ${label}`); },
    has() { return true; },
  });
  // 注意：函数对象的 length 是只读的，不能 Object.assign，要逐个安全赋值
  for (const [k, v] of Object.entries(base)) {
    try { target[k] = v; }
    catch (e) { try { Object.defineProperty(target, k, { value: v, configurable: true, writable: true }); } catch (e2) {} }
  }
  return proxy;
}

// ---------------------------------------------------------------- 搭环境
const sandbox = {};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.top = sandbox;
sandbox.parent = sandbox;
sandbox.frames = sandbox;
sandbox.globalThis = sandbox;
sandbox.opener = null;

const doc = makeDummy("document", {
  cookie: "",
  referrer: "",
  readyState: "complete",
  documentMode: undefined,
  location: null,
  defaultView: sandbox,
});
sandbox.document = doc;
sandbox.location = makeDummy("location", {
  href: "https://abs3.example.test/?lang=sc",
  protocol: "https:",
  host: "abs3.example.test",
  hostname: "abs3.example.test",
  pathname: "/",
  search: "?lang=sc",
  hash: "",
  origin: "https://abs3.example.test",
});
doc.location = sandbox.location;

sandbox.navigator = makeDummy("navigator", {
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
  platform: "Win32",
  language: "zh-CN",
  languages: ["zh-CN", "zh"],
  webdriver: false,
  hardwareConcurrency: 8,
  maxTouchPoints: 0,
});
sandbox.screen = makeDummy("screen", { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24 });
sandbox.performance = makeDummy("performance", {
  now: () => Date.now() % 100000,
  timing: {},
  markResourceTiming: () => {},
});
sandbox.setTimeout = setTimeout;
sandbox.setInterval = setInterval;
sandbox.clearTimeout = clearTimeout;
sandbox.clearInterval = clearInterval;
sandbox.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
sandbox.cancelAnimationFrame = clearTimeout;
sandbox.fetch = () => Promise.resolve(makeDummy("Response", { ok: true, status: 200 }));
sandbox.XMLHttpRequest = function () { return makeDummy("XHR"); };
sandbox.Event = function () { return makeDummy("Event"); };
sandbox.CustomEvent = sandbox.Event;
sandbox.Image = function () { return makeDummy("Image"); };
sandbox.atob = (s) => Buffer.from(s, "base64").toString("binary");
sandbox.btoa = (s) => Buffer.from(s, "binary").toString("base64");
sandbox.crypto = require("crypto").webcrypto || {
  getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = (Math.random() * 256) | 0; return a; },
};
sandbox.console = console;
sandbox.$_ts = { cd: "qE2Srcql", nsd: 78852, cc: 1 };   // 挑战页内联段会设，这里先占位

// ---------------------------------------------------------------- 执行
const vm = require("vm");
const ctx = vm.createContext(sandbox);

function run(label, code) {
  try {
    vm.runInContext(code, ctx, { filename: label, timeout: 8000 });
    return null;
  } catch (e) {
    return String((e && e.message) || e);
  }
}

let err = null;

if (pagePath) {
  const html = fs.readFileSync(pagePath, "utf8");
  // 抽出内联 script（挑战页的引导段）
  const inlines = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((m) => m[1]).filter((s) => s.trim().length > 0);
  console.error(`[probe] 内联脚本 ${inlines.length} 段`);
  for (let i = 0; i < inlines.length; i++) {
    err = run(`inline#${i}`, inlines[i]);
    if (err) { console.error(`[probe] inline#${i} 失败: ${err}`); break; }
  }
}

if (!err) {
  const src = fs.readFileSync(widgetPath, "utf8");
  console.error(`[probe] widget ${src.length} B`);
  err = run("widget", src);
}

setTimeout(() => {
  const report = {
    ok: !err,
    err,
    missing_count: missing.size,
    missing: [...missing].slice(0, 60),
    touched_count: touched.size,
    touched_top: [...touched.entries()].sort((a, b) => b[1] - a[1]).slice(0, 40),
    calls_count: calls.length,
    calls_sample: calls.slice(0, 20),
    timers_still_running: "（脚本可能挂了定时器，进程需手动退出）",
  };
  console.log(JSON.stringify(report, null, 2));
  process.exit(err ? 1 : 0);
}, 1500);


