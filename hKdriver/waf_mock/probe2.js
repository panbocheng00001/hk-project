// probe2.js —— 用「万能 dummy」喂环境，看脚本能跑多远
const fs = require('fs');
const src = fs.readFileSync('runs/captcha_specimens/waf20261116/waf_widget_8qcykgusbcwc.5057567.js', 'utf8');

const touched = new Set();
function dummy(name, real = {}) {
  const base = Object.assign({
    then: undefined,
    length: 0,
    toString: () => '',
    valueOf: () => 0,
  }, real);
  return new Proxy(base, {
    get(t, p) {
      if (p === Symbol.toPrimitive) return () => '';
      if (p === Symbol.iterator) return function* () {};
      if (p === 'nodeType') return 1;
      if (p in t) return t[p];
      touched.add(`${name}.${String(p)}`);
      if (p === 'cookie') return '';
      return dummy(`${name}.${String(p)}`);
    },
    set(t, p, v) { t[p] = v; return true; },
    apply() { touched.add(`${name}()`); return dummy(name + '()'); },
  });
}

global.window = global;
global.self = global;
global.document = dummy('document', { cookie: '', referrer: '', readyState: 'complete' });
global.navigator = dummy('navigator', { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' });
global.location = dummy('location', { href: 'https://abs3.td.gov.hk/', protocol: 'https:', host: 'abs3.td.gov.hk', pathname: '/' });
global.screen = dummy('screen');
global.performance = dummy('performance', { now: () => Date.now() });
global.addEventListener = () => {};
global.setTimeout = setTimeout; global.setInterval = setInterval;
global.clearTimeout = clearTimeout; global.clearInterval = clearInterval;
global.fetch = () => Promise.resolve(dummy('resp', { ok: true, status: 200 }));
global.XMLHttpRequest = function () { return dummy('xhr'); };
global.top = global;
global.parent = global;
global.frames = global;
global.opener = null;
global.document.location = global.location;
global.document.defaultView = global;
global.$_ts = { cd: 'qE2Srcql', nsd: 78852, cc: 1 };

let ok = false, err = null;
try { new Function(src)(); ok = true; } catch (e) { err = String(e && e.message || e).slice(0, 240); }

console.log(JSON.stringify({
  ran_to_completion: ok,
  err,
  touched_count: touched.size,
  touched_sample: [...touched].slice(0, 45),
}, null, 2));

