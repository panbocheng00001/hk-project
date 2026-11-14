'use strict';
/*
 * waf_solve.js —— 华为云 WAF（HWWAF）412 动态防护挑战求解器（纯 Node，无浏览器）
 *
 * 原理：
 *   412 页面 = meta(动态数据) + 内联($_ts.nsd/.cd) + 外链混淆JS + 末尾 _$c3()
 *   在 vm 沙箱里伪造 DOM/网络环境执行它：
 *     ① 解码 $_ts.cd  → 得到 cookie 名/参数名/算法种子（写回 $_ts.lcd/cp/jf/aebi/scj）
 *     ② 采集环境算校验值
 *     ③ XHR/fetch 发到 WAF 验证端点（带 HWWAFSESID）
 *     ④ 服务端回 Set-Cookie: HIK0kat3rt9xO=<token>
 *
 * 用法：
 *   node waf_solve.js --page chall.html --js chall.js --url https://abs3.td.gov.hk/?lang=sc
 *   node waf_solve.js --page chall.html --js chall.js --dry       # 不真发网络，只看解码
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');

const argv = process.argv.slice(2);
function arg(name, def = '') { const i = argv.indexOf('--' + name); return i >= 0 ? (argv[i + 1] || '') : def; }
const PAGE = arg('page');
const JSFILE = arg('js');
const URLARG = arg('url', 'https://abs3.td.gov.hk/?lang=sc');
const DRY = argv.includes('--dry');
const VERBOSE = argv.includes('--verbose');

// ---------- 万能 stub：让任何未知属性都可访问/可调用，避免 TypeError ----------
function makeStub() {
  const cache = new Map();
  function s(name) {
    if (cache.has(name)) return cache.get(name);
    const f = function () { return s(name + '()'); };
    const p = new Proxy(f, {
      get(t, k) {
        if (k === Symbol.toPrimitive) return () => 0;
        if (k === Symbol.iterator) return function* () {};
        if (k === 'toString') return () => '';
        if (k === 'valueOf') return () => 0;
        if (k === 'length') return 0;
        return s(name + '.' + String(k));
      },
      apply() { return s(name + '()'); },
      construct() { return s(name + '()'); },
      set() { return true; },
    });
    cache.set(name, p);
    return p;
  }
  return s;
}
const STUB = makeStub();

function proxify(obj, name) {
  return new Proxy(obj, {
    get(t, k) {
      try { if (k in t) return t[k]; } catch { /* symbol */ }
      return STUB(name + '.' + String(k));
    },
    set(t, k, v) { t[k] = v; return true; },
    has() { return true; },
  });
}

// ---------- cookie jar ----------
const jar = Object.create(null);
function applySetCookie(sc) {
  if (!sc) return;
  for (const line of [].concat(sc)) {
    const first = String(line).split(';')[0];
    const i = first.indexOf('=');
    if (i > 0) jar[first.slice(0, i).trim()] = first.slice(i + 1).trim();
  }
}

// ---------- 页面解析 ----------
function parsePage(html) {
  const out = { meta: {}, nsd: 0, cd: '', jsPath: '', entry: '' };
  let m = html.match(/<meta[^>]+id="([^"]+)"[^>]+content="([^"]+)"/i);
  if (m) out.meta = { id: m[1], content: m[2] };
  m = html.match(/\$_ts\.nsd\s*=\s*(\d+)/); if (m) out.nsd = parseInt(m[1], 10);
  m = html.match(/\$_ts\.cd\s*=\s*"([^"]*)"/); if (m) out.cd = m[1];
  m = html.match(/<script[^>]+src="([^"]+\.js[^"]*)"[^>]*r=["']?m/i);
  if (!m) m = html.match(/<script[^>]+src="(\/[^"]+\.js)"/i);
  if (m) out.jsPath = m[1];
  // 入口函数名每次随机（_$c3 / _$jr / _$_I …）
  const calls = [...html.matchAll(/_(\$[A-Za-z0-9_]{1,6})\(\)/g)].map(x => x[0].replace('()', ''));
  if (calls.length) out.entry = calls[calls.length - 1].replace(/^_/, '_');
  if (out.entry && !out.entry.startsWith('_$')) out.entry = '';
  return out;
}
function extractInline(html) {
  const parts = [];
  const re = /<script[^>]*r=["']m["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) {
    const code = m[1].trim();
    if (!code || /^<script/i.test(code)) continue;
    // 跳过「只调用入口函数」的块（此时函数还没定义，交给后面单独调）
    if (/^_\$[A-Za-z0-9_]{1,8}\(\);?$/.test(code)) continue;
    parts.push(code);
  }
  return parts.join('\n;\n');
}

