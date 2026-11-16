# waf_node · A 路线骨架（Node 补环境跑 WAF 挑战脚本）

完整方案见 [../纯脚本方案_WAF滑块_Node补环境.md](../纯脚本方案_WAF滑块_Node补环境.md)。

## 文件

| 文件 | 作用 | 状态 |
|---|---|---|
| `probe.js` | P0 探针：把 widget JS 丢进 Node，输出「缺哪些 API」 | **可立即运行，无需依赖** |
| `shims.js` | 环境垫片（navigator / screen / performance / WebGL 指纹 / 定时器记账） | 骨架，按 P0 报错迭代补 |
| `harness.js` | 主流程：抓挑战页 → jsdom 执行 → hook 绘制/提交/cookie → 求解 | 骨架，需 `npm i` |
| `package.json` | 依赖：`jsdom` + `@napi-rs/canvas` | 就绪 |

## 快速开始

```powershell
cd waf_node
npm i jsdom @napi-rs/canvas        # 都是预编译包，Windows 不需要 VS build tools

# P0：只求「环境能跑 + 拿到 cookie」
node harness.js --stage p0

# P1：多了「hook 绘制，输出矢量题面」
node harness.js --stage p1

# P2：多了「hook 提交，打印格式」
node harness.js --stage p2
```

没有网络/没有挑战页时，先用本地文件跑：

```powershell
node harness.js --stage p1 --html ..\runs\node_harness\challenge.html
```

## 探针实测结论（2026-11-16 21:3x）

```
node probe.js --widget ..\runs\captcha_specimens\waf20261116\waf_widget_8qcykgusbcwc.5057567.js
→ { ok:false, err:"_$dF[_$pU[100]] is not a function",
    missing: ["document.createElement","document.appendChild","document.removeChild"] }
```

两条可直接用的结论：

1. **304 KB 的 widget 能被 Node 完整加载并进入执行**（不是语法/模块问题），
   报错点落在 DOM 操作上 —— 这正是 jsdom 覆盖的范围。
2. **手写 dummy 走不通**：JS 里 `function` 自带 `call/apply/bind/name/length/prototype`，
   dummy 用 Proxy 包函数时，脚本一旦访问这些自有属性就会拿到原生实现，`this` 绑错后
   就报 `is not a function`。**所以 P0 不要手搓垫片，直接上 jsdom**（`harness.js` 已是这条路）。

`probe.js` 保留的价值：在你改 shims 时，用它快速确认「还缺什么」，
不用每次都起 jsdom 全流程。

## 下一步（P0 的验收）

跑 `node harness.js --stage p0`，看 `report.json` 里：

- `executed: true` —— 内联段 + widget 都跑完了
- `cookies` 里出现 `HIK0kat3rt9xO` —— 说明挑战通过、凭证已下发

这两条都满足，才能进 P1。

---

## P0 进展（2026-11-16 22:2x 实测）

### 已完成

| 项 | 结果 |
|---|---|
| `npm i jsdom @napi-rs/canvas` | ✅ 41 包，4.6 秒 |
| jsdom + canvas 联合测试 | ✅ `createCanvas(420,428)` → `toDataURL()` 1850 B |
| widget JS 在 jsdom 里加载 | ✅ 304 473 B 能进，且**注册上了 DOM 事件**（jsdom 触发到 `fireAnEvent`） |
| `probe.js`（手写 dummy） | ✅ 可用于快速查「缺哪些 API」 |

### 当前唯一阻塞：缺真实挑战页

```
$ node p0_widget_test.js
[init] $_ts 占位已注入（真实值来自挑战页内联段）
[run] 抛出：Cannot set properties of undefined (setting '_$fT')
      at _$dA (…:1:139970)  ← 读写 $_ts 的子状态
```

widget 第一行就是 `if ($_ts.cd)`，后续在 `$_ts` 上挂自己的状态（`_$fT` 等）。
占位对象补不出真实结构，**必须用 412 挑战页里那段内联引导脚本产出的 `$_ts`**。

### 为什么现在抓不到挑战页

| 出口 | `?lang=sc` | `SelectCountry` |
|---|---|---|
| 本机直连 | 200（SPA 壳 708 B，无 WAF 脚本） | 302（重定向） |
| 代理 14.215.163.195 | TLS 错误 | 超时（代理已死） |

**WAF 只在对出口「有疑问」时才给 412 挑战页**，放行态下永远拿不到。

### 两条补齐路径

**路径 A：撞 412**
换一个干净的代理出口（`proxies_api.txt` 巨量IP 提取），直到返回 `412` 且 body 里有 `$_ts`：

```python
r = creq.get(url, proxies={"https": px}, impersonate="chrome", verify=False)
if r.status_code == 412 and b"$_ts" in r.content:
    open("runs/node_harness/challenge.html","wb").write(r.content)
```

拿到后：`node harness.js --stage p0 --html ..\\runs\\node_harness\\challenge.html`

**路径 B：从运行中的页面取 `$_ts`**
w1 打开 abs3（放行态也有 WAF 脚本在跑），用 MAIN world 注入读出 `window.$_ts` 的完整结构，
存成 `challenge_state.json`，P0 直接喂给 shim。这样不用等 412。

### 已就绪的验证命令

```powershell
cd waf_node
node p0_widget_test.js        # 当前会停在 $_ts 层（预期）
node probe.js                 # 查缺什么 API
```
