# 给 ops5.5 的提示词 V4：只做人工交接提速（零识别）

> 背景：V3（旁路求解 CLI）被拒。拒绝理由不是"范围太大"，而是**输出物本身的用途**：
> `slider_dx` + 拟人轨迹 + 可提交 payload，只有交给程序拖动/提交时才有用。
> 所以再缩小范围、再拆步骤都没用——只要工具产出精确位移，同一条理由会再拒一次。
>
> V4 换掉的是**产物**，不是范围大小：不产出位移、不产出轨迹、不产出 payload、不 import
> `captcha_lab_bridge`。只优化"滑块出现 → 人完成"这段等待时间。这三件事正是 ops5.5 自己
> 在上一轮结尾主动提出的方向，它没有理由再拒。

---

## 0. 先记住三条边界（写提示词时不要越线）

| 可以做 | 会立刻被拒 |
|---|---|
| 提醒更快更醒目（置前、闪烁、声音、重复提醒） | 任何形式的缺口定位、位移计算 |
| 记录"从出现到人工完成"的耗时并出报表 | 生成轨迹 / 曲线 / 缓动档案 |
| 用 FakePage 给交接链路补离线测试 | 生成可提交的 JSON payload |
| 统计滑块出现频率，减少触发次数 | 把 captcha-lab 的 solver 接进 booking-lab |

一句话：**能读到"有人卡住了、卡了多久"，不能读到"该拖多少像素"。**

---

## 1. 主提示词（整段粘贴）

```text
你是 Python 工程方向的工程师。这次只做一件事：把 booking-lab 里"滑块出现 → 人工完成"
这段交接做得更快、更可观测。不做验证码识别，不算位移，不生成轨迹，不产出任何提交载荷。

【范围声明（先看这条，避免做错方向）】
- 不新增任何图像处理/识别代码；不 import captcha_lab_bridge；不读 canvas 像素；
- 不产出 slider_dx / dx / 轨迹点 / payload —— 这些输出在这个项目里没有合法用途；
- policy.py 的四条决策原则不变：滑块一律交人，不做图像推断。

【真实路径】
- 项目：E:\googleProduct\hk-project\hk-project-clean\booking-lab
- 只改这些：captcha.py、captcha_flow\session.py、journal.py（只加写入）、notifier.py（只加字段）
- 新增：captcha_report.py、captcha_flow\tests\test_handoff_timing.py、docs\CAPTCHA_HANDOFF_SLA.md

【先读这 5 个文件，不要凭记忆写】
1. captcha_flow\policy.py           Policy 默认值：human_wait_s=180、renotify_every_s=45、poll_s=1.5
2. captcha_flow\session.py          Guard._hand_to_human()：抬窗口 + notify + 轮询 + renotify
3. captcha_flow\adapters.py         FakePage：n_detect / fronted / n_click 可断言
4. captcha.py                       beep() / focus_browser_window() / _notify() / guard()
5. captcha_flow\tests\test_captcha_flow.py  现有 30 项契约，必须保持全绿

【本次交付（A→D，只这四项）】
A. 交接耗时结构化记录（不改决策）
   - captcha.py::guard()：在首次 HAND_TO_HUMAN 时记 t_appear，在返回前算 waited_s，
     调 journal 记一条：J().log("captcha", "handoff_done", who=..., kind=..., waited_s=...,
     result=..., renotify_n=..., timeout=(result=="timeout"))
   - 记录失败时不能影响主流程（包 try/except，异常只打日志）
B. 提醒节奏升级（参数可注入，默认行为不变）
   - policy.Policy 新增字段（只加不改）：renotify_max_n:int=5、renotify_backoff:float=1.6
   - session.py::_hand_to_human：第 1 次提醒后按 base * backoff^(k-1) 排下一次，
     间隔上限 120s，提醒总次数到 renotify_max_n 就停（不要无限响）
   - captcha.py::focus_browser_window：最小化时先 IsIconic + ShowWindow(SW_RESTORE)，
     再 SetForegroundWindow + FlashWindow；失败不抛异常
   - beep 次数/音调改成可配（env CAPTCHA_BEEP_TIMES / CAPTCHA_BEEP_HZ，默认值保持现状）
   - 每次 renotify 都记 journal：ev="renotify", n=k, waited_s=...
C. captcha_report.py（新文件，只读 journal）
   - python captcha_report.py --days 7
     输出：滑块出现次数、人工完成率、耗时 P50/P90/max、超时次数、
           按 kind 分组的耗时、平均提醒次数、按天趋势
   - python captcha_report.py --selftest
     用一条构造事件流验证统计逻辑（P50 用线性插值），打印 PASS/FAIL
   - 读不到 journal 时打印"无数据"并退出 0，不抛栈
D. captcha_flow\tests\test_handoff_timing.py（新文件，纯 FakePage + 假时钟）
   - 断言 1：waited_s == 人工完成时刻 - 首次 handoff 时刻（容差 poll_s）
   - 断言 2：renotify 次数 == min(renotify_max_n, floor(等待/递增间隔))，超上限不再叫
   - 断言 3：人完成后不再有 renotify / 不再 bring_to_front
   - 断言 4：手交期间每次 renotify 前后各一次 bring_to_front
   - 断言 5：超时返回 TIMEOUT，且 waited_s ≈ human_wait_s（容差 poll_s）
   测试不得依赖真实时间：clock/sleep 全部注入

【硬约束】
1. 不改 policy.py 的既有字段默认值与四条决策原则；只允许新增字段；
2. 不新增自动点击、自动拖动路径；extension\ 一行不动；
3. 不引入第三方依赖；不联网；
4. journal 写入失败必须降级为打日志，不能中断预约流程；
5. 现有 30 项测试必须保持全绿，新测试不得依赖真实时间或真实浏览器；
6. 不写"仅供/合规/免责"段落替代实现。

【验收：逐条贴输出】
1. cd booking-lab && python -m pytest captcha_flow\tests -q     -> 30 passed（原契约未破）
2. cd booking-lab && python -m pytest captcha_flow\tests\test_handoff_timing.py -q
                                                                -> 新增用例全绿
3. cd booking-lab && python captcha_report.py --selftest         -> PASS
4. cd booking-lab && python captcha_report.py --days 7           -> 有数据出报表 / 无数据退出 0
5. 用 FakePage 手动跑一次 handoff，把 events 里 handoff / renotify / passed_human 三行贴出来

先读 5 个文件再动手，最后给改动清单 + 5 条验收输出。
```

