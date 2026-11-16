# 验证码特征档案 · 华为云 WAF「形状对齐」滑块（Quidditch）

> 采集时间：2026-11-16 17:18–17:30　｜　站点：`abs3.td.gov.hk`（Server: CW）
> 机器可读版本：[waf_captcha_features.json](<runs/captcha_specimens/waf_captcha_features.json>)（由 `measure_waf_feature.py` 实测生成）
> 样本目录：[runs/captcha_specimens/waf20261116/](<runs/captcha_specimens/waf20261116>)、`runs/captcha_17*.png`

---

## 1. 怎么认出它（识别特征）

| 判据 | 值 |
|---|---|
| 页面文案（英文，本会话 `lang=sc` 仍出英文） | 三选一见第 2 节 |
| DOM | 一个约 **420×428** 的 canvas；滑轨和 Submit **都在 canvas 内部**（DOM 里没有手柄节点） |
| 帧归属 | 主文档（`inFrame=false`）；旁边的 `about:blank` iframe 只是 WAF 辅助帧 |
| 网络 | 一开始 `412 Precondition Failed`（挑战页），过后页面正常渲染 |
| 与 MTCaptcha 的区别 | 无 `mtcaptcha` 域名/类名、无勾选框、无 `sitekey` 参数 |

## 2. 题干三种变体（同一机制）

```
Move the slider so the solid shape lands on the hollow one.
Adjust the slider until the two shapes line up.
Drag the slider until the solid and hollow shapes overlap.
```

## 3. 双层结构（关键：不是一张图）

| 层 | 尺寸 | 底色 | 画什么 | 取法 |
|---|---|---|---|---|
| canvas 2D | **420 × 428** | 透明（合白底 `#FEFEFE`） | **实心形状** + 滑轨 + 手柄 + Submit | `canvas.toDataURL()` |
| CSS background | **420 × 320** | 深色 `#121A26` | **空心轮廓**（目标）+ 网格 + 装饰线 | `background-image` |

- 两层同宽（420），bg 只覆盖上方 **74.8%**（320/428）
- 坐标系同源：canvas 的 (x,y) 与 bg 的 (x,y) 直接可比

## 4. 几何参数（420×428 坐标系，实测）

| 元素 | 值 |
|---|---|
| 题干文字行 | y = 0–37 |
| 滑轨 | `y = 348`，`x0 = 24`（手柄最左时）→ `x1 = 394`，**总长 369** |
| 手柄 | 圆心 `(27.5, 349.5)`，直径 **22**，初始贴最左 |
| Submit | bbox `[140, 384, 140, 36]`，中心 **(210, 402)** |
| 实心形状中心 | 实测样本：`(160, 152)`，尺寸 48×44 |

## 5. 行程比 —— 最关键的数（也是之前一直失败的真因）

滑轨走 1px，图形只走 **0.317px**（三次实测：0.299 / 0.351 / 0.317，取中位 **0.317**）：

| 区间 | 手柄位移 | 图形位移 | 比值 |
|---|---|---|---|
| 172856 → 172956 | 27.5 → 94.5（+67） | 160 → 180（+20） | 0.299 |
| 172956 → 172959 | 94.5 → 131.5（+37） | 180 → 193（+13） | 0.351 |
| 172856 → 172959 | +104 | +33 | **0.317** |

推论：

- 要消除 `dx`，手柄必须走 **`dx / 0.317 ≈ 3.15 × dx`**
- 滑轨全长 369 ⇒ 图形最大可位移 **≈ 117px**
- 按 1:1 拖（旧逻辑）每轮只能补掉 31.7%，永远追不上 ⇒ 表现为「一直差一点」

## 6. 颜色表（RGB）

| 元素 | 颜色 |
|---|---|
| canvas 底（合成后） | `#FEFEFE` |
| bg 底 | `rgb(18, 26, 38)` |
| 手柄 | `rgb(69, 122, 237)` |
| Submit | `rgb(38, 98, 234)` |
| 实心形状样例 | `rgb(253, 242, 198)`（浅黄）/ `rgb(150,122,11)`（橄榄） |
| 空心轮廓样例 | `rgb(214, 108, 190)`（粉紫）/ `rgb(69, 56, 103)`（暗紫） |

## 7. 解法（已验证到「能拖动」这一步）

1. 从 canvas 取实心形状中心 `xs`，从 bg 取空心轮廓中心 `xt`（按形状族 + 尺寸 + 同行配对）
2. `dx = xt - xs`（位图像素）
3. 手柄行程 = `dx / 0.317`，起点 = 手柄圆心（初始 `(27.5, 349.5)`；拖动后用 `track.x0 + 3.5` 反推）
4. 拖完**重新量一次** `dx`，按 `剩余 / 0.317` 再补一次即可收敛
5. 点 Submit（canvas 内 `(210, 402)`）

> 拖动必须用**真实输入**：`chrome.debugger` 的 `Input.dispatchMouseEvent` 在本 widget 上时灵时不灵
> （17:28:56–17:29:32 四张 canvas sha 完全一致＝没动；17:29:56 之后才开始动）。
> 用 `waf_drag.py` 的 SendInput 路径最稳。

## 8. 网络与时序特征

| 项 | 值 |
|---|---|
| 校验接口 | `POST https://abs3.td.gov.hk/T7AyTrxoWxGd?sBCIo0XL=…` → **202**（路径与参数名每次都变） |
| 资源目录 | `https://abs3.td.gov.hk/RGOj2pQjqEKb/…`（随机） |
| widget JS | `RGOj2pQjqEKb/8qcykgusbcwc.5057567.js`（357 557 B，全混淆，无明文题干） |
| 背景图 | `RGOj2pQjqEKb/JQeAuw451wI2/header_bg_png`（浏览器内 200 / 8 205 B；直连 202） |
| WAF 标识 | `Server: CW`；cookie `HWWAFSESID` / `HWWAFSESTIME` / `HIK0kat3rt9xO`（HttpOnly+Secure，有效期 10 年） |
| 题目刷新 | 每次提交/刷新换新题（尺寸与布局不变，形状与 dx 变） |
| 通过判据 | **只看是否离开 `waf_captcha` 阶段**（进 calendar/form）；`captcha_solved` 是自评，会假通过 |

## 9. 证据文件

```
runs/captcha_specimens/waf20261116/
  shot_171828_align_two_shapes.png     "…two shapes line up…"      1920x897
  shot_171901_solid_onto_hollow.png    "…solid shape lands on…"    1920x840
  shot_172003_loop_fake_solved.png     "…solid shape lands on…"    929x812
  waf_widget_8qcykgusbcwc.5057567.js   WAF widget（357KB 混淆）
runs/captcha_172932.png  未拖基准（手柄 x=27.5, 图形 x=160, dx=57.2）
runs/captcha_172956.png  拖一次后（手柄 x=94.5,  图形 x=180, dx=36.8）
runs/captcha_172959.png  再补一次（手柄 x=131.5, 图形 x=193, dx=23.9）
runs/captcha_bg_1729*.png 背景层（空心轮廓 + 网格）
```

## 10. 复现命令

```powershell
python measure_waf_feature.py                  # 重新实测 → waf_captcha_features.json
python waf_shape_probe.py <png> --debug out.png # 单图解析 + 标注
python waf_drag.py --watch 900                 # 找面板 → 算 dx → 真鼠标拖 → Submit
```
