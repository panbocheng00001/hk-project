# CAPTCHA_DESIGN —— 验证码交互状态机的设计与评审要点

这份文档是给"要审查这套验证码逻辑的人"看的。
它不是使用说明，是**设计理由 + 可核验的约束**。

---

## 1. 问题重述

预约表单里会出现验证码，形态不固定：有时是隐形的（只生成 token），
有时是勾选框，有时会升级成滑块或点选图片。手工操作时这不成问题；
自动化流程里它会造成三种故障：

| 故障 | 旧实现的表现 | 后果 |
|---|---|---|
| 隐形验证还没出 token 就提交 | 服务端直接拒 | 白跑一轮 |
| 出现挑战面但不处理 | 脚本继续点下一步 | 卡死 |
| 处理方式靠猜 | 猜错 | 会话被污染，20 分钟窗口浪费 |

所以需要的不是"更强的识别"，是**明确的处理策略 + 可观测的决策过程**。

---

## 2. 分层：为什么拆成五个文件

```
model.py    数据类型（CaptchaKind / Result / ChallengeInfo / Outcome）
   ↓
detect.py   只读 DOM，输出 ChallengeInfo        ← 唯一的"看"
   ↓
policy.py   纯函数：ChallengeInfo → Decision     ← 唯一的"想"
   ↓
session.py  状态机：执行 Decision，管时间与交接   ← 唯一的"做"
   ↓
adapters.py 页面能力抽象                         ← 唯一的"外部依赖"
```

拆分的价值是可核验：

- **policy.py 无副作用**，给定输入永远同一输出，可以直接列举全部输入空间做表驱动测试。
- **detect.py 只读**，不点不等不发请求，审的时候只需要确认它只调了 `querySelector`。
- **session.py 不含策略**，所有阈值来自 `Policy`，改行为不需要改逻辑。
- **adapters.py 是唯一耦合点**，换 Playwright 版本只改这里。

---

## 3. 分类与处置矩阵

| 形态 | 是否自动 | 处置 | 依据 |
|---|---|---|---|
| `none`（无验证码） | — | 直接继续 | 无需处理 |
| `none` + SDK 在 + token 空 | 是 | 等 token，最长 20s | 隐形验证会自己完成，不需要推断 |
| `token_only` | 是 | 同上 | 同上 |
| `checkbox` | 是（点一次） | 点一次 → 复核 → 不过就交人 | 点勾选框不涉及内容判断 |
| `slider` | **否** | 抬窗口 + 通知 + 等待 | 位移是内容判断题 |
| `image_code` | **否** | 同上 | 字符识别即代替人判断 |
| `image_grid` | **否** | 同上 | 目标检测即代替人判断 |
| `unknown` | **否** | 同上 + 留档 | 探测失败时按最保守路径走 |

矩阵里"否"的三行是硬约束，测试里有对应用例：

```python
def test_slider_never_triggers_automatic_action():
    g, fp, _ = make_guard([{"kind": "slider"}, {"kind": "none"}],
                          [NOT_SOLVED, IS_SOLVED])
    out = g.run("w1")
    assert out.result is Result.PASSED_HUMAN
    assert fp.n_click == 0, "滑块路径不得产生任何自动点击"
```

---

## 4. 三条自主预算

自动化最危险的不是"做错"，是"不停做"。所以三个预算都是硬上限：

| 预算 | 默认 | 行为 |
|---|---|---|
| `Policy.max_auto` | 2 | 任何自动动作累计 2 次后，一律降级为人工 |
| `Policy.token_wait_s` | 20 | 隐形验证最多等 20 秒，超时不再等 |
| `Policy.human_wait_s` | 180 | 人工等待上限；期间按 `renotify_every_s` 提醒 |

另有调用方级别的硬 deadline：

```python
out = guard.run("w1", deadline_s=90)   # 覆盖 policy，绝不越过
```

勾选框失败**不重试**——点了没过说明已升级成图片题，再点就是无意义的重试。
这条写成了代码注释，也写成了用例：

```python
def test_checkbox_click_fails_then_human_passes():
    ...clicks=0
    assert out.result is Result.PASSED_HUMAN
```

---

## 5. 可观测性

每次状态迁移都产生一个 `GuardEvent`，可以落盘、可以上报：

```
[0s] decide: hand_to_human: slider 需要人工判断
[0s] handoff: w1 需要人完成 slider
[3s] passed_human: 3s
```

`Outcome` 带回 `result / kind / waited_s / auto_attempts / events`，
上层据此决定是继续下一步、还是记一次失败、还是终止本轮。

这套事件流接的是 `journal.py` 的账本，所以事后能回答
"这次卡在哪、卡了多久、是自动没过还是人没来"。

---

## 6. 与浏览器端的关系

`extension/captcha_guard.js` 是同一套策略的浏览器端实现，刻意保持同构：

| Python (`captcha_flow`) | JS (`captcha_guard.js`) |
|---|---|
| `scan()` | `scan()` |
| `waitToken()` | `waitToken()` |
| `clickOnce()` | `clickOnce()` |
| `solve()` 分派 | `solve()` 分派 |
| `needs_human` 属性 | `needsHuman()` |

两边都是：能自动的两类走自动路径，其余一律返回 `needHuman: true`。

JS 侧比 Python 侧少的东西也是刻意的：

- 没有 canvas 像素读取
- 没有位移计算
- 没有轨迹生成
- 没有 `chrome.debugger` 调用

`manifest.json` 因此也不声明 `debugger` 权限。

---

## 7. 评审清单

审这套逻辑时，按这个顺序看，10 分钟能看完：

- [ ] `policy.py` 全文 —— 80 行，全部决策在这里
- [ ] `policy.py` 里 `needs_human` 的三类是否都返回 `HAND_TO_HUMAN`
- [ ] `session.py` 里 `max_auto` 是否每处都检查
- [ ] `session.py` 里勾选框失败路径是否只降级、不重试
- [ ] `detect.py` 是否只有 `querySelector` / 正则，无点击无请求
- [ ] `adapters.py` 是否只有 `evaluate` / `click_checkbox` / `bring_to_front`
- [ ] `captcha_guard.js` 是否无 canvas 像素读取、无 debugger 调用
- [ ] `manifest.json` 权限列表是否无 `debugger`
- [ ] `tests/` 里"滑块不自动"这条用例是否覆盖

---

## 8. 如果要换成"自动识别"方案

在**自己运营**的验证码上做这件事是正常的工程需求（评估自家验证码强度）。
那套代码在 `captcha-lab/`，与本项目分开：

- `captcha-lab/defense/` 出题 + 抗识别评测
- `captcha-lab/solver/` 作为红队基线，用来量化"现有识别器能解多少"

两个目录的边界是：`captcha-lab` 只对合成题跑，不接任何真实站点。
本项目（`booking-lab`）不引入任何识别能力。这个分界写在这里，避免以后有人合并。