// ---------- 环境 ----------
function buildEnv(meta = {}, jsPath = '') {
  const log = [];
  const requests = [];

  // 挑战 JS 会读 <meta id=… content=…> 和它自己的 <script src>
  const metaEl = proxify({ id: meta.id || '', content: meta.content || '', getAttribute: (k) => (k === 'content' ? meta.content : k === 'id' ? meta.id : ''), name: 'content' }, 'meta');
  const scriptEl = proxify({ src: jsPath || '', type: 'text/javascript', getAttribute: () => jsPath || '' }, 'script');
  const linkEl = proxify({ href: '', rel: 'stylesheet', getAttribute: () => '' }, 'link');

  const docBase = {
    createElement: (tag) => proxify({ style: {}, tagName: String(tag || '').toUpperCase(), setAttribute() {}, appendChild() {}, getAttribute() { return ''; } }, 'el:' + tag),
    createTextNode: () => ({}),
    getElementsByTagName: (tag) => {
      const t = String(tag || '').toLowerCase();
      if (t === 'meta') return [metaEl];
      if (t === 'script') return [scriptEl, scriptEl];
      if (t === 'link') return [linkEl];
      if (t === 'head') return [proxify({ appendChild() {} }, 'head')];
      if (t === 'body') return [proxify({ appendChild() {} }, 'body')];
      return [proxify({ appendChild() {} }, 'el:' + tag)];
    },
    getElementById: (id) => (String(id) === (meta.id || '') ? metaEl : proxify({ appendChild() {}, style: {}, getAttribute() { return ''; } }, 'byId:' + id)),
    querySelector: (sel) => (String(sel || '').includes('meta') ? metaEl : (String(sel || '').includes('script') ? scriptEl : proxify({}, 'qs'))),
    querySelectorAll: (sel) => (String(sel || '').includes('meta') ? [metaEl] : String(sel || '').includes('script') ? [scriptEl] : []),
    addEventListener() {}, removeEventListener() {},
    head: proxify({ appendChild() {} }, 'head'),
    body: proxify({ appendChild() {} }, 'body'),
    documentElement: proxify({ style: {} }, 'html'),
    readyState: 'complete', referrer: '', characterSet: 'UTF-8',
    _cookie: '',
  };
  Object.defineProperty(docBase, 'cookie', {
    get() { return this._cookie; },
    set(v) {
      const first = String(v).split(';')[0];
      const i = first.indexOf('=');
      if (i > 0) {
        const k = first.slice(0, i).trim(), val = first.slice(i + 1).trim();
        jar[k] = val;
        log.push('COOKIE_SET ' + k + '=' + val.slice(0, 40));
      }
      this._cookie = Object.entries(jar).map(([a, b]) => `${a}=${b}`).join('; ');
    },
  });
  const doc = proxify(docBase, 'document');

  class XHR {
    constructor() { this.readyState = 0; this.status = 0; this.responseText = ''; this.response = ''; this._h = {}; this.onreadystatechange = this.onload = this.onerror = null; }
    open(m, u) { this._m = m; this._u = u; this.readyState = 1; }
    setRequestHeader(k, v) { this._h[k] = v; }
    getAllResponseHeaders() { return this._rh || ''; }
    getResponseHeader(k) { const m = new RegExp('^' + k + ':\\s*(.*)$', 'im').exec(this._rh || ''); return m ? m[1] : null; }
    send(body) {
      const rec = { method: this._m, url: this._u, body: body == null ? '' : String(body), headers: { ...this._h } };
      requests.push(rec);
      log.push(`XHR ${rec.method} ${rec.url} body=${rec.body.length}`);
    }
    abort() {} addEventListener() {}
  }

  const nav = proxify({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    appName: 'Netscape', appVersion: '5.0 (Windows NT 10.0; Win64; x64)',
    platform: 'Win32', product: 'Gecko', vendor: 'Google Inc.',
    language: 'zh-CN', languages: ['zh-CN', 'zh'], cookieEnabled: true, onLine: true,
    hardwareConcurrency: 8, deviceMemory: 8, maxTouchPoints: 0, webdriver: false,
  }, 'navigator');

  const loc = new URL(URLARG);
  const win = {
    document: doc, navigator: nav,
    screen: proxify({ width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24, availLeft: 0, availTop: 0 }, 'screen'),
    location: proxify({ href: URLARG, protocol: loc.protocol, host: loc.host, hostname: loc.hostname, port: loc.port, pathname: loc.pathname, search: loc.search, hash: '', origin: loc.origin, replace() {}, assign() {}, reload() {} }, 'location'),
    history: proxify({ length: 2, pushState() {}, replaceState() {} }, 'history'),
    localStorage: proxify({ _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } }, 'localStorage'),
    sessionStorage: proxify({ _d: {}, getItem(k) { return this._d[k] ?? null; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } }, 'sessionStorage'),
    innerWidth: 1920, innerHeight: 947, outerWidth: 1920, outerHeight: 1040, devicePixelRatio: 1,
    screenX: 0, screenY: 0, pageXOffset: 0, pageYOffset: 0,
    setTimeout: (fn, ms) => { try { return setTimeout(fn, 0); } catch { return 0; } },
    clearTimeout: (id) => clearTimeout(id),
    setInterval: () => 0, clearInterval() {},
    requestAnimationFrame: (fn) => { try { return setTimeout(() => fn(Date.now()), 0); } catch { return 0; } },
    cancelAnimationFrame() {},
    XMLHttpRequest: XHR,
    fetch: (u, o) => {
      const rec = { method: (o && o.method) || 'GET', url: String(u), body: (o && o.body) || '', headers: {} };
      requests.push(rec); log.push('FETCH ' + rec.method + ' ' + rec.url);
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, text: async () => '', json: async () => ({}) });
    },
    addEventListener() {}, removeEventListener() {},
    atob: (s) => Buffer.from(String(s), 'base64').toString('binary'),
    btoa: (s) => Buffer.from(String(s), 'binary').toString('base64'),
    performance: { now: () => Date.now(), timing: {}, getEntriesByType: () => [] },
    crypto: { getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256); return a; } },
    console: { log: (...a) => VERBOSE && console.log('[page]', ...a), warn() {}, error() {} },
    Image: function () { return proxify({ width: 0, height: 0 }, 'img'); },
    Date, Math, JSON, String, Number, Array, Object, RegExp, Error, Promise, Map, Set,
    encodeURIComponent, decodeURIComponent, encodeURI, decodeURI, escape, unescape,
    parseInt, parseFloat, isNaN, isFinite, undefined,
  };
  win.window = win; win.globalThis = win; win.self = win; win.top = win; win.parent = win;
  win.frames = [];
  win.document.defaultView = win;

  return { win, doc, log, requests };
}

