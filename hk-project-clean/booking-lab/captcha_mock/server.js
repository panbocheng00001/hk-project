// server.js —— 验证码复刻服务（本地 Node，无第三方依赖）
// ============================================================================
// 用途：让你的脚本在一个「和线上同构」的滑块题上回归，不碰真站。
//
// 端点：
//   GET  /                      复刻题目页（canvas + 轨道 + 手柄，几何与线上一致）
//   GET  /challenge?seed=&dpr=  取一道新题（JSON：dataUrl / 几何 / 不返回真值）
//   POST /analyze               与线上 /captcha **完全同形**的入参 → 返回 dx
//   POST /grade                 提交 {seed, dx 或 sliderDx} → 判定是否命中（含真值）
//   GET  /specimen/:id          回放历史样本（runs/captcha_specimens）
//   GET  /specimens             样本索引
//   GET  /healthz
//
// 关键设计：
//   · /analyze 的入参/出参照抄线上，脚本改一行 baseURL 就能切过来
//   · /grade 只在本地返回真值，线上永远拿不到（防非预期使用）
//   · 种子固定时题目可复现，便于把失败样本固化成回归用例
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');
const crypto = require('crypto');
const challenge = require('./challenge');
const analyze = require('./analyze');

const PORT = Number(process.env.PORT || 8777);
const SPEC_DIR = path.join(__dirname, '..', 'runs', 'captcha_specimens');

// 与线上实测一致：位图 400×330、轨道 400、手柄 34
const GEOM = { w: 400, h: 330, trackW: 400, handleW: 34 };

const TOL = Number(process.env.TOL || 3);   // 判定容差（位图像素）

