# 提示词包（给 OPS / 任何编码模型）

> 这份文档回答一个问题：**要把这个仓库的下一步改动交给一个模型做，粘哪一段，才能拿到落盘、可跑、和仓库既有契约不打架的结果。**
>
> 用法：在第 0 节选一个任务类型 → 整段复制对应的代码块 → 只替换 `<>` 里的内容 → 连 `【前置】` 一起发。
> 改任务不要改这段结构，结构本身就是产物清单。

---

## 0. 30 秒选型

| 你要的 | 用哪段 | 交付物落点 | 验收命令 |
|---|---|---|---|
| 整理目录 / 重写文档 / 统一措辞 | **P1** | `docs/`、各 README | `tools/check.ps1` |
| 改识别算法、加题型、提精度 | **P2** | `captcha-lab/solver/` | `captcha-lab` pytest + `bench_*` |
| 让"滑块出现 → 人完成"更快、可观测 | **P3** | `booking-lab/captcha*.py` | `booking-lab` pytest + `captcha_report.py` |
| 把内核接进宿主、加开关 | **P4** | `booking-lab/captcha_lab_bridge.py`、`captcha_auto.py` | 桥接测试 + 复刻题 bench |
| 扩浏览器扩展侧 | **P5** | `booking-lab/extension/` | `node --check` + 本地夹具手测 |

所有段落共用三条前提（写在每段的 `【前置】` 里）：

> 只做加速层（`captcha_auto` → 桥 → 内核）那一件事时，用更细的一版：
> [`PROMPT_CAPTCHA_AUTO.md`](PROMPT_CAPTCHA_AUTO.md)，里面有 7 个先读文件、口径常量与 5 条验收命令。

1. 先读文件再动手，不凭记忆写路径和函数名；
2. 目标一律是本机夹具：`example.test` / `127.0.0.1:8777`（复刻题）/ `127.0.0.1:8765`（扩展数据服务）；
3. 行为约束写进代码和测试，不写进文档的说明段。

---

## 1. P0 通用前置（任何任务都粘这段在最前面）

```text
【前置】
仓库：E:\googleProduct\hk-project\hk-project-clean（下文路径都相对这个根）
先读这三份，再读任务里点名的文件：
  1) AGENTS.md                         仓库契约与不变量
  2) docs/AI_CODING_SPEC.md            注释/文档/命名规范（含词表）
  3) docs/ARCHITECTURE.md              两层职责与依赖方向

硬要求：
- 不新建"说明性"段落替代实现；README 只写：安装 / 接口 / 架构 / 实测 / 路线图。
- 不写立场、动机、免责类文字；要约束行为就写成不变量或测试用例。
- 目标地址一律用本机夹具；真实地址与凭据只存在于本机 config.json，不进仓库。
- 新增/修改代码后必须跑：pwsh -File tools/check.ps1（文案 lint + 两套 pytest），
  贴出它的完整输出。
- 改动清单按文件列，每个文件一句话说明改了什么。

定义完成：文件已落盘 + 验收命令全绿 + 输出贴出。只说方案不算完成。
```

---

## 2. P1 文档与结构整理

```text
【任务】整理这个仓库的结构与文案，目标是让下一个接手的人（或模型）读 15 分钟就能开工。

【现状】
- 两个子项目：booking-lab（宿主：表单填写/会话/交接/扩展）、captcha-lab（识别内核）。
- 两边 README 与 docs/ 里存在互相矛盾的描述：有的文档写"不做图像识别"，
  但 booking-lab/captcha_auto.py 已经在做；有的写"30 项测试"，实际是 30+25+3。
- 根目录 README / HANDOFF 仍在引用已经不存在的旧目录名。

【交付】
A. docs/ 补齐并互相链接：README.md（索引）、ARCHITECTURE.md（两层职责+依赖方向）、
   AI_CODING_SPEC.md（已存在，若有缺项补上）、WORDING_LEXICON.md（由工具生成，不手改）。
B. 重写根 README.md：一屏内说清 这是什么 / 两个子项目各做什么 / 怎么跑 / 怎么验 / 文档在哪。
   删掉所有关于"这份代码能不能被谁接受"的段落。
C. 重写根 HANDOFF.md：接手顺序（读哪 5 个文件）、当前实测数字、不变量列表、下一步可做什么。
D. 消除矛盾：所有写着"不做识别"的文档，改成两层模型——
   captcha_flow 默认交人（决策层不变），captcha_auto 是 CAPTCHA_AUTO 开关控制的可选加速器。
   逐处改：booking-lab/README.md、docs/SECURITY_MODEL.md、docs/CAPTCHA_DESIGN.md、
   captcha_flow/__init__.py 的模块头。
E. 数字一律换成实测值并附命令（见 docs/PROMPT_PACK.md 第 7 节底稿表）。

【硬约束】
1. 不改任何 .py 的代码逻辑，只改 docstring/注释/文档；
2. 每改一处文档，跑一次 tools/wording_lint.py，最后必须 0 block；
3. 不删测试、不改断言；
4. 文档里的路径必须真实存在（改完用 Test-Path 逐条验一遍）。

【验收】
pwsh -File tools/check.ps1        -> lint 0 block、115 passed、166 passed
```

