# booking-lab —— 预约流程辅助工具

一个本机运行的浏览器自动化工具，用来把**重复的填表步骤**压缩掉：
打开预约页 → 选服务 → 填申请人资料 → 上传证件影像 → 选日期时段 → 提交。
验证码、排队等待、OTP 这类必须由人判断的环节，工具只负责**发现并交接**，不代替判断。

> 本文所有示例域名都是 `example.test` 占位符。
> 真实目标地址通过本机 `config.json` 提供，不入库。见 `docs/SECURITY_MODEL.md`。

---

## 1. 它解决什么问题

手工做一次完整预约，有 40 多个字段要填、4 张图要传、一次 OTP 要收。
真正需要人做的只有两件事：**核对资料对不对**、**完成验证码**。
其余都是机械复制粘贴，且必须在很短的会话有效期内完成。

工具的定位就是把机械部分自动化，把判断部分留给人：

| 环节 | 谁做 | 说明 |
|---|---|---|
| 打开页面、走服务选项 | 工具 | 固定路径的点选 |
| 填写申请人资料 | 工具 | 从本机配置读，字段与人给的资料逐字一致 |
| 上传证件影像 | 工具 | 本地文件按表单要求塞入 |
| 选择日期时段 | 工具 | 按 `bureau_preference` 顺序取最早可用 |
| **验证码 · 滑块** | 工具（可选层）→ 人（兜底） | `captcha_auto` 先尝试一次识别与拖动；关闭或失败即抬窗口交人 |
| **验证码 · 图形码/点选** | **人** | 工具只做分类、抬窗口、响铃通知，不做内容识别 |
| **OTP** | **人** | 工具等待并填入，取码动作由人完成 |
| 提交 | 工具 | 填完即提交，回执留档 |

两层怎么分工、开关怎么关、失败怎么退，见 [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md) 第 2 节
与 [`docs/CAPTCHA_AUTO.md`](docs/CAPTCHA_AUTO.md)。

---

## 2. 目录结构

```
booking-lab/
├─ captcha_flow/            验证码决策层（本仓库核心，纯逻辑、可单测）
│  ├─ model.py              分类 / 结果 数据类型
│  ├─ detect.py             只读 DOM 探测
│  ├─ policy.py             纯函数决策：什么能自动、什么必须交人
│  ├─ session.py            状态机：按决策执行，管超时与交接节奏
│  ├─ adapters.py           唯一的浏览器耦合层（Playwright / 测试假实现）
│  └─ tests/                30 项决策契约 + 25 项交接计时，全部离线
├─ captcha_auto.py          可选加速层：滑块识别 → 位移 → 轨迹 → 拖动 → 复核
├─ captcha_lab_bridge.py    调用 captcha-lab 内核的薄桥（单向）
├─ captcha_report.py        人工交接耗时报表（只读 journal）
├─ extension/               浏览器扩展（页面内脚本，不经过调试协议）
│  ├─ captcha_guard.js      验证码分类与交接（与 captcha_flow 同一套策略）
│  ├─ content.js            页面内状态机：判断当前在哪一步、执行该步
│  ├─ background.js         注入调度、本地服务通信、网络记录
│  ├─ netrec.js / netrelay.js  接口记录与转发
│  └─ manifest.json         权限声明（无 debugger）
├─ captcha_mock/            复刻题服务（Node，零依赖，127.0.0.1:8777）
├─ fill_form.py             表单填写主流程（Playwright）
├─ http_flow.py             无浏览器流程：纯 HTTP 走完排队与名额查询
├─ waf_profile.py           身份与头顺序唯一出口（UA / Sec-CH-UA / TLS 画像 / locale）
├─ session_pool.py          会话票据池（预热 / 续期 / 取用）
├─ edge_*.py                412 挑战的识别、退避、票据与放行窗口观测
├─ check.py                 名额查询（只读，不提交）
├─ flow_verify.py           端到端自检
├─ throttle.py              全局节流与熔断（防止把自己打到限流）
├─ journal.py / runlog.py   全链路事件账本与逐步回放
├─ notifier.py              关键节点通知（webhook，未配置则静默）
├─ ext_server.py            扩展的本地数据服务（127.0.0.1:8765）
├─ pagekit/                 页面渲染 → 结构摘要 → 脱敏外发（见 docs/PAGEKIT.md）
│  ├─ browser.py            Playwright 会话与快照落盘
│  ├─ digest.py             快照 → 摘要（Markdown 可外发）
│  ├─ redact.py             个人资料与口径词换占位；verify 查残留
│  ├─ cli.py                open / walk / digest / redact / verify
│  └─ mcp_server.py         stdio MCP 工具面（page_open / page_click / redact_text …）
├─ tools/                   排查与回归：captcha_regress（闸门）/ captcha_solve / captcha_selftest / bench_captcha_auto
└─ docs/                    行为边界、验证码设计、加速层、交接 SLA（索引见 ../../docs/README.md）
```

---

## 3. 怎么跑

### 3.1 准备配置

```bash
cp config.example.json config.json
```

`config.json` 里填三类东西：