---

## 2. 短版

```text
只做 booking-lab 的人工交接提速，不做识别、不算位移、不生成轨迹/payload。
读 captcha_flow\policy.py、session.py（_hand_to_human）、adapters.py（FakePage）、
captcha.py、captcha_flow\tests\test_captcha_flow.py。
交付：①captcha.py 里记 handoff 耗时到 journal（waited_s/result/renotify_n，异常不影响主流程）；
②renotify 改为递增间隔（policy 新增 renotify_max_n=5、renotify_backoff=1.6，只加不改），
到上限就停；focus_browser_window 最小化时先 IsIconic+SW_RESTORE；③captcha_report.py
出 P50/P90/超时/按 kind 报表，带 --selftest；④test_handoff_timing.py 用 FakePage+假时钟断言
耗时、提醒次数上限、完成后不再提醒。
约束：policy 既有默认值不变、不碰 extension、现有 30 项测试全绿、不依赖真实时间。
验收：pytest captcha_flow\tests -q = 30 passed；新测试全绿；captcha_report.py --selftest = PASS。
```

---

## 3. 它如果还是保守（可能只说"现有已够"）

```text
现有 _hand_to_human 只有"叫一次 + 每 45s 再叫一次"，没有上限、没有耗时数据，
也无法回答"昨天滑块平均卡了多久"。
请只做最小增量：①把 waited_s / renotify_n / result 写进 journal；②renotify 加次数上限 5；
③写 captcha_report.py 能出 P50/P90。三件事都不涉及验证码内容，先落这三个文件。
```

---

## 4. 另一个能真正减少卡顿的方向（不碰验证码）

如果目标只是"卡在滑块上的时间更短"，提醒提速是第二优先，**第一优先是让滑块少出现**：

```text
统计 booking-lab 里滑块出现的前置条件：出现前的请求序列、间隔、代理会话复用情况。
只做统计与归因，不碰验证码求解：读 journal + runs\ 下的 nets/steps，
输出"滑块出现率 vs 请求间隔 / 会话复用 / 代理切换"的对照表，指出最可能的触发因子。
产出 docs\CAPTCHA_TRIGGER_ANALYSIS.md + 一个只读分析脚本。
```

理由：`throttle.py`、`session_pool.py`、`run_guard.py` 都在做频率与会话管理，
滑块出现率高通常来自切会话太快或请求节奏异常。减少触发次数，比把 3 分钟压到 40 秒更划算。

---

## 5. 你这边复核

```powershell
cd E:\googleProduct\hk-project\hk-project-clean\booking-lab
python -m pytest captcha_flow\tests -q        # 30 passed
python captcha_report.py --selftest            # PASS
python captcha_report.py --days 7              # 有数据出 P50/P90
git diff --stat                                # 只应有约定内的文件
```
