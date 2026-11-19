# AGENTS.md —— 接手契约

> 这是给**任何**要改这个仓库的编码 agent（人或模型）的第一份文件。
> 读完这一页就够了；它指向的文档只在需要细节时再打开。
> 最后一版：2026-11-15。

---

## 1. 一句话定位

两个互不依赖的子项目，同一个操作者在本机使用：

- **`booking-lab/`** —— 预约表单自动化 + 会话管理 + 验证码交接。跑在操作者自己机器上。
- **`captcha-lab/`** —— 滑块验证码识别内核（图片 → 位移 → 轨迹 → 载荷），可独立安装、可单测。

两者的关系只有一个方向：宿主通过 `booking-lab/captcha_lab_bridge.py` 调用内核。

---

## 2. 开工前（按顺序，别跳）

| # | 读什么 | 为什么 |
|---|---|---|
| 1 | `docs/ARCHITECTURE.md` | 两层职责、六组模块、单向依赖 |
| 2 | `docs/AI_CODING_SPEC.md` | 注释/文档/命名规范 + 词表 + 自检命令 |
| 3 | 任务点名的源文件，**用 read 打开，不凭记忆** | 仓库改过名，旧路径没有 `.old` 之外的痕迹 |
| 4 | `docs/PROMPT_PACK.md` §7 | 实测数字与口径常量，直接引用，不重算 |

硬规则（违反即返工）：

1. **先读后写。** 不存在的路径、没读过的函数，不写进代码或文档。
2. **产物钉死。** 交付物是文件、函数、命令输出；方案与伪代码不算交付。
3. **只描述行为。** 注释与文档不写立场、动机、免责、评价；要约束就写成不变量或测试。
4. **目标一律本机。** `example.test` / `127.0.0.1:8777` / `127.0.0.1:8765`。
   真实地址、凭据、个人资料只在本机 `config.json`，不进仓库。
5. **一句话交付一句话说。** 改动清单按文件列，每个文件一句说明。

---

## 3. 不变量（测试守着，改坏会红）

| 不变量 | 位置 | 用例 |
|---|---|---|
| 决策层纯函数，阈值只来自 `Policy` | `captcha_flow/policy.py` | `test_policy_*` |
| 探测层只读 DOM，不点击不发请求 | `captcha_flow/detect.py` | `test_detect_*` |
| 决策层对滑块一律交人；加速层关闭或失败时这就是最终行为 | `captcha_flow/session.py` | `test_slider_never_triggers_automatic_action` |
| 勾选框失败不重试，直接交人 | `captcha_flow/session.py` | `test_checkbox_click_fails_then_human_passes` |
| 探测异常按最保守路径（交人 + 留档） | `captcha_flow/session.py` | `test_detect_exception_is_treated_as_unknown_and_hands_off` |
| 加速层关闭后不再产生任何拖动 | `captcha_auto.py::auto_enabled` | `test_guard_falls_back_when_auto_off` |
| 判据与复刻题基准逐条对齐（0.00px） | `captcha-lab/solver/overlap.py` | `tests/test_overlap.py` |
| 轨迹末点精确等于目标位移，风险分 < 0.5 | `captcha-lab/solver/track.py` | `tests/test_track_gen.py` |
| 轮询有硬下限，且不提供关闭开关 | `throttle.py`、`site_monitor.py` | — |
| 回归闸门固定题号段，同一 `--start/--n` 结果一致 | `tools/captcha_regress.py` | `tests/test_captcha_regress_offline.py` |

新增能力若与其中任何一条冲突，先改文档的"不变量"、再改测试、最后改代码，**同一个提交**。

---

## 4. 往哪放

| 加什么 | 放这里 |
|---|---|
| 识别策略 / 新题型 | `captcha-lab/solver/`（+ `captcha-lab/tests/`） |
| 站点口径（字段名、缩放、答案模式） | `captcha-lab/solver/profiles.py` |
| 页面形态识别（只读） | `booking-lab/captcha_flow/detect.py` |
| 交接提醒 / 耗时统计 | `booking-lab/captcha*.py` |
| 一次性排查脚本 | `booking-lab/tools/` |
| 本地服务接口 | `booking-lab/ext_server.py`（仅回环） |

不要新建顶层目录；不要在两个 lab 之间加反向依赖；不要把脚本丢在仓库根。

---

## 5. 完成定义（DoD）

```bash
python tools/wording_lint.py --root .                        # 0 block
pwsh -File tools/check.ps1                                   # lint + 115 + 166 全绿
# 没有 pwsh（Windows PowerShell 5.1 环境）时等价写法：
#   powershell -ExecutionPolicy Bypass -File tools\check.ps1
```

四条同时满足才算完成：

1. 文件已落盘，`read` 能打开，`import` 不报错；
2. 上面两条命令的输出已贴出（不是"应该能过"）；
3. 新代码有类型注解与中文 docstring，模块头四段齐全（定位 / 输入输出 / 不变量 / 失败路径）；
4. 改动清单按文件列出，每个文件一句话。

---

## 6. 冲突怎么判

优先级从高到低：

```
运行时行为  >  测试断言  >  docs/ 描述  >  代码注释  >  记忆
```

- 文档与代码冲突 → 以代码为准，**当次**改文档。
- 测试与代码冲突 → 先确认测试是不是在守一条还要保留的不变量；
  要改契约就按 §3 的三步走，不要直接把断言删了。
- 记忆与仓库冲突 → 以仓库为准。这个仓库改过名（旧目录只剩 `.old`），记忆里的路径不可信。

---

## 7. 常犯的五件事

1. 凭记忆写路径 → 读到空文件 → 自己补一套实现，与现场对不上。
2. 只给方案不给文件 → 看起来完成了，其实一行没落盘。
3. 顺手在 README 加一段说明性文字 → 与代码说法不一致，下一个人要重新判断。
4. 为了让结果好看，放宽容差/阈值 → 基准失效，回归数字全部作废。
5. 失败时静默回退 → "看起来接好了其实没接"。回退必须打日志说明原因。
