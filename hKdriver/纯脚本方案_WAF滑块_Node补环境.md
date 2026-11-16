# 纯脚本过 WAF 形状滑块（A 路线）· 完整方案

> 目标：**不启动浏览器**，用 Node 把华为云 WAF 的挑战脚本跑起来，直接算出答案并提交。
> 依据：2026-11-16 全天实测 + 已归档的 `waf_widget_8qcykgusbcwc.5057567.js`（304 KB）。
> 配套骨架：`waf_node/`（`harness.js` / `shims.js` / `probe.js`）。

---

## 0. 一句话

不是「模拟鼠标去拖」，而是 **hook 掉 widget 自己的绘制和提交**：
绘制给了什么形状 → 直接得到矢量坐标（零识图误差）；提交要什么参数 → 按同一格式自己发。
拖动、找手柄、点 Submit、被换题这些环节**全部消失**。

---

## 1. 为什么这条路能走通（实证，不是推测）

| 实证 | 结果 |
|---|---|
| widget JS 归档 | `runs/captcha_specimens/waf20261116/waf_widget_8qcykgusbcwc.5057567.js`，304 473 B |
| Node 能否执行 | **能**，`new Function(src)()` 可进入 |
| 第一次报错 | `Cannot read properties of undefined (reading 'location')` → 缺 `top` / `parent` |
| 补 `top/parent/frames` 后 | 摸到 `document.createElement` / `appendChild` / `removeChild` / `performance.markResourceTiming` / `document.documentMode` |
| 再下一步 | `Cannot set properties of undefined (setting '_$fT')` → 依赖挑战页**内联引导脚本**先铺全局 |
| 之后 | 进程不退出 → 挂了定时器（心跳/防调试） |

结论：

1. 它要的是 **DOM + canvas + timer**，属于「可补」的范畴；
2. 目前**没有出现** `navigator.webdriver` / canvas 指纹比对的硬报错（304 KB 里明文搜 `webdriver`、`toDataURL`、`webgl` 均为 0 —— 字符串被混淆表加密，不能据此断定没有，但至少不是第一道门）；
3. `_$fT` 报错说明**必须连挑战页内联段一起跑**，单跑外部 widget 不行。

---

## 2. 架构（五层）

```
┌─ L1 会话 ─────────────────────────────────────────────┐
│ curl_cffi (impersonate=chrome) 或 Node undici + 手工 TLS │
│ 职责：保 TLS 指纹、维护 cookie jar（HWWAFSESID/HIK0…）  │
└───────────────┬───────────────────────────────────────┘
                │ GET https://abs3.td.gov.hk/?lang=sc
                ▼
┌─ L2 挑战页 ───────────────────────────────────────────┐
│ 412 → HTML（内联引导脚本 + 外链 widget JS 地址）        │
│ 产出：{ inline_bootstrap, widget_url, cookies }        │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ L3 执行环境（Node + jsdom + canvas） ─────────────────┐
│ 顺序：eval(inline_bootstrap) → eval(widgetSrc)         │
│ 补齐 30~60 个 API（见 §5），报错驱动迭代               │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ L4 Hook ─────────────────────────────────────────────┐
│ ① canvas 绘制（arc/rect/moveTo/lineTo + fill/stroke）  │
│   → 实心形状坐标 / 空心轮廓坐标（矢量，零误差）         │
│ ② fetch / XHR → 捕获提交 URL、方法、body、header       │
│ ③ document.cookie setter → 捕获 HIK0 等 cookie 写入    │
│ ④ performance.now / Date.now → 可控时间源              │
└───────────────┬───────────────────────────────────────┘
                ▼
┌─ L5 求解 + 提交 ──────────────────────────────────────┐
│ dx = hollow.x − solid.x（矢量减法，不做图像识别）       │
│ 按 ② 捕获的格式构造提交（带上 ③ 捕获的 cookie）         │
│ 校验响应：202/200 + 下一次业务请求是否放行              │
└───────────────────────────────────────────────────────┘
```

---

## 3. 分阶段与验收（每阶段都有「失败即转 B」的判据）

### P0 · 环境能跑（0.5 天）

**做**：装 `jsdom` + `@napi-rs/canvas`，按 §5 补环境，跑 `waf_node/probe.js` 直到 widget JS 执行完不抛错。

```powershell
cd waf_node
npm i jsdom @napi-rs/canvas        # 都是预编译包，Windows 不需要 VS build tools
node probe.js --widget ..\runs\captcha_specimens\waf20261116\waf_widget_8qcykgusbcwc.5057567.js
```

**验收**：脚本跑完 `ok:true`，且 `document.cookie` 里出现 `HIK0kat3rt9xO`。

**失败判据**：连续补 3 轮仍卡在同一处（尤其是 `toDataURL` / `webgl` 相关）→ **转 B 路线（headless）**。

### P1 · 拿到矢量题面（1 天）

