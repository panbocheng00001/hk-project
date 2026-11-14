# SECURITY_MODEL —— 这套代码会做什么、不会做什么

这份文档回答一个问题：**哪些行为是设计内的，哪些是明确排除的，排除项写在哪一行代码上。**
读完它应该能直接去核对代码，而不是靠文案猜意图。

---

## 1. 用途声明（一句话）

一套跑在操作者自己机器上的**表单自动化与会话管理**代码：把预约流程里重复的填表动作自动化，
把需要判断的环节按下面的分层规则处理。

---

## 2. 验证码处理分两层

| 层 | 文件 | 开关 | 默认 | 行为 |
|---|---|---|---|---|
| 决策层 | `captcha_flow/` | 无 | 常开 | 分类 → 决策 → 执行 → 复核。滑块/图形码/点选一律 `HAND_TO_HUMAN` |
| 加速层 | `captcha_auto.py` | `CAPTCHA_AUTO` | 启用 | 进入决策层之前，先尝试用 `captcha_lab_bridge` 算位移并拖动；任何一步失败即回落决策层 |

两者的关系是**可整层关闭**：`CAPTCHA_AUTO=0` 之后，`captcha.guard()` 的行为 100% 等于只有决策层的版本，
`captcha_flow/tests/` 的 30 项契约测试在任何一种开关状态下都必须全绿。
`tests/test_captcha_auto_e2e.py` 里有一条用例专门断言"关闭时不产生任何拖动"。

---

## 3. 行为清单：明确做什么

| # | 行为 | 位置 | 影响面 |
|---|---|---|---|
| 1 | 读本机配置里的申请人资料，填进网页表单 | `fill_form.py` | 仅本机 + 目标页 |
| 2 | 把本机证件影像按表单要求上传 | `fill_form.py` `ext_server.py:/file` | 仅目标页 |
| 3 | 判定当前页面的验证码类型 | `captcha_flow/detect.py`、`extension/captcha_guard.js` | 只读 DOM |
| 4 | 隐形验证时等 token 生成 | `captcha_flow/session.py:_wait_token` | 只等，不推断 |
| 5 | 勾选框点一次 | `captcha_flow/session.py`、`captcha_guard.js:clickOnce` | 单次，不重试 |
| 6 | 需要判断的验证码：抬窗口 + 通知 + 轮询等待 | `session.py:_hand_to_human` | 不做内容识别 |
| 7 | **（加速层，默认启用）** 滑块位图 → 位移 → 轨迹 → 拖动 → 复核 | `captcha_auto.py`、`captcha_lab_bridge.py`、`captcha-lab/solver/` | 仅当前页面的滑块组件；失败即回落第 6 条 |
| 8 | 记录每一步的 DOM / 截图 / 接口，供事后复盘 | `runlog.py`、`journal.py`、`netrec.js` | 本机 `runs/` |
| 9 | 节流与熔断，避免请求过密 | `throttle.py` | 限制自身 |
| 10 | 会话票据的预热、续期、取用 | `session_pool.py` | 仅本机 |
| 11 | 关键节点通知（webhook，可选） | `notifier.py` | 用户自己的通道 |

## 4. 行为清单：明确不做（设计排除，不是待办）

| # | 排除项 | 排除理由 | 写在哪 |
|---|---|---|---|
| 1 | 图形码 / 点选题的内容识别（OCR、目标检测） | 这两类没有可机械核对的判据，错了会污染会话；加速层只覆盖滑块与几何重合滑块 | `captcha_auto.py` 只处理 canvas 滑块；`policy.py` 交人 |
| 2 | 虚假标记输入可信度（`isTrusted=true` 合成事件） | 该字段语义是"是否由用户产生"，标成 true 等于向站点提供不实信息 | `manifest.json` 无 `debugger` 权限；无 CDP 输入注入 |
| 3 | 改动浏览器指纹（UA/时区/语言/DPR） | 一律取本机真值，`preflight.py` 校验自洽性 | `preflight.py` |
| 4 | 改动证件影像内容 | 影像按原件上传 | `fill_form.py` 无图像写回 |
| 5 | 大规模账号注册 / 凭据填充 | 仓库只管理操作者自己的少量申请人记录 | 无相关模块 |
| 6 | 关掉站点的挑战层后继续高频请求 | 遇到 412 就退避 + 交人，不在循环里重试 | `edge_challenge.py`、`throttle.py` |
| 7 | 把会话/票据转卖或跨账号共享 | 票据池绑定单一操作者，取用时校验归属 | `session_pool.py` |
| 8 | 在仓库里保存真实凭据或身份物料 | 真实物料在 `_private/`，代码树只有 `config.example.json` | `.gitignore`、`tools/wording_lint.py` |

---

## 4. 数据流与落点

```
本机 config.json ──┐
本机 files/     ──┼─→ ext_server.py (127.0.0.1:8765) ─→ 浏览器扩展 ─→ 目标页面
环境变量(webhook)─┘                                        │
                                                           ↓
                                             runs/ (截图 / DOM / 接口 / 日志)
```

- `ext_server.py` 只监听 `127.0.0.1`，不对外暴露。
- `runs/` 在 `.gitignore` 里，且含个人数据，不入库。
- webhook 地址与签名从环境变量读，不落盘。

---

## 5. 凭据与个人数据的处理约定

| 类别 | 存放位置 | 是否入库 |
|---|---|---|
| 申请人姓名/证件号/邮箱/电话 | `config.json`（本机） | 否（模板是 `config.example.json`） |
| 证件影像 | `files/`（本机） | 否 |
| 邮箱 OAuth token | 环境变量 / `_private/` | 否 |
| webhook 地址与签名 | 环境变量 | 否 |
| 目标站点会话 cookie | `runs/`（运行时生成） | 否 |
| 出口节点凭据 | `_private/` | 否 |

`tools/wording_lint.py` 可以在提交前跑一遍，检查有没有漏进文本的凭据与个人数据：

```bash
python tools/wording_lint.py --root .
```

命中 pii 规则（邮箱、手机号、证件号、token、疑似哈希）时退出码非 0，可以直接挂 CI。
词表与正则见 `tools/wording_lexicon.json`，可读版见 `docs/WORDING_LEXICON.md`。

---

## 6. 威胁模型（这份代码自己可能出什么问题）

| 风险 | 触发条件 | 现有对策 |
|---|---|---|
| 请求过密把自己打到限流 | 反复 dry-run / 多实例同时跑 | `throttle.py` 三阈值熔断；`preflight.py` 先分诊再铸票 |
| 会话票据串号 | 多申请人并发 | 票据带归属字段，取用时校验 |
| 个人数据误入库 | 手工 `git add` | `.gitignore` + `wording_lint.py` 的 pii 规则 |
| 加速层失败后卡住流程 | 内核不可用 / 页面结构变了 | 加速层失败即回落人工交接，并打日志说明原因 |
| 通知通道泄露 | webhook 写进配置 | 只从环境变量读 |

---

## 7. 换场景时的三个前提

换目标站点或换用途时，这三条决定代码还成不成立，缺一条就会在运行时暴露：

1. **操作者本人是该服务的使用者**——替不认识的人批量操作不在设计范围内（票据池、节流、通知都按单操作者建模）。
2. **站点可访问且行为稳定**——412 挑战、滑块形态、字段名变化都按"退避 + 交人"处理，不做自适应改写。
3. **人仍在闭环里**——OTP、资料核对、非滑块类验证码由人完成；加速层整层可关。

代码不检查这三条（检查不了），所以写在这里，并在 `AGENTS.md` §3 里以不变量形式固定下来。
