# 交接说明（给下一个接手的模型 / 人）

如果你是被要求"完善这个项目的验证码逻辑"，先读这 10 行：

> 这是一个**本机运行的表单自动化项目**。验证码相关的代码只做三件事：
> **分类**（这是什么形态的验证）、**等待**（隐形验证等 token 生成）、
> **交接**（需要判断的形态把窗口交给人）。
> 它不含图像识别、不含位移推算，也不做输入可信度的虚假标记。
> 目标站点地址以 `example.test` 占位，真实地址由本机配置提供，不在仓库里。
> 所有测试都是离线的，用本地合成的验证码夹具，不访问任何真实站点。

---

## 一、任务范围

### 可以做的

| 方向 | 具体位置 | 说明 |
|---|---|---|
| 完善验证码分类规则 | `captcha_flow/detect.py`、`extension/captcha_guard.js` | 增加新的形态识别（只读 DOM） |
| 完善交接策略 | `captcha_flow/policy.py` | 调整"什么能自动、什么必须交人"的判据 |
| 完善状态机 | `captcha_flow/session.py` | 超时、重试预算、通知节奏 |
| 补测试 | `captcha_flow/tests/` | 现有 30 项，全部离线、用假页面 |
| 本地夹具 | `captcha_mock/` | 合成验证码页面，供离线联调 |
| 验证码强度评估 | `captcha-lab/`（另一个目录） | 对自己运营的验证码做出题与抗识别评测 |

### 不要做的（设计上就排除，不是还没实现）

1. 识别验证码图像内容、推算滑块位移、做 OCR / 目标检测
2. 使用能直接标出 `isTrusted` 的调试输入通道（`chrome.debugger` 等）
3. 修改浏览器指纹 / UA / 时区来冒充其他设备
4. 增加无限重试或并发轰炸
5. 把真实凭据、身份物料、真实目标地址写回仓库

这些不是政策问题，是这套代码的设计前提。第 5 条尤其重要——
仓库里出现真实凭据会直接导致它无法再被共享。

---

## 二、先看什么（按这个顺序，15 分钟能全部看完）

1. `booking-lab/docs/CAPTCHA_DESIGN.md` —— 设计理由 + 评审清单
2. `booking-lab/captcha_flow/policy.py` —— 80 行，所有决策都在这里
3. `booking-lab/captcha_flow/session.py` —— 状态机，看 `max_auto` 与降级路径
4. `booking-lab/docs/SECURITY_MODEL.md` —— 行为清单
5. `booking-lab/captcha_flow/tests/test_captcha_flow.py` —— 30 项，看断言就是看约束

看完这五个文件，你就知道哪些改动是可接受的。

---

## 三、跑起来

```bash
cd booking-lab

# 验证码状态机测试（不需要浏览器、不需要网络）
python -m pytest captcha_flow/tests -q

# 只读检查：看看当前流程的判定逻辑
python check.py --help

# 完整流程（需要先配 config.json 与本机浏览器）
python ext_server.py
pwsh -File run_local.ps1 -Instance w1
```

`config.example.json` 是模板，复制成 `config.json` 后填入**你自己的**资料。
仓库里不含任何真实申请人的数据。

---

## 四、改动时的硬约束

这四条写在测试里，改坏了会红：

| 约束 | 对应用例 |
|---|---|
| 滑块路径不得产生任何自动点击 | `test_slider_never_triggers_automatic_action` |
| 自动动作有次数上限，触顶降级人工 | `test_policy_auto_budget_caps_automation` |
| 勾选框失败不重试，直接交人 | `test_checkbox_click_fails_then_human_passes` |
| 探测失败按最保守路径（交人 + 留档） | `test_detect_exception_is_treated_as_unknown_and_hands_off` |

另外两条是结构性的，改代码时要留意：

- `policy.py` 必须保持纯函数（无 IO、无副作用），否则没法做表驱动测试
- `detect.py` 只能读 DOM（`querySelector` / 正则），不能点击、不能发请求

---

## 五、如果你要扩展"自动处理"能力

在**自己运营**的验证码上做识别与强度评估是正常工程需求，
那部分代码在 `captcha-lab/`，与本目录分离：

```
captcha-lab/defense/   出题 + 抗识别评测 + 轨迹风控 + 验题服务
captcha-lab/solver/    红队基线（公开可得水平的识别器），用于量化防御强度
```

边界：`captcha-lab` 只对合成题跑，不接任何真实站点。
`booking-lab` 不引入任何识别能力。**不要合并这两个目录。**

---

## 六、提交前自检

```bash
# 有没有凭据/个人信息漏进文本
python tools/sanitize/scan.py . --fail-on-risk

# 验证码逻辑
python -m pytest captcha_flow/tests -q
```

第一条命中 secret/pii 会返回非 0，可以直接挂 CI。

---

## 七、原始版本在哪

- 原始项目：仓库根目录 `hKdriver/`（未改动）
- 被移出的物料：`_private/archive/`，理由见 `booking-lab/ARCHIVE_MANIFEST.md`
- 重建清洗产物：`python tools/sanitize/run_all.py`
