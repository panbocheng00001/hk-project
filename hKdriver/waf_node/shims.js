// shims.js —— 环境垫片：把 WAF 脚本要的浏览器 API 补到 jsdom window 上
// =====================================================================
// 原则：
//   1) 报错驱动 —— 别预先堆一大坨；probe.js 报缺什么就补什么
//   2) 值要自洽 —— UA / platform / sec-ch-ua 必须互相对得上，否则反而露出马脚
//   3) 只补「读」不补「写」的地方用 defineProperty，避免 jsdom 的 readonly 抛错

function define(w, name, value) {
  try {
    Object.defineProperty(w, name, { configurable: true, get: () => value });
  } catch (e) {
    try { w[name] = value; } catch (e2) { /* ignore */ }
  }
}

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36";

function buildShims(w) {
  // ---------------- navigator（jsdom 自带的字段是只读的，整体替换）
  const nav = {
    userAgent: UA,
    appVersion: UA.replace("Mozilla/", ""),
    appName: "Netscape",
    appCodeName: "Mozilla",
    platform: "Win32",
    vendor: "Google Inc.",
    product: "Gecko",
    productSub: "20030107",
    language: "zh-CN",
    languages: ["zh-CN", "zh"],
    webdriver: false,               // 关键：自动化标记必须是 false
    hardwareConcurrency: 8,
    deviceMemory: 8,
    maxTouchPoints: 0,
    onLine: true,
    cookieEnabled: true,
    doNotTrack: null,
    plugins: { length: 5 },
    mimeTypes: { length: 2 },
    connection: { effectiveType: "4g", rtt: 50, downlink: 10, saveData: false },
    // 常用方法
    javaEnabled: () => false,
    sendBeacon: () => true,
    vibrate: () => true,
  };
  define(w, "navigator", nav);

  // ---------------- screen
  define(w, "screen", {
    width: 1920, height: 1080,
    availWidth: 1920, availHeight: 1040,
    availLeft: 0, availTop: 0,
    colorDepth: 24, pixelDepth: 24,
    orientation: { type: "landscape-primary", angle: 0 },
  });

  // ---------------- performance（jsdom 有，但缺 markResourceTiming）
  const perf = w.performance || {};
  if (!perf.now) perf.now = () => Date.now() % 1e6;
  if (!perf.markResourceTiming) perf.markResourceTiming = () => {};
  if (!perf.timing) perf.timing = {};
  if (!perf.timeOrigin) perf.timeOrigin = Date.now();
  define(w, "performance", perf);

  // ---------------- 定时器（jsdom 有；这里只做记账，方便收尾时清理）
  const timers = new Set();
  const rawSetInterval = w.setInterval;
  const rawClearInterval = w.clearInterval;
  w.setInterval = (fn, ms, ...a) => {
    const id = rawSetInterval(fn, Math.max(10, ms || 10), ...a);
    timers.add(id);
    return id;
  };
  w.clearInterval = (id) => { timers.delete(id); return rawClearInterval(id); };
  w.__clearAllTimers = () => { for (const id of timers) { try { rawClearInterval(id); } catch (e) {} } timers.clear(); };

  // ---------------- 事件 / 媒体 / 其他
  if (!w.requestAnimationFrame) {
    w.requestAnimationFrame = (cb) => w.setTimeout(() => cb(w.performance.now()), 16);
    w.cancelAnimationFrame = (id) => w.clearTimeout(id);
  }
  if (!w.matchMedia) w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  if (!w.devicePixelRatio) define(w, "devicePixelRatio", 1);
  if (!w.innerWidth) define(w, "innerWidth", 1920);
  if (!w.innerHeight) define(w, "innerHeight", 940);
  if (!w.outerWidth) define(w, "outerWidth", 1920);
  if (!w.outerHeight) define(w, "outerHeight", 1040);
  if (!w.scrollX) define(w, "scrollX", 0);
  if (!w.scrollY) define(w, "scrollY", 0);

  // ---------------- WebGL 指纹：给个固定的假值，别让脚本读到 null 起疑
  const origGetContext = w.HTMLCanvasElement && w.HTMLCanvasElement.prototype.getContext;
  if (origGetContext) {
    w.HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
      if (/webgl/i.test(type)) {
        return {
          getParameter: (p) => {
            if (p === 0x9245) return "Google Inc.";                       // UNMASKED_VENDOR_WEBGL
            if (p === 0x9246) return "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)";
            if (p === 0x1F00) return "WebKit";                            // VENDOR
            if (p === 0x1F01) return "WebKit WebGL";                      // RENDERER
            return null;
          },
          getExtension: () => null,
          getSupportedExtensions: () => [],
          getShaderPrecisionFormat: () => ({ precision: 23, rangeMin: 127, rangeMax: 127 }),
        };
      }
      return origGetContext.call(this, type, ...rest);
    };
  }

  // ---------------- 语言/时区一致性
  try { w.Intl.DateTimeFormat().resolvedOptions = () => ({ locale: "zh-CN", timeZone: "Asia/Shanghai" }); } catch (e) {}

  return w;
}

module.exports = { buildShims, UA };
