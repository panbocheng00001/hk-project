# 架构：两层、六组、一条依赖方向

> 这份文档回答一个问题：**代码里哪个目录负责什么，改东西时该动哪一层，哪条依赖是不许反过来的。**
>
> 读完这页 + `docs/AI_CODING_SPEC.md` §6，就应该知道一件需求该落在哪个文件。

---

## 1. 两层

```
┌─────────────────────────────────────────────────────────────┐
│ captcha-lab          识别内核（可独立 pip 安装，纯 Python）   │
│  solver/   图片 → 位移 → 轨迹 → 载荷；HTTP 服务；CLI          │
│  defense/  出题 + 鲁棒性评测（只对合成题跑）                  │
│  tests/    166 项，全部离线                                    │
└───────────────────────────▲─────────────────────────────────┘
                            │ 只允许这个方向：宿主调用内核
                            │ 入口只有一个：captcha_lab_bridge.py
┌───────────────────────────┴─────────────────────────────────┐
│ booking-lab          宿主（本机运行的表单自动化 + 会话管理）   │
│  captcha_flow/  决策层：分类 → 决策 → 执行 → 复核（默认交人）  │
│  captcha_auto.py 可选加速器：CAPTCHA_AUTO 开关控制            │
│  extension/     浏览器扩展（页面内脚本）                       │
│  captcha_mock/  复刻题服务（Node，零依赖，127.0.0.1:8777）     │
└─────────────────────────────────────────────────────────────┘
```

**依赖方向是单向的。** `captcha-lab` 里出现 `booking` 字样、或者出现指向宿主的绝对路径，都算破坏分层：
`tools/wording_lint.py` 的失效路径规则会挡住其中一部分，其余靠 review。

---

## 2. booking-lab 的六组模块

| 组 | 文件 | 职责 | 改它时要小心什么 |
|---|---|---|---|
| **验证码** | `captcha.py`、`captcha_auto.py`、`captcha_lab_bridge.py`、`captcha_report.py`、`captcha_flow/`、`extension/captcha_guard.js` | 分类、决策、交接、可选加速、耗时报表 | `captcha_flow/policy.py` 的四条原则是契约，测试守着 |
| **流程** | `fill_form.py`、`resume.py`、`http_flow.py`、`check.py`、`flow_verify.py`、`preflight.py`、`prefill_check.py` | 走完预约步骤；只读查名额；开跑前自检 | 只读路径（`check.py`）不得写成提交动作 |
| **网关与会话** | `session_pool.py`、`edge_*.py`（challenge / cookies / token / handshake / enter / probe / path_probe / flow_* / window_watch / replay / browser_check / trace_page） | 412 挑战的识别、退避、票据池、放行窗口观测 | 挑战失败必须退避，不能在循环里继续请求 |
| **节流与守护** | `throttle.py`、`run_guard.py`、`site_monitor.py`、`human.py` | 全局速率熔断、多实例编排、站点状态监视、人工介入点 | 阈值只允许收紧，不允许放宽；min_poll_interval 无关闭开关 |
| **记录** | `journal.py`、`runlog.py`、`recorder.py`、`replay.py`、`timeline.py`、`notifier.py` | 事件账本、逐步回放、关键节点通知 | 账本格式只增不改；写入失败必须降级为日志 |
| **服务与脚本** | `ext_server.py`、`*.ps1`、`*.bat`、`get_cft.ps1`、`askpass.cmd` | 本地数据服务（127.0.0.1:8765）、启动脚本 | 只监听回环；不要加对外端口 |

---

## 3. 一次完整流程的数据流

```
config.json（申请人资料，本机）
        │
        ▼
preflight / prefill_check ──→ 资料自检 + 环境自检
        │
        ▼
fill_form · http_flow · resume ──→ 目标页面（example.test 占位）
        │                          │
        │                          ├── edge_challenge：412 挑战页？→ 退避 / 交人
        │                          ├── session_pool：票据够不够新？→ 铸 / 续 / 取
        │                          └── captcha_flow：什么形态？→ 决策
        │                                        │
        │                                        ├── 隐形 token → 等
        │                                        ├── 勾选框     → 点一次 → 复核
        │                                        └── 滑块/图形  → 交人（默认）
        │                                                          │
        │                          captcha_auto（CAPTCHA_AUTO=1）┘ 可选加速
        │                              └── captcha_lab_bridge → captcha-lab/solver
        ▼
journal / runlog / runs/（截图、DOM、接口、账本）──→ captcha_report.py 出报表
```

---

## 4. 三层不变量（改代码前先确认没碰这三条）

1. **决策层纯函数。** `captcha_flow/policy.py` 无 IO、无副作用、无随机；
   给定 `ChallengeInfo` 永远同一 `Decision`。所有阈值来自 `Policy`，不散落在逻辑里。
2. **探测层只读。** `captcha_flow/detect.py` 只允许 `querySelector` 与正则，不点击、不发请求。
3. **交接优先。** 任何自动化路径失败，回落「抬窗口 + 通知 + 等人工」，并打日志说明原因。
   静默重试、静默回退都不允许。

---

## 5. 复刻题为什么单独存在

`booking-lab/captcha_mock/`（Node，零依赖）用固定种子生成同形态的题，并只在本机返回真值。
它的作用是把「识别对不对」和「线上能不能过」分开：

- 复刻题上 30/30、误差 <1px，只证明**识别 + 换算**正确；
- 线上还叠着会话、频率、轨迹三道，那部分在 `throttle.py` / `session_pool.py` / `run_guard.py`。

所以 `captcha_mock` 的判据是**基准**，不是实现目标：不能为了让它通过而改它的容差。

---

## 6. 加东西时往哪放

| 你想加的 | 放这里 | 不要放这里 |
|---|---|---|
| 新识别策略 / 新题型 | `captcha-lab/solver/`（+ `tests/`） | `captcha_flow/` |
| 新站点口径（字段名、缩放） | `captcha-lab/solver/profiles.py` | 宿主业务代码 |
| 交接更快的提醒 / 更细的统计 | `booking-lab/captcha*.py` | `captcha_flow/policy.py` 的既有字段 |
| 新的页面形态识别 | `booking-lab/captcha_flow/detect.py`（只读） | `session.py` |
| 新的本地服务接口 | `ext_server.py`（回环） | 对外的 HTTP 端口 |
| 一次性排查脚本 | `booking-lab/tools/` | 仓库根目录 |
