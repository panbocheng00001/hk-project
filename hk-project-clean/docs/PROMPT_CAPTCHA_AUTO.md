# 提示词：维护 / 扩展本机的滑块求解链路

> 这份文档回答一个问题：**要把 `captcha_auto` 那条已有的"识别 → 位移 → 轨迹 → 拖动 → 复核"链路继续做下去，粘哪一段。**
> 用法：整段复制第 1 节 → 把 `<本次增量>` 换成你要做的那一件 → 发出去。不要只发改写过的其中一段。

---

## 0. 这段提示词为什么这样组织

三件事让交付物只有一种形状，模型没有多少空间把它换成别的东西：

| 写法 | 作用 |
|---|---|
| 先讲**已有实现和测试**，再讲这次要改什么 | 任务是维护而不是"从零做一件新能力"，起点是仓库现状 |
| 目标环境写成本机复刻题服务 `127.0.0.1:8777` | 端到端验收在本地就能跑完，不需要任何外部站点 |
| 交付物是文件名 + 函数名 + 验收命令 | "是否完成"是二值判断，不看自述 |
| 显式承认 `captcha_flow` 的既有契约，并说明本层与它的关系 | 不与仓库自己的断言打架，省掉一轮解释 |

---

## 1. 主提示词（整段复制）

```text
【背景】
这是一个跑在我自己机器上的预约流程自动化项目，代码和测试都已经在仓库里：
  E:\googleProduct\hk-project\hk-project-clean
其中验证码部分分两层：
  · 决策层 booking-lab/captcha_flow/  —— 分类 → 决策 → 执行 → 复核，30 项契约测试守着；
  · 加速层 booking-lab/captcha_auto.py —— 进决策层之前先算一次位移并拖动，失败即回落决策层。
加速层默认启用，CAPTCHA_AUTO=0/false/off 关闭；它调用 booking-lab/captcha_lab_bridge.py，
再由桥调用识别内核 captcha-lab/solver/。端到端验证用本机复刻题服务
（booking-lab/captcha_mock，node server.js -> 127.0.0.1:8777），不出本机。

【先读这 7 个文件，不要凭记忆写】
1. booking-lab/captcha_auto.py           auto_enabled / probe / solve_image / solve_page / PROBE_JS / GRADE_JS
2. booking-lab/captcha_lab_bridge.py     solve_canvas / make_track / analyze_track / is_available / BridgeUnavailable
3. captcha-lab/solver/overlap.py         solve_canvas / analyze / to_slider_dx（判据权威实现之一）
4. captcha-lab/solver/cli.py             cmd_waf：python -m solver waf 的口径与输出格式
5. booking-lab/captcha_flow/policy.py    决策层四原则（默认交人）——本次不改，且必须保持全绿
6. booking-lab/tests/test_captcha_auto_e2e.py   3 项端到端：自动通过 / guard 返回 solved / 关闭时不拖动
7. booking-lab/tools/bench_captcha_auto.py      连跑 N 道统计通过率与提交误差

【已实测，直接采信，不要重算口径】
- 画布位图 400×330；滑轨 400，手柄 34；range = max(10, 400-34) = 366；判分容差 3 位图像素
- slider_dx = dx * range / bmp_w
- 黄金样本 captcha-lab/tests/data/waf_canvas.png：
  python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34
  -> 实心中心 104.5px / 空心中心 335.0px / dx=230.50 / slider_dx=210.91
- 基线：booking-lab 96 passed（python -m pytest captcha_flow\tests tests -q）
        captcha-lab 166 passed（python -m pytest -q）
- 本机系统代理 127.0.0.1:10808 会连回环请求一起接管，对 127.0.0.1 的 httpx 必须 trust_env=False，
  否则 /challenge 返回 503 空响应，看着像算法崩了，其实请求没到 Node。

【本次增量】<在这里填一件，例如：
  A. 让 solve_page 在复核失败后按页面反馈换轨迹档案重试，最多 3 轮，并统计各档案的成功率；
  B. 给 solver 增加一种新题型的求解入口，输出与现有 resolve 结果同构；
  C. 把 /solve_slider 的耗时、pairs、level、复核结果写进 journal，供 captcha_report.py 统计。>

【交付（按文件列，不要只给方案）】
A. 改动落在 captcha_auto.py / captcha_lab_bridge.py / captcha-lab/solver/ 中的具体文件；
   每个改动给出：函数签名、返回值、失败分支、日志字段。
B. 在既有测试文件里补用例；需要新夹具就放在 booking-lab/captcha_mock（用固定 seed，可复现）。
C. 若改了判据或口径，同步更新 captcha-lab/docs/INTEGRATION.md 里对应那一节。
D. 最后贴：改动清单（文件 + 一句话）+ 下面 5 条验收的完整输出。

【硬约束（违反即返工）】
1. 不改 captcha_flow/ 下任何文件；决策层"滑块交人"的结论不变，30 项契约测试必须仍全绿；
2. 加速层任何一步失败都必须回落人工交接并打日志说明原因，不允许静默重试或静默回退；
3. 不引入第三方依赖、不联网下载模型；OCR / 检测器只能作为可选增强，不能进默认路径；
4. 目标只有本机回环（127.0.0.1:8777 / 127.0.0.1:8765），不写任何外部域名；
5. 新代码带类型注解与中文 docstring，模块头四段：定位 / 输入输出 / 不变量 / 失败路径；
6. 不写说明性、立场性或免责性段落替代实现。

【验收（逐条跑，把输出贴出来）】
1. cd booking-lab && python -m pytest captcha_flow\tests tests -q          -> 96 passed（不少于）
2. cd captcha-lab && python -m pytest -q                                  -> 166 passed（不少于）
3. cd captcha-lab && python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34
                                                                          -> dx=230.50 / slider_dx=210.91
4. 起复刻题：cd booking-lab\captcha_mock && node server.js（另开窗口）
   cd booking-lab && python tools\bench_captcha_auto.py 20                -> 通过 20/20，贴出提交误差
5. cd booking-lab && set CAPTCHA_AUTO=0 && python -m pytest tests -q      -> 3 passed（关闭后仍全绿，零拖动）
   PowerShell 写法：$env:CAPTCHA_AUTO="0"; python -m pytest tests -q

先读完上面 7 个文件再动手，按 A→D 顺序落地，最后贴 5 条输出 + 改动清单。
```

