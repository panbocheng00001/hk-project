# AI 编码与文案规范 v1.0

> 这份文档回答一个问题：**下次让模型（或人）改这个仓库时，新写出来的代码、注释、文档应该长什么样，才不需要任何人回头重新解释一遍意图。**
>
> 生效方式：`AGENTS.md` 第 2 条要求开工前读这一份；`tools/wording_lint.py` 把它里面可以机械检查的部分变成一条命令。
> 本版基线：2026-11-15，`booking-lab` 115 passed、`captcha-lab` 166 passed。

---

## 0. 适用范围

| 范围 | 是否受约束 |
|---|---|
| 仓库内所有 `.py` / `.js` / `.md` / `.json` / `.ps1` / `.bat` | 是 |
| 新增文件的模块头注释、docstring、行内注释 | 是 |
| README、`docs/` 下的说明文档、`captcha_mock/README.md` 一类配套文档 | 是 |
| `runs/`、`out/`、`__pycache__` 等运行产物 | 否（已在 lint 的 skip 列表） |
| 第三方库副本（若将来引入 `vendor/`） | 否 |

---

## 1. 三条判断尺子

任何一句注释、文档、提交说明，用这三条量一遍，过不了就改：

1. **只描述行为。** 读者应能从这句话推出「输入是什么、做了什么变换、输出是什么、失败走哪条路」。
   推不出来的话，说明这句话在讲立场，不在讲代码。
2. **目标永远是本机。** 例子里的域名用 `example.test`，端口用 `127.0.0.1:8777`（复刻题服务）。
   真实地址、账号、token 只存在于本机 `config.json`，不进仓库——这条由 lint 的 pii 规则守。
3. **不写给"谁批准"看的话。** 不写立场、不写免责、不为动机辩护。
   要约束行为，就写成代码里的不变量（`policy.py` 的四条原则）或测试用例（`test_captcha_flow.py`）。
   文档里出现"这段是为了说明…"这类句子，一律删。

---

## 2. 硬规则 W1–W9

| # | 规则 | 反例 | 正例 | 谁检查 |
|---|---|---|---|---|
| W1 | 动词描述能力，不描述胜负 | 见 §3 词表左列 | 见 §3 词表右列 | `wording_lint` block |
| W2 | 不点名第三方产品作为「要处理的对象」，用形态命名 | 直接写厂商名 + 产品线 | 「几何重合滑块（画布实心/空心配对）」 | 人工 + review |
| W3 | 不写立场段：免责、合规、边界声明、"仅供…" | 见 §3「免责填充」 | 模块头的「不变量」小节 | `wording_lint` block |
| W4 | 不写元叙事：关于这份代码"会不会被谁接受"的句子 | 见 §3「元叙事」 | 直接写它在做什么 | `wording_lint` block |
| W5 | 数字必须可复现：出现实测值就附命令 | 「精度很高」 | 「hit@1 96.7%，`python -m solver bench --n 60`」 | 人工 + review |
| W6 | 路径必须是仓库内真实存在的相对路径 | 盘符绝对路径、已改名目录 | `captcha-lab/solver/overlap.py` | `wording_lint` 失效路径 |
| W7 | 真实地址 / 账号 / token / 证件号不进文本 | 明文邮箱、手机号、`access_token=` | `user@example.test`、`<TOKEN>` | `wording_lint` pii |
| W8 | 注释解释"为什么"，不解释"是什么" | `# 把 x 加 1` | `# 位图宽度可能是 CSS 宽度的 2 倍，先按 bmpW 归一` | 人工 + review |
| W9 | 文档与代码冲突时以代码为准，并且**当次提交**里改文档 | 文档写"不做识别"，代码里已有识别模块 | 两处同时更新 | 人工 + review |

> W9 是本仓库历史上最贵的一条：`SECURITY_MODEL.md` 长期写着"不做图像识别"，而 `captcha_auto.py` 已经在做，
> 结果任何接手的人（或模型）读到的第一手信息就是矛盾的，只能靠猜。矛盾本身就是缺陷。

---

## 3. 词表