---

## 3. P2 识别内核维护（captcha-lab）

```text
【任务】<一句话：例如"把 hit@1 从 96.7% 提到 98%" / "新增一种曲线重合题型" / "修 overlap 的某个错配对">

【先读】
1) captcha-lab/solver/engine.py         求解编排：图片 → 距离 → 轨迹 → 载荷
2) captcha-lab/solver/gap.py            缺口定位：6 策略投票融合 + ±2.5px 保守精修
3) captcha-lab/solver/overlap.py        几何重合滑块：三档阈值 → 连通域 → 8×8 剖面 → 色相精修
4) captcha-lab/solver/track.py          轨迹：最小加加速度 + 手颤 + 停顿 + 自检
5) captcha-lab/tests/                   166 项；改哪一块先看它的用例

【已实测，直接采信，不要重算口径】
- 基准样本：tests/data/waf_canvas.png
  python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34
  -> 实心中心 104.5px / 空心中心 335.0px / dx=230.50 / slider_dx=210.91（range = 400-34 = 366）
- 合成样本精度：python -m solver bench --n 60 -> hit@1 96.7%、hit@top3 100%、~66ms/张
- 全量测试：python -m pytest -q -> 166 passed（改动前先跑一遍留底）

【交付】
A. 改动落在 solver/ 下的具体文件（列出文件名与函数名，不要只说"优化算法"）；
B. 每个新策略给出：输入、输出、在 bench 上的增量、失效场景；
C. tests/ 里补用例，覆盖新分支与它最容易错的那一种输入；
D. 若改了判据，必须同时更新 docs/INTEGRATION.md 里对应的口径说明。

【硬约束】
1. 判据与 captcha_mock/analyze.js 的对齐关系不能破（tests/test_overlap.py 守着 0.00px）；
2. 不放宽容差来提升通过率；容差是站点口径，不是调参旋钮；
3. 不引入需要联网下载模型的依赖作为默认路径（检测器只能是可选增强）；
4. 不改变对外接口签名；要改就新增函数并保留旧名做别名；
5. 新代码带类型注解与中文 docstring，模块头四段（定位/输入输出/不变量/失败路径）。

【验收】
1. cd captcha-lab && python -m pytest -q                       -> 全绿，数量不少于 166
2. python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34
                                                               -> dx=230.50 / slider_dx=210.91
3. python -m solver bench --n 60                               -> 贴出 hit@1 / hit@top3 / 单张耗时
4. 改动清单 + 每个数字对应的命令
```

---

## 4. P3 宿主侧交接与可观测性（booking-lab）

