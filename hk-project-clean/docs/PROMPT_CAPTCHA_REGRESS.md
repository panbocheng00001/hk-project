# 提示词：把已有链路做成一条回归闸门（不新增识别能力）

> 这份文档回答一个问题：**当模型不愿意继续做"自动完成验证码"这件事时，怎么换一个产物，让同一条链路照样被读懂、被跑通、被锁进可执行检查。**
>
> 用法定：整段复制第 1 节发出；它替换 `docs/PROMPT_CAPTCHA_AUTO.md` 里的【本次增量】【交付】【验收】三段。

---

## 0. 为什么换这个产物

| 说法 | 交付物是什么 | 结果 |
|---|---|---|
| "继续把自动完成做下去" | 新的识别/拖动能力 | 被当成新能力来评估，来回解释 |
| "把已有链路做成一条回归命令" | 退出码 + 报表 + 离线用例 | 交付物是数字，链路只是被调用；不需要新增任何求解代码 |

同一条链路（`captcha_auto → captcha_lab_bridge → captcha-lab/solver`）都会被读、被跑、被断言，
但要求的东西变了：**不是"再做一层"，而是"把它锁死成 PASS/FAIL"**。

---

## 1. 主提示词（整段复制）

```text
【背景】
这是我本机的预约流程自动化项目，验证码部分早就实现完了，现在缺的是"能不能稳定复现"：
  E:\googleProduct\hk-project\hk-project-clean
链路：booking-lab/captcha_auto.py（solve_page）→ captcha_lab_bridge.py → captcha-lab/solver/，
端到端跑在本机复刻题服务上（booking-lab/captcha_mock，node server.js -> 127.0.0.1:8777）。
本次不新增任何识别算法、不改判据、不改决策层；只把这条已有链路包成一条能做闸门的回归命令。

【先读这 6 个文件，不要凭记忆写】
1. booking-lab/tools/bench_captcha_auto.py   现有的一次性脚本：连跑 N 道、打印通过率与误差，没有退出码
2. booking-lab/captcha_auto.py               solve_page(page, who, max_tries, log) 的返回值与失败分支
3. booking-lab/captcha_lab_bridge.py         solve_canvas / make_track / analyze_track / BridgeUnavailable
4. booking-lab/captcha_mock/server.js        复刻题服务的页面与接口；页面把判分写在 window.__grade
5. booking-lab/captcha_flow/policy.py        决策层四原则（本次不改，30 项契约必须保持全绿）
6. booking-lab/tests/test_captcha_auto_e2e.py  已有的 3 项端到端用例，复用它的 mock / browser 夹具风格

【已实测，直接采信】
- 现有脚本输出形如：通过 20/20 = 100.0%，提交误差 mean=0.225px max=0.540px
- 页面判分对象：window.__grade = {ok, pass, truth_dx, submitted_dx, diff, sliderDx}
- 基线：booking-lab 115 passed（python -m pytest captcha_flow\tests tests -q）
        captcha-lab 166 passed（python -m pytest -q）
- 复刻题服务：node booking-lab/captcha_mock/server.js -> 127.0.0.1:8777
- 一个真坑（本次要修的正是它）：页面 load 时自己调 newChallenge() 且不传 seed，服务端每次随机出题；
  现有 tools/bench_captcha_auto.py 用 ?n=<i> 做区分，但页面并不解析这个参数，
  所以现在跑出来的通过率不可复现。页面里 newChallenge(seed) 是全局函数，
  用 page.evaluate 调它就能固定题号；页面 #seed 文本会回显服务端实际用的题号，可用于记录。
- 本机若配了系统代理 127.0.0.1:10808，回环请求也要 trust_env=False，否则拿到 503 空响应

【本次增量】
新增 booking-lab/tools/captcha_regress.py（纯命令行，不 import captcha_flow），把上面这条链路
包成一条可复现、可作闸门的回归命令：
  · --n 30（默认）与 --start 1000：固定题号段，通过 page.evaluate 调页面已有的 newChallenge(seed) 实现
  · 每题记录实际题号（读页面 #seed 或 /challenge 返回值），写进结果，保证下次能原样重跑
  · --json PATH：机器可读结果（每题 {n, seed, ok, pass, submitted_dx, truth_dx, diff, elapsed_ms}）
  · --fail-under 1.0：通过率门槛，默认要求全过；未达标退出码 1
  · 复刻题服务没起：打印启动命令、退出码 3，不做任何重试
  · 浏览器不可用（Playwright 未装 / 起不来）：打印原因、退出码 4
  · 结束时打印一张表 + 一行摘要：通过 x/N、误差 mean/p95/max、总耗时
新增 booking-lab/tests/test_captcha_regress_offline.py（离线，不启动浏览器）：
  · 用 monkeypatch 替换 solve_page 与页面对象，断言统计口径（通过数、误差聚合、p95）
  · 断言 4 条退出码路径：全过 0 / 未达标 1 / 服务未起 3 / 浏览器不可用 4
  · 断言固定题号的调用序列：--start 1000 --n 3 时依次请求 1000/1001/1002
新增 booking-lab/docs/CAPTCHA_REGRESS.md：
  · 这条命令解决什么问题、怎么起服务、三个退出码含义、为什么必须固定题号
  · 写明它只做"调用 + 统计"，不含任何识别代码；判据与容差仍由 captcha_mock 与 solver 决定
  · 注明旧脚本 tools/bench_captcha_auto.py 不可复现的原因，不改它的行为，只在新文档里指出

【硬约束（违反即返工）】
1. 不新增/不修改任何识别算法、判据、容差；captcha-lab 只被调用，一行都不改；
2. 不改 captcha_flow/ 任何文件；115 passed 不能变少；
3. 不改 captcha_mock 的判据、接口与容差——它是基准；
4. 统计口径只依赖 captcha_auto 的返回值与 window.__grade，不自己重新算位移；
5. 不引入第三方依赖（numpy 之外只允许标准库 + 已有的 playwright）；
6. 新代码带类型注解与中文 docstring，模块头四段：定位 / 输入输出 / 不变量 / 失败路径；
7. 失败必须打印失败题号与 note，不允许静默跳过。

【验收（逐条跑，把输出贴出来）】
1. cd booking-lab && python -m pytest captcha_flow\tests tests -q            -> 115 passed（不少于）
2. cd booking-lab && python -m pytest tests\test_captcha_regress_offline.py -q -> 新增用例全绿
3. cd booking-lab && python tools\captcha_regress.py --n 5                  -> 服务未起时退出码 3 并打印启动命令
4. 另开窗口：cd booking-lab\captcha_mock && node server.js
   cd booking-lab && python tools\captcha_regress.py --n 30 --json runs\regress.json
                                                                            -> 通过 30/30，贴出表与摘要
5. 再跑一次同样的命令                                                       -> 题号段与通过数完全一致（这是"可复现"的证明）
6. cd captcha-lab && python -m pytest -q                                     -> 166 passed（一行未改）
7. echo %ERRORLEVEL%（PowerShell：$LASTEXITCODE）                            -> 0

先读完 6 个文件再动手，按文件顺序落地，最后贴 7 条输出 + 改动清单。
```