**做**：hook `CanvasRenderingContext2D` 的 `arc/rect/moveTo/lineTo/closePath/fill/stroke`，
按「是否有 `fill`」区分实心/空心，得到形状中心与尺寸。

**验收**：hook 出的坐标与同页面 `captureVisibleTab` 截图做像素比对，误差 < 3 px。

**失败判据**：形状不是逐个绘制而是 `drawImage(一张整图)` → hook 不到 → 转 P1'（对 hook 到的 `drawImage` 源做像素分析）。

### P2 · 拿到提交格式（0.5–1 天）

**做**：hook `fetch` / `XMLHttpRequest`，记录提交的 URL、method、headers、body；
复用现有 `ext_server.py` 记录过的 `POST /T7AyTrxoWxGd?sBCIo0XL=…` 做交叉验证。

**验收**：能在 Node 里复现一次提交，拿到 `202` 且响应体与浏览器里一致。

### P3 · 端到端（0.5 天）

**做**：P1 的 dx → 按 P2 的格式提交 → 拿 cookie 回到 curl_cffi 会话 → 请求 `SelectCountry`。

**验收**：`SelectCountry` 返回 200 且有正文（不是 202 空壳），并进入日历页。

---

## 4. 核心代码骨架

见 `waf_node/harness.js`（主流程）、`waf_node/shims.js`（环境垫片）、`waf_node/probe.js`（P0 探针）。
下面是关键片段的索引，细节在文件里。

### 4.1 Hook canvas 绘制（P1 核心）

```js
const shapes = [];
function hookCanvas(w) {
  const P = w.CanvasRenderingContext2D.prototype;
  let cur = null;                      // 当前路径
  const wrap = (name, kind) => {
    const orig = P[name];
    P[name] = function (...a) {
      if (kind === "begin") { cur = { op: name, args: a, pts: [] }; shapes.push(cur); }
      else if (kind === "pt") cur && cur.pts.push(a.slice(0, 2));
      else if (kind === "paint") cur && (cur.paint = name);   // fill 或 stroke
      return orig.apply(this, a);
    };
  };
  wrap("beginPath", "begin");
  wrap("arc", "pt"); wrap("rect", "pt");
  wrap("moveTo", "pt"); wrap("lineTo", "pt"); wrap("closePath", "pt");
  wrap("fill", "paint"); wrap("stroke", "paint");
}
```

> 判据：`paint === "fill"` → **实心**（要拖的那个）；`paint === "stroke"` → **空心轮廓**（目标）。
> 这和线上视觉完全一致（今天用截图验证过：canvas 层只有实心，background 层只有空心）。

### 4.2 Hook 提交（P2 核心）

```js
function hookFetch(w, realFetch) {
  w.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const rec = { url, method: init.method || "GET", body: init.body || null,
                  headers: init.headers || {}, at: Date.now() };
    captured.push(rec);
    // 关键：不要立刻放行，先落盘，必要时可以拦下来自己改 body
    return realFetch(input, init);
  };
  // XHR 同理：hook open/send
}
```

### 4.3 Hook cookie（P0 验收）

```js
function hookCookie(w) {
  const jar = new Map();
  let _cookie = "";
  Object.defineProperty(w.document, "cookie", {
    get: () => _cookie,
    set: (v) => {
      _cookie = mergeCookie(_cookie, v);
      const [nv] = String(v).split(";");
      const [k, val] = nv.split("=");
      jar.set(k.trim(), (val || "").trim());
      console.log("[cookie+]", k.trim());
    },
  });
  return jar;
}
```

---

## 5. 补环境清单（按实测报错整理）

| 分组 | API | 怎么补 | 风险 |
|---|---|---|---|
| 全局 | `window` / `self` / `top` / `parent` / `frames` / `opener` | 全指向同一个全局对象（**第一次报错就是缺 `top`**） | 低 |
| 文档 | `document.createElement/appendChild/removeChild/documentMode/cookie/location/referrer/readyState/addEventListener` | jsdom 原生覆盖大部分；`documentMode` 手动给 `undefined` | 低 |
| 定位 | `location.href/protocol/host/pathname/search/hash` | jsdom + 正确 `url` 参数 | 低 |
| 导航器 | `navigator.userAgent/platform/language/languages/webdriver/hardwareConcurrency` | 从真实 Chrome 抄一组固定值；`webdriver` 给 `false` | 中（值要与 UA 自洽） |
| 屏幕 | `screen.width/height/availWidth/availHeight/colorDepth` | 固定 1920×1080 / 24 | 低 |
| 时间 | `performance.now/markResourceTiming/timing`、`Date.now` | `performance.now` 用单调计时器；`markResourceTiming` 给空函数 | 低 |
| 定时器 | `setTimeout/setInterval/clearTimeout/clearInterval` | Node 原生，但要**主动清理**（脚本会挂心跳） | 中（不清理进程不退出） |
| 网络 | `fetch` / `XMLHttpRequest` | 用 `undici` 或 Node 原生 fetch，外面包 hook | 中（要处理 CORS 语义） |
| 图形 | `HTMLCanvasElement` / `CanvasRenderingContext2D` / `Image` | `@napi-rs/canvas` 提供，jsdom 里替换 `createElement('canvas')` | **高**（见 §7） |
| 加密 | `crypto.getRandomValues` / `crypto.subtle` | Node `crypto.webcrypto` 直接给 | 低 |
| 事件 | `Event` / `CustomEvent` / `EventTarget` / `addEventListener` | jsdom 原生 | 低 |

