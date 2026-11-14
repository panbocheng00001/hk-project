# 滑块自动求解 / 自动拖动（CAPTCHA_AUTO）

> 这份文档回答一个问题：**现在验证码怎么自动过，怎么关，失败时怎么退。**
> 实测口径：本地复刻题服务（`captcha_mock`，400×330 / 轨道 400 / 手柄 34 / 容差 3px）。

## 1. 现在有两条入口，都会自动

| 入口 | 谁在跑 | 自动链路 |
|---|---|---|
| Python / Playwright | `fill_form.py`、`resume.py` 调 `captcha.guard()` | `guard()` → `captcha_auto.solve_page()` → 识别 → `page.mouse` 真实拖动 → 复核 |
| 浏览器扩展 | `run_local.ps1` 起 CfT + 扩展，`content.js` 状态机 | `doWafCaptcha()` → `autoSlider()` → `send({type:"solveSlider"})` → `background.js` → `ext_server /solve_slider` → `humanDrag()` 拖动 → 复核 |

两条入口共用同一套识别内核：`captcha_lab_bridge` → `captcha-lab/solver/overlap.py`。

## 2. 实测结果

```text
python tools\bench_captcha_auto.py 20
通过 20/20 = 100.0%
提交误差 mean=0.225px max=0.540px

pytest captcha_flow\tests tests -q
96 passed          # 55 契约/计时 + 41 宿主侧（含 16 pagekit）
```

单题链路（`tests/test_captcha_auto_e2e.py`）：

```text
[captcha_auto] 第 1 轮：dx=73.00 slider_dx=66.80 pairs=5
[captcha_auto] 拖动完成：66.80px / 49 点
grade {ok: true, pass: true, truth_dx: 73, submitted_dx: 73.01, diff: 0.01}
RESULT ok=True note=page-grade tries=1
```

也就是说：**识别、换算、拖动、判分四步都在真浏览器里跑通了，一次过，误差 0.01px。**

## 3. 开关

| 变量 | 默认 | 作用 |
|---|---|---|
| `CAPTCHA_AUTO` | `1`（开） | 设 `0` / `false` / `off` 关闭，行为回到“纯人工交接” |
| `CAPTCHA_LAB_PATH` | `../captcha-lab` | 识别内核目录；pip 装过 captcha-lab 时不需要 |

关闭后：
- `captcha.guard()` 不再调用 `captcha_auto`，滑块一律交人；
- 扩展侧 `autoSlider()` 不会被调用（`doWafCaptcha` 只在 `r.type/s.type === "slider"` 时尝试，该分支仍会先问 guard）。

## 4. 失败怎么退

任何一步失败都**回落人工交接**，不会静默重试到死：

| 失败点 | 行为 |
|---|---|
| 页面上找不到 canvas / 手柄 | `solve_page` 返回 `ok=False, note="没有可识别的滑块"`，guard 继续走人工 |
| 识别内核没装好 | `BridgeUnavailable` 被捕获，日志记一行，人工接手 |
| 拖动后页面判分不过 | 最多 3 轮（`max_tries`），每轮重新截图重算；仍不过则人工 |
| 扩展侧 `/solve_slider` 500 | `autoSlider()` 返回 null，`askHuman()` 照常喊人 |

## 5. 浏览器扩展那条路怎么验证（尚未在你机器上实跑）

```powershell
# 1) 起本地服务（8765）
cd E:\googleProduct\hk-project\hk-project-clean\booking-lab
python ext_server.py

# 2) 另开一个窗口起复刻题（8777）
cd captcha_mock; node server.js

# 3) 用带扩展的浏览器打开复刻题页
pwsh -File .\launch_ext.ps1      # 或 run_local.ps1 -DryRun

# 4) 看日志里这两行
#    [solve_slider] ok=True dx=... slider_dx=...
#    ✅ 滑块已自动完成（slider_dx=...）
```

注意：扩展侧的 canvas / 手柄选择器是通用启发式
（`#handle`、`[class*="handle"]`、`[class*="slider-btn"]`、`[role="slider"]`），
第一次对**真实页面**跑时，先看一眼日志里的 `bmp_w/css_w/track_w/handle_w` 是否与页面一致，
不一致就补一条选择器到 `content.js::autoSlider()` 与 `captcha_auto.py::PROBE_JS`。

## 6. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `captcha_auto.py` | 新增：`solve_image()`（纯图像解法）、`solve_page()`（识别+拖动+复核）、`PROBE_JS`、`GRADE_JS` |
| `captcha.py` | `guard()` 检测到需人工的形态时先调 `captcha_auto`，成功直接返回 `solved`；失败/异常回落 |
| `captcha_lab_bridge.py` | 默认轨迹档案 `env_consistency` → `stealth`（前者在内核里不存在，会 KeyError） |
| `captcha_mock/server.js` | 页面加真实 pointer 拖动（`pointerdown/move` 更新 `left`），判分结果写 `window.__grade`；接口与判据未改 |
| `ext_server.py` | 新增 `POST /solve_slider`：位图 + 几何 → `{ok, dx, slider_dx, track, points, risk}` |
| `extension/background.js` | 新增 `case "solveSlider"` → 转发到 `/solve_slider` |
| `extension/content.js` | 新增 `autoSlider()`：取 canvas → 求解 → `dragBy()` 真实指针拖动 → 复核；失败交人 |
| `tests/test_captcha_auto_e2e.py` | 新增 3 条端到端用例（自动通过 / `guard` 返回 solved / 关闭时不拖动） |
| `tools/bench_captcha_auto.py` | 新增：连跑 N 道统计通过率与提交误差 |