function json(res, obj, code) {
  const body = Buffer.from(JSON.stringify(obj));
  res.writeHead(code || 200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    let n = 0;
    req.on('data', (c) => { n += c.length; if (n < 20 * 1024 * 1024) chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
  });
}

const PAGE = `<!doctype html><meta charset="utf-8"><title>captcha replica</title>
<style>
 body{background:#0e0e14;color:#ddd;font-family:system-ui,sans-serif;padding:20px}
 canvas{display:block;width:400px;height:330px;background:#1b2430;border-radius:8px}
 #track{position:relative;width:400px;height:34px;margin-top:12px;background:#262f3c;border-radius:17px}
 #handle{position:absolute;left:0;top:0;width:34px;height:34px;border-radius:17px;background:#4aa3ff;cursor:fill_form}
 code{color:#8fd}
</style>
<p>拖动滑块，让实心图形和空心图形完全重合。</p>
<canvas id="c" width="400" height="330"></canvas>
<div id="track"><div id="handle"></div></div>
<p>seed=<code id="seed"></code>　（这是复刻题，不是真站；真值只由 /grade 返回）</p>
<script>
let cur = null;
async function newChallenge(seed) {
  const q = seed ? ('?seed=' + seed) : '';
  const r = await fetch('/challenge' + q).then((x) => x.json());
  cur = r;
  document.getElementById('seed').textContent = r.seed;
  const img = new Image();
  img.onload = () => { const g = document.getElementById('c').getContext('2d'); g.drawImage(img, 0, 0); };
  img.src = r.dataUrl;
  document.getElementById('handle').style.left = '0px';
  return r;
}
async function analyze() {
  const c = document.getElementById('c');
  const r = await fetch('/analyze', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      dataUrl: c.toDataURL('image/png'), bmpW: c.width, bmpH: c.height,
      cssW: 400, cssH: 330, dpr: window.devicePixelRatio,
      trackW: 400, handleW: 34, seed: cur && cur.seed
    })
  }).then((x) => x.json());
  console.log('analyze', r);
  return r;
}
document.getElementById('handle').addEventListener('pointerup', async () => {
  const left = parseFloat(document.getElementById('handle').style.left) || 0;
  const r = await fetch('/grade', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sliderDx: left, bmpW: 400, trackW: 400, handleW: 34, seed: cur && cur.seed })
  }).then((x) => x.json());
  document.body.style.outline = r.pass ? '2px solid #3c3' : '2px solid #c33';
  console.log('grade', r);
});
newChallenge();
</script>`;

const server = http.createServer(async (req, res) => {
  const u = url.parse(req.url, true);
  const p = u.pathname;

  if (req.method === 'OPTIONS') return json(res, { ok: true });

  if (p === '/healthz') return json(res, { ok: true, geom: GEOM, tol: TOL });

  if (p === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(PAGE);
  }

  if (p === '/challenge') {
    const seed = Number(u.query.seed || Math.floor(Math.random() * 1e9));
    const dpr = Number(u.query.dpr || 1);
    const w = GEOM.w * (dpr > 1 ? 1 : 1), h = GEOM.h;
    const c = challenge.make(seed, w, h);
    return json(res, {
      seed, geom: GEOM, dpr,
      bmpW: c.w, bmpH: c.h,
      dataUrl: c.dataUrl,
      sha256: crypto.createHash('sha256').update(c.png).digest('hex'),
      // 故意不返回 truth.dx
    });
  }

  if (p === '/analyze' && req.method === 'POST') {
    try {
      const body = JSON.parse(await readBody(req) || '{}');
      const seed = body.seed;
      const r = analyze.analyzePayload({
        dataUrl: body.dataUrl, bmpW: body.bmpW, bmpH: body.bmpH,
        cssW: body.cssW, cssH: body.cssH, dpr: body.dpr, inFrame: body.inFrame,
        trackW: body.trackW, handleW: body.handleW,
      });
      // 本地调试时把真值一并给出，便于定位算法偏差（线上不返回）
      if (seed != null) {
        const c = challenge.make(Number(seed), GEOM.w, GEOM.h);
        r.truth_dx = c.truth.dx;
        r.err = r.ok ? +(r.dx - c.truth.dx).toFixed(2) : null;
      }
      return json(res, r);
    } catch (e) {
      return json(res, { ok: false, error: String(e && e.message || e) }, 400);
    }
  }

  if (p === '/grade' && req.method === 'POST') {
    try {
      const b = JSON.parse(await readBody(req) || '{}');
      if (b.seed == null) return json(res, { ok: false, error: 'seed required' }, 400);
      const c = challenge.make(Number(b.seed), GEOM.w, GEOM.h);
      let dx;
      if (typeof b.dx === 'number') dx = b.dx;
      else if (typeof b.sliderDx === 'number') {
        const range = Math.max(10, (b.trackW || GEOM.trackW) - (b.handleW || GEOM.handleW));
        dx = (b.sliderDx / range) * (b.bmpW || GEOM.w);
      } else return json(res, { ok: false, error: 'dx or sliderDx required' }, 400);
      const diff = Math.abs(dx - c.truth.dx);
      return json(res, {
        ok: true, pass: diff <= TOL, truth_dx: c.truth.dx,
        submitted_dx: +dx.toFixed(2), diff: +diff.toFixed(2), tol: TOL,
        truth_sliderDx: +(c.truth.dx * (GEOM.trackW - GEOM.handleW) / GEOM.w).toFixed(2),
        meta: c.truth,
      });
    } catch (e) {
      return json(res, { ok: false, error: String(e && e.message || e) }, 400);
    }
  }

  if (p === '/specimens') {
    const mp = path.join(SPEC_DIR, 'manifest.json');
    if (!fs.existsSync(mp)) return json(res, { ok: false, error: 'no specimens' }, 404);
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    return fs.createReadStream(mp).pipe(res);
  }

  if (p.startsWith('/specimen/')) {
    const id = decodeURIComponent(p.slice('/specimen/'.length));
    const sp = path.join(SPEC_DIR, `spec_${id}.json`);
    if (!fs.existsSync(sp)) return json(res, { ok: false, error: 'not found' }, 404);
    const spec = JSON.parse(fs.readFileSync(sp, 'utf8'));
    const bmp = path.join(SPEC_DIR, spec.bitmap.file);
    if (u.query.meta) return json(res, spec);
    if (!fs.existsSync(bmp)) return json(res, { ok: false, error: 'bitmap missing' }, 404);
    res.writeHead(200, { 'Content-Type': 'image/png' });
    return fs.createReadStream(bmp).pipe(res);
  }

  return json(res, { ok: false, error: 'not found', paths: ['/', '/challenge', '/analyze', '/grade', '/specimens', '/specimen/:id'] }, 404);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[captcha-mock] http://127.0.0.1:${PORT}  geom=${JSON.stringify(GEOM)} tol=${TOL}px`);
  console.log('[captcha-mock] 端点: /  /challenge  /analyze  /grade  /specimens  /specimen/:id');
});