> 补的原则：**报错驱动**。一次只补被点名的那一个，补完立刻重跑，别预先堆一大坨（堆多了反而掩盖真实依赖）。

---

## 6. Hook 点详解（为什么这四个就够）

| Hook 点 | 拿到什么 | 解决今天的哪个失败点 |
|---|---|---|
| canvas 绘制 | 实心/空心的**矢量**坐标 | dx 算错（识图误差、配对跳）→ 直接减，零误差 |
| fetch / XHR | 提交 URL、body 结构、签名参数 | 不知道怎么提交 / 点错 `#swap` |
| document.cookie setter | `HIK0kat3rt9xO` 等凭证 | 会话无法在多进程间传递 |
| performance.now | 时间源 | 轨迹/时序校验依赖它（若要伪造轨迹） |

**不需要 hook 的**：鼠标事件、滑轨、手柄、Submit —— A 路线根本不走 UI 层。

---

## 7. 风险与对策

| 风险 | 表现 | 对策 | 转 B 判据 |
|---|---|---|---|
| **canvas 指纹** | 脚本 `toDataURL()` 后与内部预期比对，`@napi-rs/canvas` 渲染差异导致不一致 | 在 shim 里伪造 `toDataURL` 返回固定串；或从真实 Chrome 采一次真值硬编码 | 需要真值且对不上 → 转 B |
| **WebGL 指纹** | `getContext('webgl')` 后读 `UNMASKED_RENDERER` | 返回固定字符串（如 `ANGLE (Intel…)`） | 同上 |
| **内联引导脚本缺失** | `_$fT` 类报错反复出现 | **必须从 412 挑战页完整抓 HTML**，不能只用外部 widget | 挑战页拿不到 → 转 B |
| **心跳/定时器** | Node 进程不退出、请求被定时重放 | `w.close()` + 全局 `clearInterval` 记账 | — |
| **加密参数** | 提交 body 里有看不懂的密文 | 不逆向，直接**复用 hook 到的字面值**（每次现取） | 需要跨会话复用 → 转 B |
| **412 挑战频率** | 触发后短时间全是 412 | 走代理池换出口，参照 `proxy_watch.py` | — |

---

## 8. 降级路径（B 路线）

一旦 P0 卡在指纹，立刻切：

```powershell
chrome.exe --headless=new --remote-debugging-port=9444 --user-data-dir=... `
           --no-first-run "https://abs3.td.gov.hk/?lang=sc"
```

然后用 CDP 做和 A 一样的事：

- `Runtime.evaluate` 取 `canvas.toDataURL()` + `getComputedStyle(c).backgroundImage`（等价于 P1）
- `Page.addScriptToEvaluateOnNewDocument` 注入 hook（等价于 P1）
- `Network.requestWillBeSent` / `Network.responseReceived` 看提交（等价于 P2）
- `Input.dispatchMouseEvent` 走可信输入（今天已验证在本地 mock 上 6/6 通过）

B 的好处：指纹天然真实；代价：要维护一个内核。

---

## 9. 与现有代码的对接

| 现有资产 | 在 A 路线里的角色 |
|---|---|
| `slider.py` | 保留作**兜底**（B 路线用）；A 路线用自己的矢量版 `dx = hollow.x - solid.x` |
| `ext_server.py` | 继续当观测台；A 路线把每次结果 POST 到 `/conb` 之类新端点做对账 |
| `waf_mock/` | **回归环境**：A 路线先在自己复刻的 mock 上跑通 50 轮，再上线上 |
| `token_pool.py` / `proxy_watch.py` | 出口 IP 池，处理 412 频率 |
| `runs/captcha_specimens/` | 已抓的样本 + widget JS，P0/P1 的输入 |

---

## 10. 工期与投入

| 阶段 | 人天 | 主要不确定性 |
|---|---|---|
| P0 环境跑通 | 0.5 | 指纹 |
| P1 矢量题面 | 1 | 是否 `drawImage` 整图 |
| P2 提交格式 | 0.5–1 | body 是否含密文 |
| P3 端到端 | 0.5 | 会话/cookie 衔接 |
| **合计** | **2.5–3 天** | |

---

## 11. 立刻可做的第一步

```powershell
cd waf_node
node probe.js --widget ..\runs\captcha_specimens\waf20261116\waf_widget_8qcykgusbcwc.5057567.js
```

它会用最小 shim 跑一遍 widget JS，输出「缺什么 API」的清单。
把清单喂给 `shims.js` 逐个补，就是 P0 的全部工作。
