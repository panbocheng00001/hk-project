# CAPTCHA_HANDOFF_SLA —— 验证码"出现 → 人工完成"交接的节奏与度量

本文只覆盖交接这一段：状态机判定 `HAND_TO_HUMAN` 之后，怎么喊人、喊几次、
等多久、怎么记、怎么看。决策本身（哪些形态交人）见 `CAPTCHA_DESIGN.md`，未改动。

---

## 1. 时间轴

```
t_appear                     首次 handoff 事件（policy 判定交人的那一刻，蜂鸣/推送之前）
  ├─ 抬窗口 → 首次提醒 → 抬窗口
  ├─ +45s     第 1 次再提醒（抬窗口 → 提醒 → 抬窗口）
  ├─ +117s    第 2 次再提醒
  ├─ ...      间隔按 1.6 倍递增，单次间隔封顶 120s，最多 5 次
  └─ 人工完成 → passed_human，立即返回，不再提醒、不再抬窗口
     或 t_appear + human_wait_s → timeout
```

- `waited_s` = 交接结束时刻 − `t_appear`，从交接开始算，不含之前等 token、点勾选框的时间。
- 交接截止 = `min(调用方硬 deadline, t_appear + human_wait_s)`。

## 2. 参数（`captcha_flow/policy.py::Policy`）

| 字段 | 默认 | 含义 |
|---|---|---|
| `human_wait_s` | 180 | 交接后最多等多久 |
| `renotify_every_s` | 45 | 第 1 次再提醒距交接的间隔；0 = 只提醒一次 |
| `renotify_backoff` | 1.6 | 第 k 次间隔 = `renotify_every_s × backoff^(k-1)` |
| `renotify_max_n` | 5 | 再提醒次数上限（不含首次）；0 = 不再提醒 |
| `poll_s` | 1.5 | 检查"人是否已完成"的轮询间隔，也是所有耗时的测量精度 |

单次间隔封顶 `RENOTIFY_CAP_S = 120s`（`session.py`）；若 `renotify_every_s` 本身大于 120，以它为准。
下一次提醒按计划时刻排（不随轮询抖动漂移）；若进程卡顿错过了计划时刻，从当前时刻重排，不连发补提醒。

默认参数下的再提醒时刻（相对 `t_appear`）：

| k | 间隔 | 累计 |
|---|---|---|
| 1 | 45s | 45s |
| 2 | 72s | 117s |
| 3 | 115.2s | 232.2s |
| 4 | 120s（封顶） | 352.2s |
| 5 | 120s（封顶） | 472.2s |

`human_wait_s=180` 时实际只会响前两次（45s、117s）。旧实现是固定 45s 间隔，180s 内响 3 次（45/90/135s）。

本地提醒通道：

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `CAPTCHA_BEEP_TIMES` | 6 | 每次提醒的蜂鸣次数（0–20，0 = 静音） |
| `CAPTCHA_BEEP_HZ` | 1400 | 蜂鸣音调（37–32767） |

`captcha.focus_browser_window()` 只对最小化窗口做 `SW_RESTORE`（不会把最大化窗口缩回普通尺寸），
然后 `SetForegroundWindow` + `FlashWindow`；任何一步失败都返回 False，不抛异常。

## 3. 记录（journal）

由 `captcha.guard()` 写入 `runs/journal/YYYY-MM-DD.jsonl`，`src=captcha`：

| ev | 字段 | 时机 |
|---|---|---|
| `renotify` | `who, kind, n, waited_s` | 每次再提醒 |
| `handoff_done` | `who, kind, waited_s, result, renotify_n, timeout` | 交接结束（`result` 为 `solved` / `timeout`） |

没有发生交接（无验证码 / 自动通过）时不写 `handoff_done`。
写入失败（磁盘、权限、journal 初始化异常）只经 `say` 打一行日志，`guard()` 返回值不受影响。

## 4. 报表口径与命令

| 参数 | 用途 |
|---|---|
| `--days N` | 读取最近 N 天（含今天）的 `YYYY-MM-DD.jsonl`，默认 7 天 |
| `--dir DIR` | 指定 journal 目录，默认 `runs/journal` |
| `--json PATH` | 把与终端表格同源的统计结构写成 UTF-8 JSON，父目录不存在时创建 |
| `--selftest` | 用构造事件流检查统计函数，打印 27 项结果与 PASS/FAIL |

```powershell
python captcha_report.py --days 7
python captcha_report.py --dir tests\fixtures\journal --days 7
python captcha_report.py --dir tests\fixtures\journal --json runs\report.json
python captcha_report.py --selftest
```

终端表格与 JSON 都来自 `summarize()`：交接总次数（其中滑块）、人工完成率、
完成耗时 P50/P90/max、超时次数、平均再提醒次数、按 kind 分组、按天趋势。
JSON 固定输出 `days / n / solved / timeout / rate / p50 / p90 / max /
avg_renotify / slider / by_kind / by_day`。

统计口径：
- 完成率 = `result=solved` 的条数 / 全部 `handoff_done` 条数。
- P50/P90/max 只取 `result=solved` 且 `waited_s` 为有限数值的记录；超时单列，不进入耗时序列。
- 分位数先按 `waited_s` 升序排列，再取位置 `(样本数 - 1) × q`；位置落在两个样本之间时线性插值，`q` 分别为 0.5 和 0.9。
- `avg_renotify` = 全部 `handoff_done.renotify_n` 之和 / 交接总次数；缺失或非数值按 0。
- 坏 JSON 行、非 captcha 事件与空行跳过；读不到 journal 或窗口内无记录时打印“无数据”，退出码 0。

## 5. 目标值（初始，按报表数据校准）

| 指标 | 目标 | 超出时先查 |
|---|---|---|
| 完成耗时 P50 | ≤ 30s | 首次提醒是否被听到（蜂鸣次数/音量、钉钉是否送达） |
| 完成耗时 P90 | ≤ 90s | 第 1、2 次再提醒是否生效；人是否在工位 |
| 超时率 | ≤ 5% | `human_wait_s` 是否太短；是否多窗口同时交接 |
| 平均再提醒次数 | ≤ 1.0 | 首次提醒的到达率 |

## 6. 测试

`captcha_flow/tests/test_handoff_timing.py`，假时钟 + `FakePage` 子类，不 sleep、不开浏览器：

1. `waited_s` = 人工完成时刻 − 首次 handoff 时刻（容差 `poll_s`），含"勾选框失败后再交人"的非零起点；
2. 再提醒次数 = `min(renotify_max_n, 计划时刻早于完成时刻的个数)`，触顶后不再提醒；
3. 人工完成后不再提醒、不再抬窗口（含完成时刻恰好等于计划提醒时刻的边界）；
4. 每次提醒前后各一次 `bring_to_front`；
5. 超时返回 `TIMEOUT`，`waited_s ∈ [human_wait_s, human_wait_s + poll_s)`；
6. journal 写入抛异常时流程照常返回，只留日志。