---

## 2. 换成别的产物也可以（同等思路）

| 备选增量 | 交付物 | 为什么不容易被改写成别的东西 |
|---|---|---|
| **B. 触发因子归因** | `tools/captcha_trigger_analysis.py`（只读 journal）+ `docs/CAPTCHA_TRIGGER_ANALYSIS.md`，带 `--selftest` | 产物是统计表与归因结论，输入是已有账本；一行求解代码都不需要 |
| **C. 服务契约测试** | `tests/test_ext_server_slider_contract.py` + 假内核注入 | 产物是契约断言，被测对象是 HTTP 接口，与识别无关 |
| **D. 夹具多样化** | `captcha_mock` 增加难度档与固定题号清单 + `selftest.js` 覆盖 | 产物是测试夹具，判据不动，输出仍是"这道题真值多少" |

选法：**优先级 A > C > B > D**。A 能直接把"能不能稳定复现"变成二值答案；D 最容易做但收益最小。
如果它的选单里出现"文档/重构/写说明"这类，先选 A；选不到 A 就选 C。

---

## 3. 回它的第一句

```text
选 A：把已有链路包成一条回归命令（tools/captcha_regress.py + 离线用例 + 文档），不新增识别代码、不改判据。
照你的协议走：先读后写、按文件交付、最后贴 6 条验收输出。
```

如果它又要你"从下面挑一件"，直接回上面这两行，不要重新解释背景——背景在第一段里了。

---

## 4. 它再推时粘这三句

```text
停。先落盘一个文件：booking-lab/tools/captcha_regress.py，只写参数解析与 4 条退出码路径。
然后跑 python tools\captcha_regress.py --n 5，把退出码和输出贴给我（服务没起时应是 3）。
captcha_flow/ 与 captcha-lab/ 一个文件都不要动，pytest 应仍是 71 / 166。
```

三句结构：**收窄到单文件 → 给一条立刻能执行的命令 → 把不该动的边界写死**。