```text
【任务】把"滑块出现 → 人工完成"这段做得更快、更可观测。不做识别、不算位移、不生成轨迹与载荷。

【范围声明（先看这条）】
- 不新增图像处理代码，不 import captcha_lab_bridge，不读 canvas 像素；
- 不产出 slider_dx / dx / 轨迹点 / payload；
- captcha_flow/policy.py 的四条决策原则不变：滑块走人工交接。

【先读】
1) captcha_flow/policy.py               Policy 默认值：human_wait_s=180、renotify_every_s=45、
                                        max_auto=2、renotify_max_n=5、renotify_backoff=1.6、poll_s=1.5
2) captcha_flow/session.py::_hand_to_human   抬窗口 → 通知 → 轮询 → 再通知
3) captcha_flow/adapters.py             FakePage：n_detect / fronted / n_click 可断言
4) captcha.py                           guard() / beep() / focus_browser_window() / _notify()
5) journal.py                           事件账本（只允许新增写入点，不改格式）
6) captcha_report.py                    现有 --days / --selftest
7) captcha_flow/tests/                  30 项契约 + 25 项交接计时，改完必须仍全绿

【交付（只这四项）】
A. 交接耗时结构化记录：captcha.py::guard() 在首次 HAND_TO_HUMAN 记 t_appear，
   返回前算 waited_s，落 journal：ev=handoff_done / waited_s / result / renotify_n / timeout。
   写入失败必须降级为打日志，不能中断流程。
B. 提醒节奏升级：policy 只增不改（renotify_max_n / renotify_backoff 已有则复用），
   第 k 次间隔 = renotify_every_s * backoff^(k-1)，上限 120s，到次数上限即停（不允许无限响）；
   每次再提醒记一条 ev=renotify。
C. captcha_report.py 增补统计口径：出现次数 / 人工完成率 / P50 / P90 / max / 超时数 /
   按 kind 分组 / 平均提醒次数 / 按天趋势。无数据时打印一行并退出 0。
D. captcha_flow/tests/test_handoff_timing.py 增补用例：耗时口径、提醒次数上限、
   完成后不再提醒、超时返回 TIMEOUT。时钟与 sleep 全部注入，不得依赖真实时间。

【硬约束】
1. 不改 policy 既有字段默认值，只允许新增；
2. 不碰 extension/，不新增自动点击路径；
3. 现有 30 项契约测试一条不能变；
4. 不引入第三方依赖，不联网。

【验收】
1. cd booking-lab && python -m pytest captcha_flow\tests -q     -> 55 passed（30 契约 + 25 计时）
2. cd booking-lab && python -m pytest captcha_flow\tests tests -q -> 115 passed
3. cd booking-lab && python captcha_report.py --selftest        -> PASS
4. cd booking-lab && python captcha_report.py --days 7          -> 有数据出表 / 无数据退出 0
```

---

## 5. P4 接线与开关（把内核接进宿主）

```text
【任务】<一句话：例如"新增一个 CLI，按需调用内核并打印 dx/slider_dx/轨迹风险分">

【先读】
1) booking-lab/captcha_lab_bridge.py   既有薄桥：solve_canvas / make_track / analyze_track /
                                       is_available / BridgeUnavailable
2) booking-lab/captcha_auto.py         可选加速器：solve_image / solve_page / probe，
                                       开关 CAPTCHA_AUTO（"0"/"false"/"off" 关闭）、CAPTCHA_LAB_PATH
3) captcha-lab/solver/overlap.py       内核实现（判据权威版本之一）
4) captcha-lab/solver/cli.py           python -m solver waf 的入口，口径参考它
5) booking-lab/captcha_flow/policy.py  决策层不变量（本次不改）

【已实测】
- 口径：range = max(10, track_w - handle_w) = 366；slider_dx = dx * range / bmp_w；判分容差 3px
- 基准：tests/data/waf_canvas.png -> dx=230.50 / slider_dx=210.91
- 复刻题服务：node booking-lab/captcha_mock/server.js -> 127.0.0.1:8777
- 本机系统代理 127.0.0.1:10808 会让 httpx 把回环请求也送进代理，/challenge 会拿到 503 空响应。
  对 127.0.0.1 的请求一律 httpx.Client(trust_env=False)。

【交付】
A. booking-lab/tools/captcha_solve.py（新文件，纯 CLI，不 import captcha_flow）
   - solve：--image PATH 或 --mock SEED；调 captcha_lab_bridge.solve_canvas()，
     打印 dx / slider_dx / pairs / elapsed_ms / 轨迹点数 / risk / payload JSON
   - bench：--n 30，连跑复刻题，打印 通过 x/30 与最大误差
   - 内核不可用（BridgeUnavailable）：退出码 2，打印具体原因，不静默回退
B. booking-lab/tools/captcha_selftest.py（新文件）
   - 复刻题没起：打印启动命令，退出 3；起了：跑 30 个 seed，断言 30/30 且
     slider_dx 与 /analyze 的 dx 换算差 < 1px，输出 PASS/FAIL 与一行摘要
C. docs/CAPTCHA_SOLVER_TOOL.md：工具定位（人触发、独立于决策链）、三条用法、代理坑、怎么加题

【硬约束】
1. 不改 captcha_flow/ 任何文件；工具不进入自动决策链；
2. 不新增自动点击/自动拖动路径（要拖由既有 captcha_auto 开关控制，不在本任务范围）；
3. 不改 captcha_mock 的判据、容差、接口——它是对拍基准；
4. 不新增第三方依赖；不写真实站点地址。

【验收】
1. cd booking-lab && python -m pytest captcha_flow\tests tests -q   -> 115 passed
2. cd booking-lab && python tools\captcha_selftest.py               -> PASS（含 30/30 与 <1px）
3. cd booking-lab && python tools\captcha_solve.py solve --mock 1000 -> slider_dx≈210.9 + payload
4. git diff --stat                                                  -> 只有约定内的文件
```