// ---------- 主流程 ----------
(async function main() {
  const pageInfo = { meta: {}, nsd: 0, cd: '', jsPath: '' };
  let inline = '';
  if (PAGE) {
    const html = fs.existsSync(PAGE) ? fs.readFileSync(PAGE, 'utf8') : '';
    if (!html) { console.error('页面不存在:', PAGE); process.exit(2); }
    Object.assign(pageInfo, parsePage(html));
    inline = extractInline(html);
  }
  const bigJsRaw = JSFILE && fs.existsSync(JSFILE) ? fs.readFileSync(JSFILE, 'utf8') : '';
  if (!bigJsRaw) { console.error('缺少混淆 JS（--js）'); process.exit(2); }
  // 反调试 patch：把 debugger 语句全部干掉（否则 devtools/断点会拖死或触发死循环）
  const dbgCount = (bigJsRaw.match(/\bdebugger\b/g) || []).length;
  const bigJs = bigJsRaw.replace(/\bdebugger\b/g, ';');
  if (dbgCount) console.log(`  [patch] debugger ×${dbgCount} 已置空`);

  console.log('=== 挑战页解析 ===');
  console.log('  meta id :', pageInfo.meta.id || '(无)');
  console.log('  nsd     :', pageInfo.nsd || '(无)', ' cd len:', (pageInfo.cd || '').length);
  console.log('  js path :', pageInfo.jsPath || '(无)', ' inline:', inline.length);

  const { win, log, requests } = buildEnv(pageInfo.meta, pageInfo.jsPath);
  const ctx = vm.createContext(win);
  try { vm.runInContext('var window=this, self=this, globalThis=this;', ctx); } catch (e) { console.log('  ctx 初始化:', e.message); }

  // 1) 内联：$_ts
  if (inline) {
    try { vm.runInContext(inline, ctx, { timeout: 20000 }); }
    catch (e) { console.log('  inline 异常:', e.message.slice(0, 160)); }
  }
  const ts = win.$_ts || (win.$_ts = {});
  if (pageInfo.nsd) ts.nsd = pageInfo.nsd;
  if (pageInfo.cd) ts.cd = pageInfo.cd;

  // 2) 混淆 JS
  console.log('\n=== 执行挑战 JS ===');
  try { vm.runInContext(bigJs, ctx, { timeout: 60000 }); }
  catch (e) { console.log('  异常:', e.constructor.name, e.message.slice(0, 240)); }
  console.log('  _$c3 =', typeof win._$c3, ' 全局 _$* :', Object.keys(win).filter(k => /^_\$/.test(k)).slice(0, 12).join(','));

  const tsKeys = ['nsd', 'lcd', 'cp', 'jf', 'aebi', 'scj'];
  console.log('  $_ts 解码结果:');
  for (const k of tsKeys) {
    const v = win.$_ts[k];
    console.log('    ' + k.padEnd(5), '=', typeof v === 'string' ? v.slice(0, 80) : JSON.stringify(v));
  }

  // 3) 入口（名字每次随机：_$c3 / _$jr / _$_I …）
  const entryCands = [];
  if (pageInfo.entry) entryCands.push(pageInfo.entry);
  for (const k of Object.keys(win)) {
    if (/^_\$[A-Za-z0-9_]{1,8}$/.test(k) && typeof win[k] === 'function') entryCands.push(k);
  }
  console.log('  入口候选:', entryCands.slice(0, 8).join(', ') || '(无)');
  for (const cand of entryCands) {
    try {
      console.log(`\n=== 调用 ${cand}() ===`);
      vm.runInContext(cand + '()', ctx, { timeout: 60000 });
      console.log('  调用成功');
      break;
    } catch (e) {
      console.log('  异常:', e.constructor.name, e.message.slice(0, 260));
    }
  }
  await new Promise(r => setTimeout(r, 300));

  console.log('\n=== 结果 ===');
  console.log('  捕获请求:', requests.length);
  for (const r of requests.slice(0, 10)) {
    console.log(`    ${r.method} ${r.url}  body=${r.body.length}B`);
    if (r.body && VERBOSE) console.log('      ', r.body.slice(0, 300));
  }
  console.log('  cookie:', JSON.stringify(jar).slice(0, 600));
  console.log('  HIK0kat3rt9xO =', jar['HIK0kat3rt9xO'] || '(未拿到)');
  const out = path.join(process.env.TEMP || '.', 'waf_solve_out.json');
  fs.writeFileSync(out, JSON.stringify({ at: new Date().toISOString(), url: URLARG, jar, requests, nsd: pageInfo.nsd, ts: { lcd: win.$_ts.lcd, cp: win.$_ts.cp, jf: win.$_ts.jf, aebi: win.$_ts.aebi, scj: win.$_ts.scj } }, null, 2));
  console.log('  →', out);
  if (VERBOSE) console.log('\n--- log ---\n' + log.slice(0, 100).join('\n'));
})();
