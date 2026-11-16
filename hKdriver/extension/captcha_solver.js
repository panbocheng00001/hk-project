// captcha_solver.js —— 验证码自动识别与求解（主文档 + 任意 iframe 都跑一份）
// ============================================================================
// 为什么单独成文件：
//   原来 content.js 只有主文档会解滑块（doWafCaptcha），iframe 分支只上报一句
//   「请手动完成」（2026-11-10 实战就是这么卡住的：验证码整个在 Queue-it iframe 里，
//   主文档看不到 canvas，脚本一声不响）。这里把「识别 + 求解」抽成通用件，
//   主文档和每个 iframe 都注入一份，谁看见谁解。
//
// 能力：
//   1) scan()      多表面盘点：canvas / 滑块手柄 / 关键词 / mtcaptcha / turnstile /
//                  hcaptcha / 图形码 / 点选类，每个表面都带坐标
//   2) computeDx() 纯 JS 图像差分求滑块位移（不依赖 Python 或 OpenCV）
//      —— 与 slider.py 同一套判据：前景阈值 → 连通域 → 填充密度分「实心/空心」→ 配对
//   3) solve()     按类型分派：滑块拖动 / 勾选框点击 / 上报取证
//   4) 拖动两条路：合成 PointerEvent（默认，无痕）→ 失败升级 chrome.debugger 的可信事件
//   5) 坐标换算：跨域 iframe 里拿不到父级 frameElement，用 postMessage 把手
//      把父级偏移传下来（__CAP_FRAME_OFFSET），可信输入才能落到正确屏幕位置
//
// 用法（content.js 里）：
//   const r = await window.__CAPSOLVER.solve();   // 返回 {type, ok, note, tries}
(() => {
  if (window.__CAPSOLVER) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const send = (msg) => new Promise((res) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) res({ error: chrome.runtime.lastError.message });
        else res(r || {});
      });
    } catch (e) { res({ error: String(e) }); }
  });

  // ---------------------------------------------------------------- 帧偏移
  // 跨域 iframe 的坐标要加上父级偏移才能被 chrome.debugger 的 Input 域命中。
  // 父级（同一套注入脚本）每秒把自己的 iframe 矩形广播进来。
  const frameOffset = { x: 0, y: 0, depth: 0 };
  let specSent = false;   // 每次做题只落一份样本
  window.addEventListener("message", (ev) => {
    const d = ev.data;
    if (d && d.__cap_frame_offset && typeof d.x === "number") {
      frameOffset.x = d.x;
      frameOffset.y = d.y;
      frameOffset.depth = d.depth || 0;
    }
  }, false);

  function broadcastToChildFrames() {
    try {
      const base = 0;   // 本帧视口原点
      const iframes = document.querySelectorAll("iframe, frame");
      for (const f of iframes) {
        if (!f.contentWindow) continue;
        try {
          const r = f.getBoundingClientRect();
          f.contentWindow.postMessage({
            __cap_frame_offset: true,
            x: frameOffset.x + r.x + base,
            y: frameOffset.y + r.y + base,
            depth: frameOffset.depth + 1
          }, "*");
        } catch (e) { /* ignore */ }
      }
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- 盘点
  // 2026-11-16 17:20 实测：挑战页 lang=en 时文案是「Adjust the slider until the two shapes line up.」，
  // 中文是「调整滑块，直到两个形状叠在一起」。原表两个都不含，导致 scan() 报 type=none、
  // solve() 第一轮就误判「已通过」，页面于是每 2 秒空转一次。
  const TEXT_SLIDER = /调整滑块|拖动滑块|拖曳滑块|滑动滑块|拖动下方|按住滑块|adjust\s+the\s+slider|drag\s*the\s*slider|slide\s*to\s*(verify|unlock)|swipe\s*to|two\s+shapes/i;
  const TEXT_SUBMIT = ["提交", "確定", "确定", "验证", "驗證", "submit", "verify", "confirm"];

  function visible(el) {
    if (!el) return false;
    try {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return false;
      const cs = getComputedStyle(el);
      return cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity || 1) > 0.05;
    } catch (e) { return false; }
  }

  function walk(root, out, depth) {
    // 递归进 shadow root：验证码组件常用 Shadow DOM 封装，querySelector 看不见里面
    if (!root || depth > 4) return out;
    let nodes = [];
    try { nodes = root.querySelectorAll("*"); } catch (e) { return out; }
    for (const el of nodes) {
      out.push(el);
      if (el.shadowRoot) walk(el.shadowRoot, out, depth + 1);
    }
    return out;
  }

  function canvases() {
    const list = [];
    for (const c of document.querySelectorAll("canvas")) {
      if (!visible(c)) continue;
      const r = c.getBoundingClientRect();
      if (r.width < 60 || r.height < 30) continue;
      list.push({ el: c, rect: r, bmpW: c.width, bmpH: c.height });
    }
    return list;
  }

  function pickHandle() {
    const sels = ["input[type=range]", ".slider-handle", "[class*=slider-handle]",
                  "[class*=handle]", "[class*=slider]", "[class*=drag]", "[id*=slider]",
                  "[id*=handle]", "[id*=drag]", "[id*=thumb]", "[class*=thumb]",
                  "[class*=btn-slide]", ".verify-move-block", "[class*=verify-btn]"];
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) {
        const r = el.getBoundingClientRect();
        if (r.width > 8 && r.height > 8 && r.width < 140 && visible(el)) return el;
      }
    }
    return null;
  }

  function bodyText() {
    try { return ((document.body && document.body.innerText) || "").slice(0, 5000); } catch (e) { return ""; }
  }

  function mtWidget() {
    // MTCaptcha 的 widget 可能嵌在 iframe 里，也可能直接是自定义元素
    for (const f of document.querySelectorAll("iframe[src]")) {
      if (/mtcaptcha/i.test(f.src || "")) {
        const r = f.getBoundingClientRect();
        const m = /[?&]sitekey=([^&]+)/i.exec(f.src || "");
        return { kind: "iframe", el: f, rect: r, sitekey: m ? decodeURIComponent(m[1]) : "", src: f.src.slice(0, 200) };
      }
    }
    for (const el of document.querySelectorAll("[class*=mtcap], [id*=mtcap], .mtcaptcha")) {
      if (visible(el)) return { kind: "inline", el, rect: el.getBoundingClientRect(), sitekey: "", src: "" };
    }
    return null;
  }

  function tokenLen() {
    const t = document.querySelector('input[name="captchaToken"], #captchaToken');
    return t ? (t.value || "").length : -1;
  }

  function scan() {
    const out = {
      type: "none", detail: [], url: location.href,
      inFrame: (() => { try { return window.top !== window; } catch (e) { return true; } })(),
      text: bodyText().slice(0, 400), tokenLen: tokenLen(), canvasN: 0
    };
    const cs = canvases();
    out.canvasN = cs.length;
    out.canvas = cs.length ? { w: cs[0].bmpW, h: cs[0].bmpH, css: [Math.round(cs[0].rect.width), Math.round(cs[0].rect.height)] } : null;

    // 2026-11-16 17:23 实测：华为 Quidditch 页的 body.innerText 几乎是空的
    // （整屏只有一个 canvas + 内联 <style>），文案匹配全部落空 → type=none。
    // 业务页（abs1/abs2/abs3 的 React 页）没有任何 canvas，所以「大 canvas」本身就是铁证。
    if (cs.length && cs[0].rect.width > 150 && cs[0].rect.height > 100) {
      out.type = "waf_slider";
      out.detail.push("滑块（大 canvas 兜底）");
      return out;
    }

    if (cs.length && TEXT_SLIDER.test(out.text)) {
      out.type = "waf_slider";
      out.detail.push("滑块（文案+canvas）");
      return out;
    }
    if (cs.length && /重合|align|overlap|实心|空心|solid|outline|shape/i.test(out.text)) {
      out.type = "waf_slider";
      out.detail.push("滑块（重合类图形）");
      return out;
    }
    // 只有 canvas + 手柄，也是滑块
    if (cs.length && pickHandle()) {
      out.type = "waf_slider";
      out.detail.push("滑块（canvas+手柄）");
      return out;
    }

    const mt = mtWidget();
    if (mt) {
      out.type = "mtcaptcha";
      out.detail.push("MTCaptcha " + mt.kind + (mt.sitekey ? " sitekey=" + mt.sitekey.slice(0, 12) + "…" : ""));
      return out;
    }

    // 其他常见：点选类 / 图形码 / 勾选框
    if (/请依次点击|请点击|點選|click\s+in\s+order|select\s+all/i.test(out.text) && cs.length) {
      out.type = "click_select";
      out.detail.push("点选类（需按序点击）");
      return out;
    }
    for (const f of document.querySelectorAll("iframe[src]")) {
      const s = f.src || "";
      if (/challenges\.cloudflare\.com/.test(s)) { out.type = "turnstile"; out.detail.push("Cloudflare Turnstile"); return out; }
      if (/hcaptcha\.com/.test(s)) { out.type = "hcaptcha"; out.detail.push("hCaptcha"); return out; }
      if (/recaptcha/.test(s) && /bframe/.test(s)) { out.type = "recaptcha_image"; out.detail.push("reCAPTCHA 图片挑战"); return out; }
      if (/recaptcha/.test(s)) { out.type = "recaptcha_checkbox"; out.detail.push("reCAPTCHA 勾选框"); return out; }
    }
    for (const img of document.querySelectorAll("img")) {
      const s = (img.getAttribute("src") || "") + " " + (img.id || "") + " " + (img.alt || "");
      const r = img.getBoundingClientRect();
      if (/captcha|verify|checkcode|validcode|yzm|authcode/i.test(s) && r.width > 30) {
        out.type = "image_code";
        out.detail.push("图形验证码图片");
        return out;
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- 图像差分
  // 在 canvas 位图里找「实心图形」与其「同类空心轮廓」的水平差。
  // 与 slider.py 判据一致，但纯 JS：不依赖 Python 进程，iframe 里也能算。
  function hueOf(rgb) {
    const r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (d < 0.02) return -1;                       // 近灰/白，无色调
    let h;
    if (mx === r) h = ((g - b) / d) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    return (h + 360) % 360;
  }

  function hueDist(a, b) {
    if (a < 0 || b < 0) return 0.15;               // 有一方无色调：按中性处理
    let d = Math.abs(a - b);
    if (d > 180) d = 360 - d;
    return d / 180;
  }

  function computeDx(canvasEl) {
    const w = canvasEl.width | 0, h = canvasEl.height | 0;
    if (!w || !h) return { ok: false, err: "canvas 尺寸为 0" };
    let data;
    try {
      const ctx = canvasEl.getContext("2d", { willReadFrequently: true });
      data = ctx.getImageData(0, 0, w, h).data;
    } catch (e) {
      return { ok: false, err: "getImageData 失败（canvas 被跨域污染）" };
    }

    // 多级阈值：华为这块画布是「深色网格 + 亮色图形」，网格线（低亮度细线）
    // 必须被排除，否则所有形状会被网格连成一坨，连通域直接废掉。
    // 级别从严到松：严格档排掉网格；形状颜色特殊（低饱和/低亮度）时才逐级放宽。
    const LEVELS = [
      (mx, sat) => (sat > 90 && mx > 90) || (mx > 190 && sat < 80),
      (mx, sat) => (sat > 70 && mx > 70) || (mx > 175 && sat < 85),
      (mx, sat) => (sat > 50 && mx > 55) || (mx > 160 && sat < 95)
    ];

    const buildMask = (test, hueCenter) => {
      const mask = new Uint8Array(w * h);
      for (let i = 0, p = 0; i < data.length; i += 4, p++) {
        const r = data[i], g = data[i + 1], b = data[i + 2];
        const mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
        const mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
        const sat = mx === 0 ? 0 : ((mx - mn) / mx) * 255;
        if (!test(mx, sat)) { mask[p] = 0; continue; }
        if (hueCenter != null && hueCenter >= 0) {
          const hh = hueOf([r, g, b]);
          if (hh < 0) { mask[p] = 0; continue; }          // 无色像素（网格/白线）一律剔除
          let d = Math.abs(hh - hueCenter);
          if (d > 180) d = 360 - d;
          mask[p] = d <= 16 ? 1 : 0;
        } else mask[p] = 1;
      }
      return mask;
    };

    let blobs = [];
    let usedLevel = -1;
    for (let li = 0; li < LEVELS.length; li++) {
      const found = components(buildMask(LEVELS[li], null), w, h, data);
      if (found.length >= 2) { blobs = found; usedLevel = li; break; }
      if (li === LEVELS.length - 1) blobs = found;
    }
    if (blobs.length < 2) return { ok: false, err: "前景块不足", blobs: blobs.length };

    const pairBest = (list) => {
      const solid = list.filter((b) => b.density >= 0.45);
      const hollow = list.filter((b) => b.density < 0.45);
      if (!solid.length || !hollow.length) {
        const byDen = list.slice().sort((a, b) => b.density - a.density);
        if (!solid.length) solid.push(byDen[0]);
        if (!hollow.length) hollow.push(byDen[byDen.length - 1]);
      }
      let bst = null;
      for (const s of solid) {
        for (const t of hollow) {
          if (s === t) continue;
          const dw = Math.abs(s.w - t.w) / Math.max(1, Math.max(s.w, t.w));
          const dh = Math.abs(s.h - t.h) / Math.max(1, Math.max(s.h, t.h));
          if (dw > 0.4 || dh > 0.4) continue;
          const dy = Math.abs(s.cy - t.cy) / Math.max(1, h);
          // 形状相似度：实心剖面 vs 空心「填满后」的剖面。这是最强的一项——
          // 干扰图形常常外接框大小也接近，只有形状对不上。
          const shape = t.profFilled ? profDist(s.prof, t.profFilled) : 0.35;
          const score = dw + dh + dy * 0.6 + hueDist(s.hue, t.hue) * 0.6 + shape * 1.8;
          if (!bst || score < bst.score) bst = { score, s, t, shape };
        }
      }
      return bst;
    };

    let best = pairBest(blobs);
    let refined = null;

    // 颜色细化：干扰图形经常正压在同色目标上，一合块就把外接框带偏。
    // 人眼配对靠的是「同形状 + 同颜色」，所以在主色簇里重算一次连通域，
    // 把不同色的干扰像素直接剔掉，外接框就回正了。
    const seedHues = [...new Set(
      blobs.filter((b) => b.density >= 0.45 && b.hue >= 0)
           .map((b) => Math.round(b.hue / 12) * 12)
    )].slice(0, 4);
    for (const hc of seedHues) {
      const sub = components(buildMask(LEVELS[Math.max(0, usedLevel)], hc), w, h, data);
      if (sub.length < 2) continue;
      const cand = pairBest(sub);
      if (cand && (!best || cand.score < best.score - 0.02)) {
        best = cand;
        refined = hc;
      }
    }

    if (!best) return { ok: false, err: "没有可配对的实心/空心" };
    return {
      ok: true,
      dx: best.t.cx - best.s.cx,
      current_x: best.s.cx, target_x: best.t.cx,
      pairs: blobs.length, level: usedLevel, refinedHue: refined,
      hue: [Math.round(best.s.hue), Math.round(best.t.hue)],
      solidFill: +best.s.density.toFixed(2), hollowFill: +best.t.density.toFixed(2),
      bmpW: w, bmpH: h
    };
  }

  // 8×8 归一化占用剖面：把块的外接框切成 64 格，每格算前景占比。
  // 用途：实心 vs 空心「填满后的剖面」形状距离 —— 这才是人眼判「同一个形状」的依据，
  // 光看外接框大小会把「圆 vs 六边形」判成一对。
  function profile(mask, w, h, x0, y0, bw, bh, extraFill) {
    const prof = new Array(64).fill(0);
    for (let gy = 0; gy < 8; gy++) {
      const ya = y0 + Math.floor(bh * gy / 8), yb = y0 + Math.max(1, Math.floor(bh * (gy + 1) / 8));
      for (let gx = 0; gx < 8; gx++) {
        const xa = x0 + Math.floor(bw * gx / 8), xb = x0 + Math.max(1, Math.floor(bw * (gx + 1) / 8));
        let on = 0, tot = 0;
        for (let y = ya; y < yb && y < h; y++) {
          for (let x = xa; x < xb && x < w; x++) {
            tot++;
            if (mask[y * w + x] || (extraFill && extraFill[y * w + x])) on++;
          }
        }
        prof[gy * 8 + gx] = tot ? on / tot : 0;
      }
    }
    return prof;
  }

  function profDist(a, b) {
    let d = 0;
    for (let i = 0; i < 64; i++) d += Math.abs(a[i] - b[i]);
    return d / 64;
  }

  // 把空心轮廓「填满」：从外接框边界往里做 BFS 找外部，剩下的空腔就是内部
  function fillHollow(mask, w, h, x0, y0, bw, bh) {
    const out = new Uint8Array(bw * bh);
    const q = [];
    const idx = (x, y) => (y - y0) * bw + (x - x0);
    for (let x = x0; x < x0 + bw; x++) {
      for (const y of [y0, y0 + bh - 1]) {
        if (y >= 0 && y < h && x >= 0 && x < w && !mask[y * w + x] && !out[idx(x, y)]) {
          out[idx(x, y)] = 1; q.push([x, y]);
        }
      }
    }
    for (let y = y0; y < y0 + bh; y++) {
      for (const x of [x0, x0 + bw - 1]) {
        if (y >= 0 && y < h && x >= 0 && x < w && !mask[y * w + x] && !out[idx(x, y)]) {
          out[idx(x, y)] = 1; q.push([x, y]);
        }
      }
    }
    while (q.length) {
      const [x, y] = q.pop();
      const nb = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
      for (const [nx, ny] of nb) {
        if (nx < x0 || ny < y0 || nx >= x0 + bw || ny >= y0 + bh) continue;
        if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
        if (mask[ny * w + nx] || out[idx(nx, ny)]) continue;
        out[idx(nx, ny)] = 1;
        q.push([nx, ny]);
      }
    }
    // 没被外部 BFS 触到的空像素 = 内部
    const filled = new Uint8Array(w * h);
    for (let y = y0; y < y0 + bh; y++) {
      for (let x = x0; x < x0 + bw; x++) {
        const inside = !mask[y * w + x] && !out[idx(x, y)];
        if (mask[y * w + x] || inside) filled[y * w + x] = 1;
      }
    }
    return filled;
  }

  function components(mask, w, h, data) {
    const seen = new Uint8Array(w * h);
    const out = [];
    const stack = new Int32Array(w * h);
    for (let p0 = 0; p0 < mask.length; p0++) {
      if (!mask[p0] || seen[p0]) continue;
      let sp = 0;
      stack[sp++] = p0;
      seen[p0] = 1;
      let minX = w, maxX = -1, minY = h, maxY = -1, count = 0;
      let sr = 0, sg = 0, sb = 0;
      while (sp > 0) {
        const p = stack[--sp];
        const x = p % w, y = (p - x) / w;
        count++;
        if (data) { sr += data[p * 4]; sg += data[p * 4 + 1]; sb += data[p * 4 + 2]; }
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        if (x > 0 && mask[p - 1] && !seen[p - 1]) { seen[p - 1] = 1; stack[sp++] = p - 1; }
        if (x < w - 1 && mask[p + 1] && !seen[p + 1]) { seen[p + 1] = 1; stack[sp++] = p + 1; }
        if (y > 0 && mask[p - w] && !seen[p - w]) { seen[p - w] = 1; stack[sp++] = p - w; }
        if (y < h - 1 && mask[p + w] && !seen[p + w]) { seen[p + w] = 1; stack[sp++] = p + w; }
      }
      const bw = maxX - minX + 1, bh = maxY - minY + 1;
      if (count < 120 || bw < 12 || bh < 12 || count > w * h * 0.25) continue;
      const rgb = count ? [sr / count, sg / count, sb / count] : [0, 0, 0];
      const dens = count / (bw * bh);
      const blob = {
        cx: minX + bw / 2, cy: minY + bh / 2, w: bw, h: bh, count,
        density: dens, rgb, hue: hueOf(rgb),
        x0: minX, y0: minY,
        prof: profile(mask, w, h, minX, minY, bw, bh, null)
      };
      if (dens < 0.45) {
        // 空心：填满后再做剖面，才能和实心比形状
        const filled = fillHollow(mask, w, h, minX, minY, bw, bh);
        blob.profFilled = profile(mask, w, h, minX, minY, bw, bh, filled);
      }
      out.push(blob);
    }
    return out;
  }

  // ---------------------------------------------------------------- 拖动
  function fireEv(el, type, x, y, buttons) {
    const opt = {
      bubbles: true, cancelable: true, composed: true,
      clientX: x, clientY: y, pointerId: 1, isPrimary: true,
      button: 0, buttons: buttons || 0, pointerType: "mouse"
    };
    try { el.dispatchEvent(new PointerEvent(type, opt)); } catch (e) { /* ignore */ }
    const m = type === "pointerdown" ? "mousedown"
      : type === "pointerup" ? "mouseup" : "mousemove";
    try {
      el.dispatchEvent(new MouseEvent(m, {
        bubbles: true, cancelable: true, composed: true,
        clientX: x, clientY: y, button: 0, buttons: buttons || 0
      }));
    } catch (e) { /* ignore */ }
  }

  function track(dx, steps) {
    // 加速—匀速—减速 + 微抖 + 偶发回拉，跟真人手感一致
    steps = steps || Math.max(26, Math.min(96, Math.round(rand(560, 1150) / 15)));
    const seq = [];
    let moved = 0;
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const k = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const pos = dx * k + rand(-1.2, 1.2) * (1 - t * 0.6);
      seq.push(pos - moved);
      moved = pos;
    }
    if (Math.abs(dx) > 20 && Math.random() < 0.7) {
      const over = rand(1, 3) * (dx > 0 ? 1 : -1);
      seq.push(over, -over);
    }
    return seq;
  }

  // 滑块起点：有 DOM 手柄就用手柄中心；没有手柄（华为 Quidditch 把球画在 canvas 里）
  // 就用 canvas 内的固定比例点 —— 2026-11-16 17:19 实测（canvas 420×428）：
  // 球心 ≈ canvas 左 + 7.6% 宽、上 + 80% 高；轨道左端到右端共 ~85% 宽。
  function handleCenter(handle) {
    const r = handle.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, rect: r, canvasOnly: false };
  }

  function canvasStart(c) {
    const r = c.getBoundingClientRect();
    // 2026-11-16 17:24 实测位图（420×428）：球心在 (30, 358)，轨道右端 ~390。
    // 先按比例取；位图里能认出球就直接用球心，认不出再退回比例。
    const pt = findBall(c);
    if (pt) {
      return { x: r.left + pt.x * (r.width / c.width), y: r.top + pt.y * (r.height / c.height), rect: r, canvasOnly: true };
    }
    return {
      x: r.left + Math.max(20, Math.round(r.width * 0.071)),
      y: r.top + r.height * 0.836,
      rect: r, canvasOnly: true
    };
  }

  // 在 canvas 位图里找底部那颗蓝色圆球（Quidditch 的拖动把手没有 DOM 节点）
  // 2026-11-16 17:5x 修：原来只在 x < 25% 范围内找，手柄一拖到右边（实测到 131px）
  // 就返回 null，canvasStart 于是退回「固定最左」的兜底坐标 —— 补拖全部拖在错位置，
  // 表现就是「每轮都在动、永远不收敛」。改成**全宽搜索 + 取蓝色最右端当手柄右缘**。
  function findBall(c) {
    let data;
    try {
      const ctx = c.getContext("2d", { willReadFrequently: true });
      data = ctx.getImageData(0, 0, c.width, c.height).data;
    } catch (e) { return null; }
    const w = c.width, h = c.height;
    const y0 = Math.floor(h * 0.76);
    const y1 = Math.floor(h * 0.86);   // 只扫滑轨那一条：下方蓝色 Submit 不能被当手柄
    const isBlue = (x, y) => {
      const i = (y * w + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      // 华为这颗球是 #2260e8 一族：蓝显著高于红绿
      return b > 120 && b - r > 60 && b - g > 40;
    };
    let maxX = -1;
    for (let y = y0; y < y1; y++) {
      for (let x = 0; x < w; x++) {
        if (isBlue(x, y) && x > maxX) maxX = x;
      }
    }
    if (maxX < 0) return null;
    let sumY = 0, n = 0;
    for (let y = y0; y < y1; y++) {
      for (let x = Math.max(0, maxX - 12); x <= maxX; x++) {
        if (isBlue(x, y)) { sumY += y; n++; }
      }
    }
    if (n < 20) return null;
    return { x: Math.max(0, maxX - 11), y: sumY / n, n };   // 直径 22 → 圆心 = 右缘 - 11
  }

  // Quidditch 的题目图形画在 canvas 的 CSS background-image 里（不在 2D 内容层），
  // 所以 getImageData(canvas) 只看到文字/球/按钮。把背景图取出来单独算 dx。
  function bgDataUrl(c) {
    try {
      const css = getComputedStyle(c).backgroundImage || "";
      const m = /url\(["']?(data:image\/[a-z+]+;base64,[^"')]+)["']?\)/i.exec(css);
      return m ? m[1] : "";
    } catch (e) { return ""; }
  }

  async function dxFromBackground(c) {
    const url = bgDataUrl(c);
    if (!url) return { ok: false, err: "无背景图" };
    const im = await new Promise((res) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => res(null);
      i.src = url;
    });
    if (!im) return { ok: false, err: "背景图解码失败" };
    const off = document.createElement("canvas");
    off.width = im.naturalWidth || im.width;
    off.height = im.naturalHeight || im.height;
    try {
      off.getContext("2d").drawImage(im, 0, 0);
    } catch (e) { return { ok: false, err: "背景图绘制失败" }; }
    const r = computeDx(off);
    r.bgW = off.width; r.bgH = off.height; r.source = "bg";
    return r;
  }

  async function dragSynthetic(handle, dx, el) {
    const target = el || handle.el || document;
    const x0 = handle.x, y0 = handle.y;
    const dir = dx >= 0 ? 1 : -1;
    const fromX = x0 - dir * rand(90, 240), fromY = y0 + rand(-45, 45);

    await sleep(rand(150, 380));
    for (let i = 1; i <= 5; i++) {
      const t = i / 5, e = t * t * (3 - 2 * t);
      fireEv(target, "pointermove", fromX + (x0 - fromX) * e, fromY + (y0 - fromY) * e, 0);
      await sleep(rand(11, 26));
    }
    await sleep(rand(200, 560));
    fireEv(target, "pointerdown", x0, y0, 1);
    await sleep(rand(90, 280));

    let cur = x0;
    for (const step of track(dx)) {
      cur += step;
      fireEv(target, "pointermove", cur, y0 + rand(-1.5, 1.5), 1);
      await sleep(rand(9, 26));
    }
    fireEv(target, "pointermove", x0 + dx, y0, 1);
    await sleep(rand(150, 430));
    fireEv(target, "pointerup", x0 + dx, y0, 0);
    return true;
  }

  async function dragTrustedTouch(handle, dx) {
    // 可信 touch 拖动：Quidditch 的 canvas 带 touch-action:none，touch 路径最像真人。
    const x0 = frameOffset.x + handle.x;
    const y0 = frameOffset.y + handle.y;
    const pts = [];
    let cur = 0;
    for (const step of track(dx)) { cur += step; pts.push([x0 + cur, y0 + rand(-0.6, 0.6)]); }
    const r2 = await send({
      type: "trustedTouchDrag",
      data: { from: [x0, y0], to: [x0 + dx, y0], points: pts, holdMs: Math.round(rand(150, 400)) }
    });
    return !!(r2 && r2.ok);
  }

  async function dragTrusted(handle, dx) {
    // 走 chrome.debugger 的 Input 域：事件 isTrusted=true。
    // 代价：标签页顶部会出一条「正在调试」提示条（test_infobar.py 验过样子）。
    // 坐标直接用 content 侧算好的页面坐标 —— 对 canvas 内的球同样有效（没有 DOM 手柄也能拖）。
    const x0 = frameOffset.x + handle.x;
    const y0 = frameOffset.y + handle.y;
    const pts = [];
    let cur = 0;
    for (const step of track(dx)) { cur += step; pts.push([x0 + cur, y0 + rand(-1.5, 1.5)]); }
    const r2 = await send({
      type: "trustedDrag",
      data: { from: [x0, y0], to: [x0 + dx, y0], points: pts, holdMs: Math.round(rand(150, 430)) }
    });
    return !!(r2 && r2.ok);
  }

  // ---------------------------------------------------------------- 提交（canvas 内按钮）
  // 华为 Quidditch 的 Submit 是画在 canvas 里的，DOM 里只有「Try another verification」(#swap)。
  // 实测 420×428 的 canvas：Submit 中心 (210, 402) → 比例 (0.5, 0.945)。
  function submitPoint(c) {
    const r = c.getBoundingClientRect();
    return { x: r.left + r.width * 0.5, y: r.top + r.height * 0.945, rect: r };
  }

  async function clickTrusted(x, y) {
    const px = frameOffset.x + x;
    const py = frameOffset.y + y;
    const r = await send({
      type: "trustedDrag",
      data: { from: [px, py], to: [px, py], points: [[px, py]],
              holdMs: Math.round(rand(60, 120)) }
    });
    return !!(r && r.ok);
  }

  function clickSyntheticAt(c, px, py) {
    try {
      const el = document.elementFromPoint(px, py) || c;
      const base = { bubbles: true, cancelable: true, clientX: px, clientY: py,
                     view: window, button: 0 };
      const ev = (t, Ctor, extra) => {
        try { el.dispatchEvent(new Ctor(t, Object.assign({}, base, extra || {}))); }
        catch (e) { /* ignore */ }
      };
      ev("pointerover", PointerEvent, { pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 0 });
      ev("pointerdown", PointerEvent, { pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 1 });
      ev("mousedown", MouseEvent, { buttons: 1 });
      ev("pointerup", PointerEvent, { pointerId: 1, pointerType: "mouse", isPrimary: true, buttons: 0 });
      ev("mouseup", MouseEvent, { buttons: 0 });
      ev("click", MouseEvent, { buttons: 0 });
      return true;
    } catch (e) { return false; }
  }

  // 量一次「实心还差多少到空心」：本地背景图 → 本地 2D 层 → 服务端跨层
  async function measureDx(c, canvasOnly) {
    let loc = { ok: false };
    let src = "";
    if (!canvasOnly) {
      loc = await dxFromBackground(c); src = "js-bg";
      if (!loc.ok) { loc = computeDx(c); src = "js"; }
    }
    if (!loc.ok) {
      let dataUrl = "";
      try { dataUrl = c.toDataURL("image/png"); } catch (e) { dataUrl = ""; }
      if (dataUrl) {
        const rr = c.getBoundingClientRect();
        const resp = await send({
          type: "captcha",
          data: {
            dataUrl, bmpW: c.width, bmpH: c.height,
            cssW: Math.round(rr.width), cssH: Math.round(rr.height),
            dpr: window.devicePixelRatio, inFrame: window.top !== window,
            // Quidditch 的题目图形在 CSS 背景层：一起发过去，服务端做跨层配对
            bgUrl: bgDataUrl(c),
            url: location.href
          }
        });
        if (resp && resp.ok && typeof resp.dx === "number") {
          loc = { ok: true, dx: resp.dx, bmpW: c.width, bgW: resp.bmpW || c.width };
          src = "python";
        }
      }
    }
    if (loc.ok) loc.src = src;
    return loc;
  }

  // ---------------------------------------------------------------- 求解
  async function solveSlider(maxTries) {
    maxTries = maxTries || 3;
    const tries = [];
    for (let i = 0; i < maxTries; i++) {
      const cs = canvases();
      if (!cs.length) return { ok: false, type: "waf_slider", note: "canvas 消失", tries };
      const c = cs[0].el;
      const handle = pickHandle();
      // 2026-11-16 17:19 实测：华为 Quidditch 的球画在 canvas 里，DOM 里没有手柄。
      // 有手柄用中心；没有就按 canvas 比例取起点，照样拖。
      const pt = handle ? handleCenter(handle) : canvasStart(c);

      // ① 落样本（只落一次）：位图 + 几何 + DOM，供事后复盘与复刻服务回归
      if (!specSent) {
        specSent = true;
        try { send({ type: "captcha_spec", data: specimen(c, handle || c) }); } catch (e) { /* ignore */ }
      }

      // ② 算 dx（Quidditch 的实心在 2D 层、空心在 CSS 背景层 → 服务端跨层配对）
      const loc = await measureDx(c, pt.canvasOnly);
      const src = loc.src || "";
      if (!loc.ok) { tries.push("求 dx 失败：" + (loc.err || "?")); await sleep(rand(1500, 2600)); continue; }

      // ② 位图像素 → 屏幕拖动像素
      //    · 有 DOM 手柄：位图宽 ↔ 手柄行程成正比（沿用原口径）
      //    · 只有 canvas（Quidditch）：**图形位移 ≠ 手柄位移**
      //      2026-11-16 17:29 实测（同一道题拖前/拖后对账）：
      //        手柄 27.5→94.5（+67）图形 160→180（+20）
      //        手柄 94.5→131.5（+37）图形 180→193（+13）
      //        手柄 +104 ⇒ 图形 +33　→ 行程比 ≈ 0.317
      //      所以要消除 dx，手柄必须走 dx / 0.317 ≈ 3.15×dx。
      //      按 1:1 拖的后果就是「每轮只补 31.7%，永远差一点」。
      const TRAVEL_RATIO = 0.317;
      const bmpW = c.width || Math.max(1, Math.round(pt.rect.width));
      let sliderDx;
      if (pt.canvasOnly) {
        const cssW = pt.rect.width || bmpW;
        // dx 是在「题目图形」坐标系里量的：背景图宽 bgW → CSS 宽 cssW
        const refW = loc.bgW || bmpW;
        sliderDx = (loc.dx * (cssW / refW)) / TRAVEL_RATIO;
      } else {
        const hr = handle.getBoundingClientRect();
        const parent = handle.parentElement;
        const trackW = parent ? parent.getBoundingClientRect().width : hr.width;
        const range = Math.max(10, trackW - hr.width);
        sliderDx = loc.dx * (range / bmpW);
      }
      // 手柄行程上限：滑轨约 369px，别拖出轨道（超出会被 widget 夹紧，等于白拖）
      const maxTravel = pt.canvasOnly ? Math.max(40, (pt.rect.width || 420) - 60) : 1e9;
      if (Math.abs(sliderDx) > maxTravel) sliderDx = Math.sign(sliderDx) * maxTravel;
      if (i === 1) sliderDx *= 1.05;
      if (i === 2) sliderDx *= 0.95;
      if (Math.abs(sliderDx) < 4) sliderDx = sliderDx >= 0 ? 4 : -4;   // 防"没动"被判异常

      // ③ 拖动：canvas-only（Quidditch）优先可信 touch，其次可信 mouse，最后合成事件
      const preferTrusted = (i >= 1) && localStorage.getItem("tdabs_trusted_input") !== "0";
      let how = "synthetic";
      if (pt.canvasOnly) {
        if (await dragTrusted(pt, sliderDx)) how = "debugger";        // 桌面场景先 mouse（21:2x 实测 touch 返回 ok 但零位移）
        else if (await dragTrustedTouch(pt, sliderDx)) how = "touch";
        else await dragSynthetic(pt, sliderDx, c);
      } else if (preferTrusted) {
        const okT = await dragTrusted(pt, sliderDx);
        if (okT) how = "debugger";
        else await dragSynthetic(pt, sliderDx, c);
      } else {
        await dragSynthetic(pt, sliderDx, c);
      }
      tries.push(`${src}${pt.canvasOnly ? "(canvas)" : ""} dx=${loc.dx.toFixed(1)} → ${sliderDx.toFixed(1)}px(${how})`);

      // ③.5 校准补拖（Quidditch 专用，带**方向自学习**）：
      //   2026-11-16 实测两次得到相反的相关性（+0.317 与 -0.15 各一次），所以不假设方向：
      //   每拖一次量一次 |dx|，变大就说明拖反了，立刻翻转符号；同时用实测位移更新行程比。
      if (pt.canvasOnly) {
        let movedX = sliderDx;
        let dir = 1;                       // 1 = 按 dx 正负拖；-1 = 反着拖
        let prevAbs = Math.abs(loc.dx);
        let ratio = TRAVEL_RATIO;
        for (let k = 0; k < 4; k++) {
          await sleep(rand(500, 900));
          const again = await measureDx(c, true);
          if (!again.ok) break;
          const remainGraph = again.dx * ((pt.rect.width || c.width) / (again.bgW || c.width));
          const absNow = Math.abs(remainGraph);
          tries.push(`校准${k + 1} 图形还差 ${remainGraph.toFixed(1)}px（|dx| ${prevAbs.toFixed(1)}→${absNow.toFixed(1)}）`);
          if (absNow < 6) break;                            // 已经重合
          if (absNow > prevAbs + 3 && k > 0) {              // 变大 → 方向反了
            dir = -dir;
            tries.push(`方向翻转 → ${dir}`);
          }
          // 用实测效率修正行程比（拖了 movedX 只让 dx 变了这么多）
          if (k > 0 && Math.abs(movedX) > 12) {
            const eff = Math.abs((prevAbs - absNow) / movedX);
            if (eff > 0.03 && eff < 1.2) ratio = eff;
          }
          prevAbs = absNow;
          const remainPx = dir * (remainGraph / Math.max(0.05, ratio));
          const pt2 = { x: pt.x + movedX, y: pt.y, rect: pt.rect, canvasOnly: true };
          if (!(await dragTrustedTouch(pt2, remainPx))) await dragTrusted(pt2, remainPx);
          movedX += remainPx;
        }
      }

      // ④ 提交
      //   ⚠ 2026-11-16 17:5x 发现的大坑：DOM 里那个 `<button id="swap" class="submit">`
      //     文案是「Try another verification」——它是**换一题**，不是提交！
      //     原来的 `document.querySelector("#swap, button.submit")` 每轮都点它，
      //     于是「拖到接近 → 换新题 → dx 回到初始值」，表现就是永远差一点、永远过不了。
      //   真正的 Submit 画在 canvas 内部（实测 420×428 里中心在 (210, 402) → 比例 0.5 / 0.945），
      //   DOM 里没有对应节点，必须在 canvas 坐标上派发点击。
      await sleep(rand(500, 900));
      const sp = submitPoint(c);
      const okClick = await clickTrusted(sp.x, sp.y);
      if (!okClick) clickSyntheticAt(c, sp.x, sp.y);
      tries.push(`提交@(${Math.round(sp.x)},${Math.round(sp.y)})${okClick ? "可信" : "合成"}`);
      await sleep(rand(900, 1500));

      await sleep(rand(1000, 1800));
      const after = scan();
      const canvasGone = canvases().length === 0;
      if (after.type !== "waf_slider" || canvasGone) return { ok: true, type: "waf_slider", how, tries };
      await sleep(rand(1200, 2200));
    }
    return { ok: false, type: "waf_slider", note: "多次尝试未过", tries };
  }

  function clickableByText(labels) {
    const want = labels.map((s) => s.toLowerCase());
    for (const el of document.querySelectorAll("button, input[type=button], input[type=submit], a, [role=button]")) {
      const t = ((el.innerText || el.value || "") + "").trim().toLowerCase();
      if (!t) continue;
      if (want.some((w) => t === w || t.includes(w)) && visible(el)) return el;
    }
    return null;
  }

  async function solveMt() {
    // 在 MTCaptcha 自己的 iframe 里时，直接点它的勾选框；在主文档里则只上报 sitekey
    const tries = [];
    const boxes = [
      ...document.querySelectorAll('input[type=checkbox], [role=checkbox], .mtcap-checkbox, ' +
                                  '#mtcap-checkbox, .mtCapCheckbox, [class*=checkbox]')
    ].filter(visible);
    for (const b of boxes) {
      try { b.click(); tries.push("点击勾选框"); } catch (e) { /* ignore */ }
      await sleep(rand(1200, 2200));
      if (!document.querySelector('.mtcap-challenge, [class*=challenge], [class*=captcha-img]')) {
        return { ok: true, type: "mtcaptcha", note: "勾选框已点，等待无感验证", tries };
      }
      tries.push("出现图片挑战，需打码平台");
      return { ok: false, type: "mtcaptcha", note: "图片挑战", tries };
    }
    const btn = clickableByText(["verify", "验证", "驗證", "i am not a robot", "我不是机器人"]);
    if (btn) {
      btn.click();
      tries.push("点击验证按钮");
      await sleep(rand(1500, 2500));
      return { ok: tokenLen() > 10, type: "mtcaptcha", note: "已点击验证", tries };
    }
    return { ok: false, type: "mtcaptcha", note: "没找到可点的控件", tries };
  }

  async function solve(opts) {
    opts = opts || {};
    broadcastToChildFrames();
    const s = scan();
    if (s.type === "none") return { ok: true, type: "none", note: "没有验证码" };
    if (s.type === "waf_slider") return solveSlider(opts.tries || 3);
    if (s.type === "mtcaptcha") return solveMt();
    if (s.type === "recaptcha_checkbox" || s.type === "turnstile" || s.type === "hcaptcha") {
      const btn = clickableByText(["verify", "验证"]);
      if (btn) { btn.click(); return { ok: false, type: s.type, note: "已点验证，未见结果" }; }
      return { ok: false, type: s.type, note: "需在 iframe 内点击（该帧会自行处理）" };
    }
    return { ok: false, type: s.type, note: "该形态未内置求解（" + s.detail.join("；") + "）" };
  }

  // 把「这次遭遇」完整压成一份样本 JSON：位图 + 几何 + DOM + 环境 + 判定。
  // 事后既能复盘算法，也能直接喂给 Node 复刻服务当回归用例。
  function specimen(canvasEl, handle) {
    const sc = scan();
    const c = canvasEl || (canvases()[0] || {}).el || null;
    const rect = c ? c.getBoundingClientRect() : null;
    const hr = handle ? handle.getBoundingClientRect() : null;
    const pr = handle && handle.parentElement ? handle.parentElement.getBoundingClientRect() : null;
    let dataUrl = "";
    try { dataUrl = c ? c.toDataURL("image/png") : ""; } catch (e) { dataUrl = ""; }
    let dom = "";
    try {
      const host = c ? (c.closest("div") || c.parentElement || c) : document.body;
      dom = (host.outerHTML || "").slice(0, 200000);
    } catch (e) { dom = ""; }
    return {
      schema: "captcha-specimen/1",
      type: sc.type,
      url: location.href,
      at: Date.now(),
      env: {
        ua: navigator.userAgent,
        locale: navigator.language,
        timezone: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return ""; } })(),
        dpr: window.devicePixelRatio,
        in_frame: sc.inFrame,
        platform: navigator.platform || ""
      },
      geometry: {
        bmp_w: c ? c.width : null,
        bmp_h: c ? c.height : null,
        css_w: rect ? Math.round(rect.width) : null,
        css_h: rect ? Math.round(rect.height) : null,
        track_w: pr ? Math.round(pr.width) : null,
        handle_w: hr ? Math.round(hr.width) : null,
        canvas_rect: rect ? { x: Math.round(rect.x), y: Math.round(rect.y) } : null
      },
      dataUrl: dataUrl,
      dom: dom,
      scan: sc
    };
  }

  window.__CAPSOLVER = {
    scan, computeDx, solve, solveSlider, solveMt, specimen,
    canvases, pickHandle, tokenLen,
    frameOffset, broadcastToChildFrames,
    _track: track
  };
})();