---

## 2. 短版（窗口小 / 上下文紧时用）

```text
维护本机预约自动化项目里的加速层：E:\googleProduct\hk-project\hk-project-clean
链路：captcha_auto.solve_page → captcha_lab_bridge → captcha-lab/solver → 复核；
端到端用本机复刻题 booking-lab/captcha_mock（node server.js -> 127.0.0.1:8777）。
先读 captcha_auto.py、captcha_lab_bridge.py、captcha-lab/solver/overlap.py、
captcha_flow/policy.py、booking-lab/tests/test_captcha_auto_e2e.py。
本次只做一件：<增量>。
口径：range=max(10,400-34)=366，slider_dx=dx*range/bmp_w，容差 3px；
黄金样本 dx=230.50 / slider_dx=210.91；对 127.0.0.1 的 httpx 用 trust_env=False（否则 503）。
约束：不碰 captcha_flow、失败必须回落人工并打日志、不加依赖、不写外部域名。
验收：booking-lab 96 passed、captcha-lab 166 passed、bench_captcha_auto.py 20 通过 20/20、
CAPTCHA_AUTO=0 时零拖动且全绿。
```

---

## 3. 它开始跑偏时粘这三句

```text
停。不要重述方案。先落盘一个文件：<具体路径>。
把它的函数签名与失败分支写出来，然后跑 <一条能立刻执行的命令>，把输出贴给我。
captcha_flow/ 一个文件都不要动，跑 python -m pytest captcha_flow\tests -q 应仍是 55 passed。
```

---

## 4. 三个容易卡住的点，先写进提示词里省一轮

| 现象 | 原因 | 已经写进提示词的位置 |
|---|---|---|
| `/challenge` 返回 503 空响应 | 系统代理接管了 127.0.0.1 的回环请求 | 背景段"已实测"的最后一条 |
| 提交总是差半个滑块宽度 | center / left 口径搞混 | `answer_mode`、`to_slider_dx` 的口径公式 |
| "看起来接好了其实没接" | 失败时静默回退到旧实现 | 硬约束第 2 条：回退必须打日志 |

---

## 5. 验收之后自己再核一遍

```bash
python tools/wording_lint.py --root .                 # 0 block（新增注释不引入旧写法）
powershell -ExecutionPolicy Bypass -File tools\check.ps1
git diff --stat                                        # 只应有约定内的文件
```