1. `applicants[]` —— 申请人资料。**必须与证件逐字一致**，工具不做任何规整。
2. `files_dir` —— 证件影像目录。
3. `notify` —— 可选。webhook 地址从环境变量读，不写进文件：

```bash
set BOOKING_WEBHOOK_URL=https://webhook.example.test/robot/send?access_token=...
set BOOKING_WEBHOOK_SECRET=SEC...
```

没有配置就静默跳过，不影响主流程。

### 3.2 只读检查（不提交、不占会话）

```bash
python check.py                # 探一次
python check.py --loop 180     # 每 3 分钟探一次
```

退出码：`0` 有名额 / `10` 无名额 / `20` 未探到 / `30` 站点故障 / `40` 出口受限。

### 3.3 走完整流程

```bash
# 起本地数据服务（扩展要用）
python ext_server.py

# 起浏览器（带扩展）
pwsh -File run_local.ps1 -Instance w1

# 或者不用浏览器，纯 HTTP 走排队 + 查名额
python http_flow.py --probe
```

### 3.4 验证码测试（离线）

```bash
python -m pytest captcha_flow/tests -q          # 55 passed（30 决策契约 + 25 交接计时）
python -m pytest captcha_flow/tests tests -q    # 115 passed（30 契约 + 25 计时 + 3 端到端 + 14 闸门 + 8 报表 + 16 pagekit + 19 身份/节流）
```

覆盖：分类映射、策略边界、自动预算上限、超时降级、
「滑块路径不产生任何自动点击」这条硬约束、加速层关闭时不产生拖动、交接耗时口径与提醒次数上限。

---

### 3.5 跑页面、出摘要（供页面内容分析）

```bash
python -m pagekit open --url "https://abs3.example.test/?lang=sc" --name home
python -m pagekit walk --url "https://abs3.example.test/?lang=sc" --steps pagekit/steps.example.json
python -m pagekit verify --in runs/pagekit/<时间戳>/home.digest.md   # 退出码 0 才外发
```

只有 `*.digest.md` 是外发物；词表在 `~/.hk-project/pagekit_terms.json`。细节见 [`docs/PAGEKIT.md`](docs/PAGEKIT.md)。

---

## 4. 分层：哪些是默认行为，哪些是可选层

| 层 | 文件 | 开关 | 默认 | 行为 |
|---|---|---|---|---|
| 决策层 | `captcha_flow/` | 无 | 常开 | 分类 → 决策 → 执行 → 复核；滑块/图形码/点选一律交人 |
| 加速层 | `captcha_auto.py` | `CAPTCHA_AUTO` | 启用 | 进决策层之前先算一次位移并拖动；失败即回落决策层 |

`CAPTCHA_AUTO=0` 之后，`captcha.guard()` 的行为 100% 等于只有决策层的版本。
四条设计约束（都在测试或代码里，不靠文档口头保证）：

1. **决策层不含像素读取**。`captcha_flow/` 不认识 canvas、不认识位移。
2. **探测层只读**。`detect.py` 只调 `querySelector` 与正则，不点击、不发请求。
3. **不把脚本事件标成硬件输入**。扩展不申请 `debugger` 权限，页面看到的输入就是脚本合成的事件。
4. **不做无限重试**。自动动作有次数上限（`Policy.max_auto`），触顶即交人；
   `throttle.py` 对整体请求速率熔断，`workers` 默认 2。

---

## 5. 代码层面能保证什么

| 保证 | 实现位置 | 怎么核对 |
|---|---|---|
| 不把脚本事件标成硬件输入 | 扩展不申请 `debugger` 权限 | `extension/manifest.json` |
| 不改浏览器指纹 | UA/时区/语言/DPR 取本机真值 | `preflight.py` 自洽性自检 |
| 自动动作有上限 | `Policy.max_auto`、`throttle.py` 三阈值熔断 | `test_policy_auto_budget_caps_automation` |
| 挑战不过就退避 | 412 走 `edge_challenge.py`，不在循环里重试 | `test_*challenge*`、`preflight.py` |
| 真实物料不入库 | `config.json` / 环境变量 / `_private/` | `.gitignore`、`tools/wording_lint.py` 的 pii 规则 |

**这套代码不判断"某站点是否可以自动化"这类问题**——那是操作者与站点之间的事，代码里也没有可核对的依据。
代码能自证的是上表五条，其余属于使用场景，不在本仓库覆盖范围内。

---

## 6. 变更记录入口

- 分层与开关：`docs/SECURITY_MODEL.md` 第 2 节
- 状态机的设计取舍：`docs/CAPTCHA_DESIGN.md`
- 加速层怎么开、怎么关、失败怎么退：`docs/CAPTCHA_AUTO.md`
- 改完怎么确认链路还能跑通（一条命令 + 退出码）：`docs/CAPTCHA_REGRESS.md`
- 人工交接耗时口径与提醒节奏：`docs/CAPTCHA_HANDOFF_SLA.md`
- 页面渲染、结构摘要与外发脱敏：`docs/PAGEKIT.md`
- 归档说明（哪些原始记录被移出、为什么）：`ARCHIVE_MANIFEST.md`
