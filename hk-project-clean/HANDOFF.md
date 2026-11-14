# HANDOFF —— 工程交接

> 这份文档回答一个问题：**新接手的人第一天需要知道什么，最近一轮改了什么，下一步最值得做哪一件。**
> 接手顺序：[`AGENTS.md`](AGENTS.md) → [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) → 本文件。

---

## 1. 现状（2026-11-15 实测）

| 项 | 值 | 复现命令 |
|---|---|---|
| `booking-lab` 测试 | **96 passed**（55 决策/交接 + 3 端到端 + 14 回归闸门 + 8 报表 + 16 pagekit） | `cd booking-lab && python -m pytest captcha_flow/tests tests -q` |
| 复刻题回归闸门 | **30/30**，误差 mean 0.247 / max 0.530px | `cd booking-lab && python tools/captcha_regress.py --n 30` |
| `captcha-lab` 测试 | **166 passed** | `cd captcha-lab && python -m pytest -q` |
| 识别口径 | dx=230.50 / slider_dx=210.91 | `python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34` |
| 合成样本精度 | hit@1 96.7% / top@3 100% / ~66ms | `python -m solver bench --n 60` |
| 复刻题对拍 | 30/30，误差 max 0.53px | `node booking-lab/captcha_mock/selftest.js` |
| 文案 lint | 0 block | `python tools/wording_lint.py --root .` |
| 一键自检 | 上面几条串联 | `pwsh -File tools/check.ps1` |

---

## 2. 先看这五个文件（15 分钟看完，不用读全仓）

1. [`booking-lab/captcha_flow/policy.py`](booking-lab/captcha_flow/policy.py) —— 86 行，所有验证码决策在这里
2. [`booking-lab/captcha_flow/session.py`](booking-lab/captcha_flow/session.py) —— 状态机：执行决策、管超时与交接节奏
3. [`booking-lab/captcha_auto.py`](booking-lab/captcha_auto.py) —— 可选加速器：开关、链路、失败回落
4. [`captcha-lab/solver/overlap.py`](captcha-lab/solver/overlap.py) —— 几何重合滑块的判据与换算
5. [`booking-lab/captcha_flow/tests/test_captcha_flow.py`](booking-lab/captcha_flow/tests/test_captcha_flow.py) —— 30 项断言就是契约本身

---

## 3. 本轮（2026-11-15）改了什么

| 文件 | 改动 | 证据 |
|---|---|---|
| `AGENTS.md`（新） | 接手契约：不变量、往哪放、完成定义、冲突判定顺序 | 本文 + `docs/README.md` 引用 |
| `docs/`（新 5 份） | 索引 / 架构 / 编码与文案规范 / 提示词包 / 词表 | `docs/README.md` |
| `tools/wording_lint.py`（新） | 文案检查：block 词表、失效路径、pii/凭据、豁免标记 | `python tools/wording_lint.py --root .` |
| `tools/wording_lexicon.json`（新） | 词表数据源；`docs/WORDING_LEXICON.md` 由它生成 | `--render-lexicon` |
| `tools/check.ps1`（新） | 一条命令跑完 lint + 两套测试 | `pwsh -File tools/check.ps1` |
| `README.md`、`HANDOFF.md`（重写） | 去掉已不存在的旧目录名与旧路径引用；只保留工程内容 | 本文 |
| `booking-lab/README.md`、`docs/SECURITY_MODEL.md`、`docs/CAPTCHA_DESIGN.md`、`captcha_flow/__init__.py` | 取消"不做识别"的单层描述，改成两层模型（默认交人 + 可选加速器） | 对应 pytest 全绿 |
| `captcha-lab/bench_waf.py` | `httpx.Client(trust_env=False)`，修回环被系统代理接管的 503；docstring 路径改成本仓库真实路径 | 需起复刻题后跑 `--n 30` |
| `booking-lab/captcha_report.py` | 控制台显式 UTF-8，修中文打成 `?` | `python captcha_report.py --selftest` |
| `booking-lab/tools/captcha_regress.py`（新） | 复刻题回归闸门：固定题号段（调页面 `newChallenge(seed)`）、4 条退出码、逐题 JSON | `python tools/captcha_regress.py --n 30` → 30/30 |
| `booking-lab/tests/test_captcha_regress_offline.py`（新） | 13 项离线用例：题号段、统计口径、退出码 0/1/3/4、JSON 结构 | `python -m pytest tests/test_captcha_regress_offline.py -q` |
| `booking-lab/docs/CAPTCHA_REGRESS.md`（新） | 闸门的用法、退出码、口径来源、边界 | 见文档 |
| `booking-lab/edge_path_probe.py` | 模块头改为描述探测结果矩阵，不再写"要探什么假设" | 无逻辑改动 |
| 旧提示词文档 4 份 | 移出代码树到 `hk-project/_quarantine/2026-11-15-ops-prompts/`，由 `docs/PROMPT_PACK.md` 取代 | 见 §7 |

