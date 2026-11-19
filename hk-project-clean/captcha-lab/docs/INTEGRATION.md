# 端到端集成：从"识别对"到"真的过"

> 一句话结论：**图片 → 坐标 → 拖动距离**这一段是确定的（96.7% 一次命中）；
> **"能不能过"**取决于站点把答案包了几层：接口协议、加密参数、会话绑定、行为风控。

## 1. 成功链条

```
过 = 图像识别(96.7%) × 口径正确(center/left, CSS px) × 会话与 token 有效
     × 轨迹不被拦 × 没有被一次性/次数限制抵消
```

任何一环为 0，整体就是 0。所以"识别出来"只是走完了第一段。

## 2. 三档站点，难度差一个数量级

| 档位 | 特征 | 需要什么 | 本仓库能否直接跑 |
|---|---|---|---|
| A. 自研图片滑块 | 接口直接给 bg/piece，提交 `{x, track}` | 图片 + display_width + 字段名 | ✅ `JsonSliderFlow` 改 5 个字段即可 |
| B. 主流厂商（极验 v3、腾讯、易盾、顶象） | 提交参数有签名/加密，`w`、`sign`、`pow` 之类 | 浏览器的页面上下文（让站点自己的 JS 算加密） | ✅ 走 `PlaywrightSlider`；纯 HTTP 需要自己还原加密 |
| C. 强风控站点 | 设备指纹、一次性 token、频率/环境校验、IP 信誉 | 浏览器 + 真会话 + 可控网络出口 | ⚠️ 识别和轨迹能过，风控面需另做（代理、指纹一致性） |

极验 v4 这类"挑战题"的 `w` 是 AES+RSA 加密、还带 `pow` 与题型分支——**不要试图用
纯 HTTP 复原**，成本远超收益。正确做法是打开真实浏览器，让它自己的 JS 生成 `w`，
你只负责把"缺口在哪"翻译成鼠标事件。

## 3. 30 秒定位失败原因

按顺序问自己四个问题：

```
1. 标注图（--debug-out）里框对了吗？
   └─ 不对 → 识别问题：确认 bg 没被裁切、piece 带 alpha、display_width 正确
2. 框对，但提交的距离和站点期望差半个拼图块？
   └─ 口径问题：answer_mode 改 center / left（差 |w/2| ≈ 20–25px，最典型）
3. 距离对，提交直接返回"验证失败/重试"？
   └─ 协议问题：字段名、签名参数、token 是否随题变化（先抓一次真机请求对比）
4. 距离对、协议对，但总是"行为异常"或被要求重试？
   └─ 风控问题：换 track_profile=natural、检查采样间隔、确认鼠标事件是真实事件流
```

判断口径错的一个快速办法：把 `distance` 和 `distance_left` 都试一次，
哪个通过就是哪个。差的那一半宽度就是拼图块的半宽。

## 4. 最容易踩的五个坑

| 坑 | 现象 | 处理 |
|---|---|---|
| 缩放没换算 | 总是差固定比例（如 1.5×、2×） | 传 `display_width`（元素 clientWidth），不要用图片原始宽度 |
| 拼图块初始位置不是 0 | 总是多拖/少拖 ~10–20px | `slider_start_x` 设为手柄初始中心相对图片左边缘的偏移 |
| 一题多试 | 试第二个候选就被判定失败 | 多数站点一题一验；用 `refresh=True` 换题重试，而不是同题试多次 |
| 图片被压缩/裁剪 | 缺口周围有白边，坐标整体偏移 | 截图时只截元素本身（`element.screenshot()`），不要在整页截图上算 |
| 轨迹时长不合常理 | 通过但被要求二次验证 | 换 `track_profile`，`fast` 在强风控站点上反而更可疑 |

## 5. 端到端最小脚本（B 档站点）

```python
from playwright.sync_api import sync_playwright
from solver import SliderSolver
from solver.client import BrowserSelectors, PlaywrightSlider

URL = "https://example.com/login"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(URL, wait_until="networkidle")

    driver = PlaywrightSlider(
        SliderSolver(site="generic", track_profile="natural"),
        settle_ms=600,
    )
    sel = BrowserSelectors(
        bg=".captcha-bg",          # 背景图元素
        piece=".captcha-piece",    # 拼图块（没有就删掉）
        handle=".slider-handle",   # 手柄
    )
    result = driver.solve_page(page, sel, attempts=2)
    print("ok:", result.ok, "distance:", result.distance)

    # 验证通过后继续登录流程
    page.fill("#username", USER)
    page.fill("#password", PWD)
    page.click("#submit")
    page.wait_for_timeout(3000)
    print(page.title())
```

这一段里没有一行"加密算法"——加密由页面自己的 JS 完成，我们只提供"拖到哪里、怎么拖"。

## 6. 量产前要确认的四件事

1. **成功率**：连续跑 50 次，统计一次通过率、二次通过率；低于 70% 先修口径与轨迹，不要加并发。
2. **频率**：站点通常对单 IP/单账号有阈值；识别快不等于能高频，限速在调用侧做。
3. **可观测**：每次失败留下 `debug_image` + `answers` + 提交响应，否则永远在猜。
4. **降级**：置信度 < 0.6 时不要硬提交——换成刷新题目或人工，比盲目提交更省配额。