---

## 6. P5 扩浏览器扩展侧

```text
【任务】<一句话：例如"扩展侧把手柄选择器补全，覆盖 role=slider 的自定义组件">

【先读】
1) booking-lab/extension/manifest.json   权限声明（当前不含 debugger）
2) booking-lab/extension/content.js       页面内状态机；autoSlider() 走真实指针事件
3) booking-lab/extension/captcha_guard.js  分类与交接（与 captcha_flow 同构）
4) booking-lab/extension/background.js    注入调度 + POST /solve_slider 转发
5) booking-lab/ext_server.py              127.0.0.1:8765 的本地数据服务

【交付】只改扩展侧 JS 与必要的 content JS 选择器；给出改动前后的选择器清单对照表。

【硬约束】
1. 不申请 debugger 权限，不用任何能标记输入来源的调试通道；
2. 事件一律用真实指针事件（pointerdown/pointermove/pointerup）；
3. 失败必须回落 askHuman()，不允许静默重试；
4. 不引入构建步骤与第三方依赖，`node --check` 必须过。

【验收】
1. node --check booking-lab\extension\content.js（其余改动文件同样跑一遍）
2. 本地夹具手测：node booking-lab\captcha_mock\server.js 起 8777，
   pwsh -File booking-lab\launch_ext.ps1 打开页面，日志出现 solve_slider ok=True
3. 贴出改动前后的选择器对照表 + 一次成功的日志片段
```

---

## 7. 底稿：可直接引用的实测数字（2026-11-15，本机）

| 项 | 值 | 命令 |
|---|---|---|
| booking-lab 全量 | **115 passed** | `python -m pytest captcha_flow\tests tests -q` |
| ├ 决策契约 | 30 passed | `python -m pytest captcha_flow\tests\test_captcha_flow.py -q` |
| ├ 交接计时 | 25 passed | `python -m pytest captcha_flow\tests\test_handoff_timing.py -q` |
| └ 端到端 | 3 passed | `python -m pytest tests -q` |
| captcha-lab 全量 | **166 passed** | `python -m pytest -q` |
| 基准样本 | dx=230.50 / slider_dx=210.91 | `python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34` |
| 合成样本精度 | hit@1 96.7% / top3 100% / ~66ms | `python -m solver bench --n 60` |
| 复刻题对拍 | 30/30，误差 max 0.53px | `node captcha_mock/selftest.js`、`python bench_waf.py --n 30` |
| 文案 lint | 0 block | `python tools/wording_lint.py --root .` |

口径常量（写死在提示词里，省得模型猜）：画布 400×330、滑轨 400、手柄 34、range=366、容差 3px。

---

## 8. 模型跑偏时粘这三句

结构固定：**否掉偏移 → 收窄到一个文件 → 给一条可执行命令**。三句以内，不解释。

```text
停。你没有落在交付物上。这次只要一个文件：<具体路径>。
先把它的函数签名和返回值写出来并落盘，再跑 <一条能立刻执行的命令> 把输出贴给我。
```

三种常见偏移与对应话术：

| 现象 | 追加这一句 |
|---|---|
| 只给方案、伪代码、目录树 | 「落盘成可运行文件，并在 tests/ 加一条用合成样本断言的用例。」 |
| 顺手改写了仓库定位 / 加了一段说明性文字 | 「删掉那段说明。README 只保留：安装 / 接口 / 架构 / 实测 / 路线图。」 |
| 反复解释为什么不这么做 | 「不用解释取舍。先做 <一个文件>，跑 <一条命令>。」 |

---

## 9. 为什么这样写（三条，改提示词时别破坏）

1. **产物钉死到文件与命令。** 提示词里出现文件名、函数签名、验收命令，交付物就只有一种形状；
   只写"优化一下识别"时，交付物有无数种形状，其中大部分不是你想要的。
2. **先读、后写，路径必须真实。** 仓库里已经有过改名（旧目录名只剩 `.old`），
   让模型读不存在的路径，它只能凭记忆补，补出来的东西和现场对不上，返工比重写贵。
3. **和仓库既有契约对齐。** 如果提示词要求的行为与 `captcha_flow` 的测试断言冲突，
   模型会先花力气解释这个冲突。把"哪一层允许做什么"写在提示词第一段，冲突就不存在了。

补充一条工程习惯：**每次只交代一个可验收的增量**。四件事一起给，四件都会做成半成品；
一件事做完、验证过、再给下一件，返工率最低。