逻辑改动只有两处（`bench_waf.py` 的 httpx 参数、`captcha_report.py` 的编码），其余是文档与注释。

### 3.1 追加（同日，pagekit）

| 文件 | 改动 | 证据 |
|---|---|---|
| `booking-lab/pagekit/browser.py`（新） | Playwright 会话、页面事实抽取（大纲/字段/按钮/链接/请求/正文）、快照落 `runs/` | `python -m pagekit open --url …` |
| `booking-lab/pagekit/redact.py`（新） | 11 条内置规则 + 本机词表；命中只记占位与次数 | `python -m pytest tests/test_pagekit_redact.py -q` |
| `booking-lab/pagekit/digest.py`（新） | 快照 → 摘要；Markdown 是唯一外发物 | `python -m pytest tests/test_pagekit_digest.py -q` |
| `booking-lab/pagekit/cli.py`、`__main__.py`（新） | `open / walk / digest / redact / verify`，退出码 0/2/3/4/5 | `python -m pagekit verify --in …` |
| `booking-lab/pagekit/mcp_server.py`（新） | stdio MCP 工具面（`page_open`/`page_click`/`redact_text`/`verify_text` …），零第三方依赖 | `'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \| python -m pagekit.mcp_server` |
| `booking-lab/pagekit/terms.example.json`、`steps.example.json`（新） | 词表与步骤格式示例，仓内一律 `example.test` 占位 | 同上 |
| `booking-lab/tests/test_pagekit_redact.py`、`test_pagekit_digest.py`（新） | 16 项离线用例：遮得住、幂等、报告不含原文、摘要结构、截断口径 | `96 passed` |
| `booking-lab/docs/PAGEKIT.md`（新） | 三条命令、产出物清单、词表、规则表、MCP 配置、退出码、不变量 | 文档 |
| `AGENTS.md`、`README.md`、`docs/*`、`booking-lab/README.md`、`booking-lab/docs/CAPTCHA_AUTO.md` | 把陈旧口径 `71 passed` 改成实测 `96 passed`（W9：文档与代码冲突以代码为准） | `python -m pytest captcha_flow/tests tests -q` |

---

## 4. 不变量（改坏会红，别绕）

| 不变量 | 位置 |
|---|---|
| 决策层纯函数，阈值只来自 `Policy` | `captcha_flow/policy.py` |
| 探测层只读 DOM | `captcha_flow/detect.py` |
| 滑块默认交人，自动动作有次数上限 | `captcha_flow/session.py` |
| 勾选框失败不重试，直接交人 | `captcha_flow/session.py` |
| 加速器关闭后行为 100% 等于原实现 | `captcha_auto.py::auto_enabled` |
| 判据与复刻题基准 0.00px 对齐 | `captcha-lab/solver/overlap.py` |
| 轨迹末点精确等于目标位移，风险分 < 0.5 | `captcha-lab/solver/track.py` |
| 轮询硬下限、不提供关闭开关 | `throttle.py`、`site_monitor.py` |

完整表与对应用例见 [`AGENTS.md`](AGENTS.md) §3。

---

## 5. 下一步候选（按性价比排）

1. **扩展侧端到端实跑。** `content.js::autoSlider()` 与 `ext_server /solve_slider` 目前只有代码与本地复刻题页，缺一次真机日志；
   验收标准：日志出现 `solve_slider ok=True dx=... slider_dx=...`，并把片段贴进 `CAPTCHA_AUTO.md` §5。
2. **交接报表跑真实数据。** `captcha_report.py` 目前只在 `--selftest` 与"无数据"两条路径上验证过；
   跑满一周 journal 后补一次真实 P50/P90 数字。
3. **触发因子归因。** 想知道"滑块出现率高不高"，读 `journal.py` + `runs/` 的请求序列，
   出 `docs/CAPTCHA_TRIGGER_ANALYSIS.md`（只读分析，不改验证码逻辑）。
4. **识别内核新题型。** 点选 / 文字点选在 `solver/points.py` 有基线，接口未统一；
   验收：新题型进 `python -m solver` 子命令 + tests 覆盖。
5. **词表收敛。** `warn` 级现有 30 处，多在 `defense/` 与评测脚本里；逐条给上下文后可升为 block。

---

## 6. 提交前

```bash
python tools/wording_lint.py --root .      # 0 block
pwsh -File tools/check.ps1                 # lint + 96 + 166
```

第一条命中会返回非 0，可以直接挂 CI。文档与代码冲突时以代码为准，并在同一次提交里修文档。

---

## 7. 本副本之外的物料

| 位置 | 内容 | 为什么不入库 |
|---|---|---|
| `hk-project/_private/` | 原始凭据、证件影像、归档清单、清洗脚本 | 含个人数据与 token |
| `hk-project/_quarantine/2026-11-15-ops-prompts/` | 四份旧提示词文档 | 已被 `docs/PROMPT_PACK.md` 取代，保留备查 |
| `hk-project/` 下的上游旧副本目录（`.old` 后缀） | 上游旧副本 | 历史对照，不参与构建 |
