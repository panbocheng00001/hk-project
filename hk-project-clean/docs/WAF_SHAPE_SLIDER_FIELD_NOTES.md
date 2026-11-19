# WAF 形状对齐滑块 · 现场实测

记录 `captcha-lab/solver/overlap.py` 对应的那道题的线上形态与实测参数。
数值来自 2026-11-16 单日在 `abs3.example.test` / `abs4.example.test` 的观测，
用于校准 `captcha_auto.solve_page` 的位移换算与提交判定。

---

## 1. 题面结构（两层）

| 层 | 内容 | 载体 |
|---|---|---|
| 背景层 | 深色面板 + 32 px 网格 + 若干空心轮廓 | canvas 的 CSS `background-image`（`<base64 data url>`） |
| 前景层 | 顶部文案 + 实心图形 + 滑轨 + 手柄 + Submit | canvas 2D 内容 |

要点：实心在 2D 层，空心在背景图，两层坐标同尺寸叠加（420×428）。
只读 `getImageData` 看不到空心；只读背景图看不到实心。**必须跨层配对**。

---

## 2. 几何（420×428 位图坐标）

| 项 | 值 |
|---|---|
| 位图 | 420 × 428 |
| 滑轨 | `y = 348`，`x0 = 24` → `x1 = 394`（长 369） |
| 手柄 | 直径 22，初始圆心 `(27.5, 349.5)`，填充 `rgb(46,95,216)` |
| Submit | 中心 `(210, 402)`，尺寸 140 × 36，填充 `rgb(47,107,216)`；**画在 canvas 内，DOM 无对应节点** |
| 面板底色 | `#FEFEFE`（前景）/ `#121A26`（背景层） |
| 文案 | 白色，基线 `y ≈ 20`，字号 13 px |

---

## 3. 位移换算（关键参数）

实测两次拖前/拖后对账：

| 段 | 手柄位移 | 实心中心位移 | 比值（图形/手柄） |
|---|---|---|---|
| 27.5 → 94.5 | +67 | 160 → 180（+20） | 0.299 |
| 94.5 → 131.5 | +37 | 180 → 193（+13） | 0.351 |
| 合计 | +104 | +33 | **0.317** |

即：`slider_dx = dx / 0.317 ≈ 3.15 × dx`。

对照：`overlap.to_slider_dx` 用的是 `dx × (trackW − handleW) / bmpW`
（420 题面下 ≈ 0.83 × dx）。两者差约 3.8 倍，**说明该题的滑块不是"位置绝对值映射"，
而是"增量带阻尼"**。这一题的换算必须走 `travel_ratio`，不能用轨道宽度公式。

比值在不同题之间有浮动（观测到 0.299 – 0.351），因此定值只能作为首拖估计，
落点需要用"拖后重量 + 按实测效率补拖"收敛（`captcha_auto.solve_page` 的复核循环）。

---

## 4. 文案变体（三种，均英文，`lang=sc` 也不变）

```
Move the slider so the solid shape lands on the hollow one.
Adjust the slider until the two shapes line up.
Drag the slider until the solid and hollow shapes overlap.
```

页面识别需同时覆盖 `move the slider` / `adjust the slider` / `drag the slider` /
`two shapes` / `solid shape lands` / `shapes overlap`。

---

## 5. 提交形态

| 项 | 值 |
|---|---|
| 请求 | `POST /T7AyTrxoWxGd?sBCIo0XL=<140 字符>` |
| 查询参数 | 固定名 `sBCIo0XL`，值长度恒为 140（628/628 样本） |
| 频次 | 单日观测 1449 次（`abs4` 445 次 / `abs3` 183 次），间隔 0.4–3.4 s |
| 响应码 | 200 × 501（受理）、202 × 26（排队中）、401 × 50（未通过）、412 × 2（重新挑战） |
| 资源 | `RGOj2pQjqEKb/8qcykgusbcwc.<ver>.js`（挑战脚本）、`RGOj2pQjqEKb/JQeAuw451wI2/header_bg_png`（背景图） |

注意：页面 DOM 里唯一的 `<button id="swap" class="submit">` 文案是
`Try another verification`，语义是**换一题**。点它只会换新题，不会提交。

---

## 6. 已知失效路径（观测到并已排除）

| 现象 | 成因 |
|---|---|
| 每轮补拖都拖在错位置 | 找手柄时只扫 `x < 25%`，手柄拖到 131 px 后超出，退回固定坐标 |
| 每轮只补到 31.7% | 把 `travel_ratio` 当 1:1 |
| 拖到位却永远不过 | 提交点落在 `#swap`（换一题）上 |
| 手柄定位到按钮上 | 全宽扫蓝色像素时，Submit（`rgb(47,107,216)`）与手柄同色，落在 `y > 0.9h` |

定位手柄的扫描带应是滑轨所在行：`y ∈ [0.76h, 0.86h)`。

---

## 7. 本地复刻对照

`booking-lab/captcha_mock`（Node，`127.0.0.1:8777`）复刻的是 400×330 题面，
`trackW=400 / handleW=34`，`travel_ratio` 未建模（其 `to_slider_dx` 即轨道宽度公式）。

该复刻用于验证"识别 + 配对"是否正确（`selftest.js` 30/30，最大误差 0.53 px），
**不用于验证本题的位移换算**；换算差异见 §3。