完整词表在 `docs/WORDING_LEXICON.md`（人读）与 `tools/wording_lexicon.json`（机器读）。
改词表只改 JSON，两者由同一次提交同步。两级严重程度：

- **block**：命中即 lint 失败，必须改。
- **warn**：只报告。`captcha-lab/defense/`、`bench_robustness.py` 这类"评测/基线"语境确实需要
  「求解基线」「鲁棒性样本」这类词汇的对象性表述，允许出现，但要在 PR 描述里给一句上下文，
  说明它指的是评测基线，而不是对某个具体目标的动作。

单行、单个文件、或一段区间可以豁免，**必须写理由**：

```python
# wording-lint: allow 这里是规范正文，词表本身需要展示反例
```

```markdown
<!-- wording-lint: off -->
（这一段是反例展示，不参与检查）
<!-- wording-lint: on -->
```

---

## 4. 注释与 docstring

### 4.1 模块头：四段，缺一段就别提交

```python
# -*- coding: utf-8 -*-
"""overlap.py —— 几何重合滑块的位移求解

定位
----
输入一张画布位图，输出「实心图形中心 → 空心轮廓中心」的水平差 dx，
以及换算到滑轨后的 slider_dx。纯函数，不碰网络、不碰浏览器。

输入输出
--------
    solve_canvas(png_bytes, track_w=400, handle_w=34) -> dict
    返回 {ok, dx, slider_dx, pairs, level}；dx/slider_dx 单位是 CSS px。

不变量
------
    · 判据与 captcha_mock/analyze.js 逐条对齐，偏差 0.00px（tests/test_overlap.py 守着）
    · 只做几何配对，不认识「这是哪个站点的题」

失败路径
--------
    配对不足两块 → ok=False，不抛异常；调用方按人工交接处理。
"""
```

四段各自回答：**这是什么 / 怎么调用 / 什么必须恒成立 / 出错怎么办**。
不要写第五段「意义」「初衷」「为什么这个功能很重要」——那是 PR 描述的内容，不是代码的内容。

### 4.2 函数 docstring

```python
def to_slider_dx(dx: float, bmp_w: int, track_w: int = 400, handle_w: int = 34) -> float:
    """把画布位移换算成滑轨位移。

    前端提交的是滑轨像素：range = max(10, track_w - handle_w)，slider_dx = dx * range / bmp_w。
    画布位图宽度与元素 CSS 宽度不一致时，先按 bmp_w 归一，不要拿 CSS 宽度当分母。
    """
```

一句话职责。参数/返回只在**类型看不出来**或**有单位/口径**时补一行。不用 `:param x:` 逐条抄签名。

### 4.3 行内注释

只解释"为什么这样写"，尤其是**不这样写会出错**的地方：

```python
# 127.0.0.1 回环也会走系统代理，不关 trust_env 会拿到 503 空响应
client = httpx.Client(trust_env=False)
```

### 4.4 四条禁止

1. 禁止在注释里写评价词堆叠（"极其重要""务必小心""非常关键"）。要说重要，就写出违反后会红的那条测试。
2. 禁止把决策历史塞进注释（"经讨论决定不这样做"）。历史进 `docs/`，代码只写当前事实。
3. 禁止 `TODO` 里写越界说明。`TODO:` 后面只能跟一个具体动作 + 一个可验证条件。
4. 禁止给不存在的文件/函数写注释（凭记忆生成）。改代码前先 `read` 目标文件，`wording_lint` 的失效路径规则只兜底一部分。

---

## 5. 命名

| 概念 | 推荐 | 不用 |
|---|---|---|
| 找缺口 / 找配对 | `locate` / `pair` / `match` | — |
| 由图片算出位移 | `solve` / `compute_dx` | — |
| 口径换算 | `to_slider_dx` / `convert` | — |
| 交给人 | `handoff` / `hand_to_human` | — |
| 自检打分 | `audit` / `risk_score` | — |
| 不通过、被拦下 | `blocked` / `rejected` / `challenge` | — |
| 针对防护的取巧写法 | 无此概念 | 英文同义词（`evade` / `crack` / `spoof` / `steal` 之类）同样不要 |

