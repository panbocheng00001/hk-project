// probe_widget.js —— 纯脚本可行性第一探：把 WAF 的 widget JS 丢进 Node，看它第一口要什么
const fs = require('fs');
const path = 'runs/captcha_specimens/waf20261116/waf_widget_8qcykgusbcwc.5057567.js';
const src = fs.readFileSync(path, 'utf8');

const need = new Set();
const shim = {
  // 最小 document / window：够它跑第一段即可，缺什么由报错驱动补
  document: {
    cookie: '', createElement: () => ({ getContext: () => null, style: {} }),
    documentElement: { appendChild() {}, style: {} },
    body: { appendChild() {}, style: {} },
    addEventListener() {}, getElementById: () => null,
    querySelector: () => null, querySelectorAll: () => [],
  },
  navigator: { userAgent: 'Mozilla/5.0', platform: 'Win32', language: 'zh-CN' },
  location: { href: 'https://abs3.td.gov.hk/', protocol: 'https:', host: 'abs3.td.gov.hk' },
  setTimeout, setInterval, clearTimeout, clearInterval,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
  console,
};
global.window = global;
global.self = global;
for (const [k, v] of Object.entries(shim)) global[k] = v;
global.$_ts = { cd: 'qE2Srcql', nsd: 78852, cc: 1 };

// 用 Proxy 记录被摸到的属性，能直观看出它要哪些环境
const touched = [];
const spyAndShim = (name, obj) => new Proxy(obj, {
  get(t, p) {
    if (typeof p === 'string' && !(p in t)) touched.push(`${name}.${p}`);
    return t[p];
  },
});
global.document = spyAndShim('document', shim.document);
global.navigator = spyAndShim('navigator', shim.navigator);
global.location = spyAndShim('location', shim.location);

let stage = 'init';
try {
  stage = 'parse';
  new Function(src)();
  console.log(JSON.stringify({ ok: true, note: '直接跑通了（意外）' }));
} catch (e) {
  console.log(JSON.stringify({
    ok: false, stage,
    err: String(e && e.message || e).slice(0, 300),
    touched: [...new Set(touched)].slice(0, 40),
  }, null, 2));
}
