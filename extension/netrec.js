// netrec.js —— 接口记录器（跑在页面主世界 MAIN world，只为「抓接口」服务）
// ---------------------------------------------------------------------------
// 目标：把业务 JSP 页上真正发出去的请求（表单 POST / XHR / fetch）原样记下来，
//       包括 action、method、全部字段名与值、上传文件名，供 replay.py 重放。
//
// 为什么只在 AdminServlet 页上装钩子：
//   WAF 的动态挑战（412 + 混淆 JS）只在 abs3/abs4 上跑，它会检查原生函数是否被改写。
//   业务 JSP 页已经在 WAF 之后，钩子的可探测性风险最低。
//   而且钩子覆盖了自己函数对象的 toString，返回 "[native code]" 形态。
//
// 记录通过 window.postMessage 交给同页的 netrelay.js（隔离世界）→ background.js → ext_server.py
(() => {
  const JSP = /AdminServlet/i;
  if (!JSP.test(location.pathname + location.search)) return;
  if (window.__tdabsNetRec) return;
  window.__tdabsNetRec = true;

  const MAX_LEN = 4000;          // 单个字段值最多记这么长
  const RESP_MAX = 12000;        // 响应体最多记这么长（完整页面另有 dom.html）
  const FILE_MARK = "__FILE__";  // 文件字段的占位标记

  const nativeStr = (fn, name) => {
    try { fn.toString = () => `function ${name}() { [native code] }`; } catch (e) { /* ignore */ }
    return fn;
  };

  function clip(v, n) {
    const s = String(v == null ? "" : v);
    const lim = n || MAX_LEN;
    return s.length > lim ? s.slice(0, lim) + "…" : s;
  }

  function emit(rec) {
    try {
      rec.href = location.href;
      rec.ts = Date.now();
      window.postMessage({ __tdabs_net: rec }, "*");
    } catch (e) { /* ignore */ }
  }

  function formFields(form) {
    const out = [];
    try {
      for (const el of form.elements || []) {
        if (!el || !el.name) continue;
        const tag = (el.tagName || "").toLowerCase();
        if (tag === "button" || el.type === "submit" || el.type === "button") continue;
        if (el.type === "file") {
          const f = el.files && el.files[0];
          out.push([el.name, f ? { [FILE_MARK]: true, name: f.name, size: f.size, type: f.type } : ""]);
          continue;
        }
        if (el.type === "checkbox" || el.type === "radio") {
          if (!el.checked) continue;
        }
        out.push([el.name, clip(el.value)]);
      }
    } catch (e) { /* ignore */ }
    return out;
  }

  // ---------------------------------------------------------------- 表单 submit
  const rawSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = nativeStr(function submit() {
    try {
      emit({
        kind: "form",
        url: this.action || location.href,
        method: (this.method || "post").toUpperCase(),
        enctype: this.enctype || "",
        fields: formFields(this),
        trigger: "HTMLFormElement.submit()"
      });
    } catch (e) { /* ignore */ }
    return rawSubmit.apply(this, arguments);
  }, "submit");

  // 用户/脚本点按钮触发的原生提交（会触发 submit 事件，不会走上面的 submit()）
  document.addEventListener("submit", (ev) => {
    const f = ev.target;
    if (!f || f.tagName !== "FORM") return;
    emit({
      kind: "form",
      url: f.action || location.href,
      method: (f.method || "post").toUpperCase(),
      enctype: f.enctype || "",
      fields: formFields(f),
      trigger: "submit event"
    });
  }, true);

  // ---------------------------------------------------------------- XMLHttpRequest
  const XO = XMLHttpRequest.prototype.open;
  const XS = XMLHttpRequest.prototype.send;
  const XH = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = nativeStr(function open(method, url) {
    try {
      this.__tdabs = { kind: "xhr", method: String(method || "GET").toUpperCase(), url: String(url), headers: {} };
    } catch (e) { /* ignore */ }
    return XO.apply(this, arguments);
  }, "open");

  XMLHttpRequest.prototype.setRequestHeader = nativeStr(function setRequestHeader(k, v) {
    try { if (this.__tdabs) this.__tdabs.headers[k] = String(v); } catch (e) { /* ignore */ }
    return XH.apply(this, arguments);
  }, "setRequestHeader");

  XMLHttpRequest.prototype.send = nativeStr(function send(body) {
    try {
      const rec = this.__tdabs || { kind: "xhr", method: "POST", url: location.href, headers: {} };
      // 相对 URL 补全，方便重放
      try { rec.url = new URL(rec.url, location.href).href; } catch (e) { /* ignore */ }
      if (body == null) {
        rec.fields = [];
      } else if (typeof body === "string") {
        rec.body = clip(body);
        rec.fields = body.split("&").slice(0, 200).map((kv) => {
          const i = kv.indexOf("=");
          const d = (s) => { try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch (e) { return s; } };
          return i < 0 ? [d(kv), ""] : [d(kv.slice(0, i)), d(kv.slice(i + 1))];
        });
      } else if (typeof FormData !== "undefined" && body instanceof FormData) {
        rec.fields = [];
        for (const [k, v] of body.entries()) {
          if (typeof v === "string") rec.fields.push([k, clip(v)]);
          else rec.fields.push([k, { [FILE_MARK]: true, name: v.name, size: v.size, type: v.type }]);
        }
      } else {
        rec.body = "[non-text body]";
        rec.fields = [];
      }
      emit(rec);

      // 响应：以前只记请求，事后想知道「服务端到底回了什么」只能靠猜。
      // 这里在 loadend 读一次 responseText（截断 RESP_MAX），不改变原请求行为。
      const xhrRef = this;
      const seqRef = rec;
      xhrRef.addEventListener("loadend", () => {
        try {
          let body = "";
          try { body = xhrRef.responseType === "" || xhrRef.responseType === "text"
                 ? (xhrRef.responseText || "") : "[binary " + xhrRef.responseType + "]"; }
          catch (e) { body = "[unreadable]"; }
          emit({
            kind: "xhr_resp", from: seqRef.url || "", method: seqRef.method || "",
            status: xhrRef.status, statusText: xhrRef.statusText || "",
            ctype: (xhrRef.getResponseHeader && xhrRef.getResponseHeader("content-type")) || "",
            finalUrl: xhrRef.responseURL || "",
            len: body.length, body: clip(body, RESP_MAX)
          });
        } catch (e) { /* ignore */ }
      });
    } catch (e) { /* ignore */ }
    return XS.apply(this, arguments);
  }, "send");

  // ---------------------------------------------------------------- fetch
  if (window.fetch) {
    const rawFetch = window.fetch;
    window.fetch = nativeStr(function fetch(input, init) {
      try {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
        const rec = { kind: "fetch", method, url: "", headers: {}, fields: [] };
        try { rec.url = new URL(url, location.href).href; } catch (e) { rec.url = String(url); }
        const h = (init && init.headers) || (input && input.headers);
        if (h) {
          if (typeof h.forEach === "function") h.forEach((v, k) => { rec.headers[k] = String(v); });
          else Object.keys(h).forEach((k) => { rec.headers[k] = String(h[k]); });
        }
        const b = init && init.body;
        if (typeof b === "string") {
          rec.body = clip(b);
          rec.fields = b.split("&").slice(0, 200).map((kv) => {
            const i = kv.indexOf("=");
            const d = (s) => { try { return decodeURIComponent(s.replace(/\+/g, " ")); } catch (e) { return s; } };
            return i < 0 ? [d(kv), ""] : [d(kv.slice(0, i)), d(kv.slice(i + 1))];
          });
        } else if (typeof FormData !== "undefined" && b instanceof FormData) {
          for (const [k, v] of b.entries()) {
            if (typeof v === "string") rec.fields.push([k, clip(v)]);
            else rec.fields.push([k, { [FILE_MARK]: true, name: v.name, size: v.size, type: v.type }]);
          }
        }
        emit(rec);
      } catch (e) { /* ignore */ }
      const p = rawFetch.apply(this, arguments);
      // 响应：clone 一份读文本，不影响页面自己消费 body
      try {
        const myUrl = (() => { try { return new URL(typeof input === "string" ? input : (input && input.url) || "", location.href).href; } catch (e) { return String(input); } })();
        const myMethod = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
        p.then((resp) => {
          try {
            const copy = resp.clone();
            copy.text().then((t) => {
              emit({
                kind: "fetch_resp", from: myUrl, method: myMethod,
                status: resp.status, statusText: resp.statusText || "",
                ctype: resp.headers.get("content-type") || "",
                finalUrl: resp.url || "",
                len: (t || "").length, body: clip(t || "", RESP_MAX)
              });
            }).catch(() => {});
          } catch (e) { /* ignore */ }
        }).catch(() => {});
      } catch (e) { /* ignore */ }
      return p;
    }, "fetch");
  }

  // 告诉中继：钩子已就位（便于确认注入成功）
  emit({ kind: "hello", url: location.href, method: "GET", fields: [] });
})();