开关命名与语义必须成对写清：`CAPTCHA_AUTO`（默认值、关闭后的行为、失败回落路径）三件事写在模块头，
参照 `booking-lab/captcha_auto.py`。

---

## 6. 结构与依赖

```
captcha-lab/（识别内核，可独立 pip 安装）
      ▲
      │  只允许这个方向
      │
booking-lab/（宿主：表单填写、会话、交接、扩展）
```

- `captcha-lab` 不得 import `booking-lab` 的任何东西，也不得引用其路径。
- `booking-lab` 通过 `captcha_lab_bridge.py` 单向调用内核；内核不可用时抛 `BridgeUnavailable`，由调用方回落。
- `captcha_flow/` 是决策层：`policy.py` 纯函数、`detect.py` 只读 DOM、`session.py` 只管时序。
  新能力**不进**这几个文件；要加速就另开模块（参照 `captcha_auto.py`）。

### 新能力四件套（缺一件就返工）

1. **独立文件**，不改既有决策层；
2. **显式开关**，默认值写进模块头，关闭后行为 100% 等于原实现；
3. **降级路径**：任何一步失败都回落人工交接，并打日志说明原因，不静默重试；
4. **离线测试**：用假实现（`FakePage`）或合成夹具，不依赖真实浏览器、真实网络、真实时钟。

### 契约变更三步（同一个提交内完成）

1. 先改 `docs/` 里写"不变量"的那一节；
2. 再改对应的测试断言；
3. 最后改代码。

顺序反了就会出现"测试证明了旧契约、代码执行新契约"的状态，比没有测试更糟。

---

## 7. 文档规范

每份文档第一段必须回答「这份文档回答什么问题」。做不到就先别写。

| 要素 | 要求 |
|---|---|
| 结构 | 表格优先于段落；每节至少一条可执行命令 |
| 数字 | 每个实测值附命令与日期（「166 passed，2026-11-15，`python -m pytest -q`」） |
| 路径 | 仓库内相对路径；跨目录引用写全路径 |
| 篇幅 | 一屏能读完的优先。超过 300 行就拆 |
| 禁止 | 立场段、免责段、"为什么这个项目重要"、"读完你会明白…" |

文档索引在 `docs/README.md`，新增文档必须在索引里加一行，否则等于不存在。

---

## 8. 提交前自检

```bash
# 1) 文案与注释（必须 0 block）
python tools/wording_lint.py --root .

# 2) 宿主侧测试（期望 115 passed）
cd booking-lab && python -m pytest captcha_flow/tests tests -q

# 3) 识别内核测试（期望 166 passed）
cd captcha-lab && python -m pytest -q

# 4) 识别口径回归（期望 dx=230.50 / slider_dx=210.91）
cd captcha-lab && python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34

# 5) 一行跑完 1–3
pwsh -File tools/check.ps1
```

`tools/check.ps1` 就是 1–3 的串联，退出码非 0 直接挂 CI。

---

## 9. 违规处置

| 命中 | 处理 |
|---|---|
| block 词表 | 当次提交内改掉；改不掉的写 `wording-lint: allow` + 理由 |
| warn 词表 | 允许保留，PR 描述里给一句上下文 |
| 失效路径 | 改成本仓库真实路径；旧路径只允许出现在 `_quarantine/` 之外的归档说明里 |
| pii / secret | 立刻改占位符。若历史上推过远端，按已泄露处理：换 token，不靠删文件解决 |
| 文档与代码冲突 | 以代码为准，同提交修文档（W9） |

---

## 10. 速查卡

```
写代码前   read 目标文件 → 确认它在做什么，不凭记忆
写注释     四段头：定位 / 输入输出 / 不变量 / 失败路径
写文档     顶部一句「回答什么问题」；数字带命令；路径用相对
写不下的   立场、免责、动机、历史 → 都不写
新能力     独立文件 + 开关 + 降级 + 假实现测试
改契约     文档 → 测试 → 代码，同一个提交
提交前     pwsh -File tools/check.ps1
```
