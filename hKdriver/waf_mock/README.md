# WAF 形状滑块 · 本地复刻（mock）

线上是华为云 WAF（`Server: CW`）的 Quidditch 形状滑块。它一次只能试一轮，失败就换题、还会被风控，
所以按实测参数 1:1 复刻成本地服务，求解器可以无限次跑。

## 快速开始

```powershell
# 1) 起服务（默认 8766）
python waf_mock/server.py --port 8766

# 2) 浏览器打开
http://127.0.0.1:8766/

# 3) 回归识图 + 算 dx（headless，不需要你盯着）
python waf_mock/test_mock.py --rounds 5
python waf_mock/test_mock.py --rounds 3 --dir neg   # 反向行程，测方向自学习
```

## 接口

| 路径 | 说明 |
|---|---|
| `GET /` | 验证码页面 |
| `GET /api/state` | 当前题目真值 + 提交计数（外部断言用） |
| `POST /api/report` | 页面上报题目 / 结果 |
| `POST /api/reset` | 清零 |

`--dir neg` 会让「手柄右移 → 图形左移」，用来测求解器的方向自学习（线上两种都出现过）。

## 与线上的对应关系（实测值）

| 项 | 值 |
|---|---|
| 画布 | canvas 420×428，前景层画实心 + 滑轨 + 手柄 + Submit |
| 背景层 | CSS `background-image`（深色面板 + 32px 网格 + 空心轮廓）—— 与线上同构，`bgDataUrl()` 能取到 |
| 滑轨 | `y=348`，`x0=24 → x1=394` |
| 手柄 | 直径 22，初始圆心 `x=28` |
| Submit | 中心 `(210, 402)`，尺寸 140×36（**画在 canvas 内，DOM 没有节点**） |
| 行程比 | **0.317**（手柄走 1px，图形走 0.317px） |
| 判定 | `|实心x − 空心x| ≤ 6` 算通过 |
| 干扰项 | DOM 里同样有 `<button id="swap">Try another verification</button>`——点它只换题，绝不通过 |

## 回归结果（2026-11-16 18:16）

```
正向 4 轮：truth 103 / 65.3 / 105 / 107   got 102 / 65 / 104 / 105.5   误差 0.3~1.5px
反向 3 轮：truth -106.6 / -64.5 / -65.5   got -107 / -64.5 / -65       误差 0~0.5px
pass_rate = 1.0
```

说明「识图 + 实心/空心配对 + dx」这一段在 mock 上已经 100% 准，
线上失败的原因不在算法，而在**拖动执行**（findBall 范围 / 1:1 比例 / 点错 `#swap` → 均已修，待线上复验）。

## mock 简化了什么

- 没有 WAF 的会话校验、没有 `HWWAFSESID` 之类 cookie，也不做风控节流；
- 不校验事件 `isTrusted`，所以合成事件也能拖动（线上必须真实输入或 chrome.debugger）；
- 题目只影响形状族/颜色/位置，不做难度递增。

这些差异意味着：**mock 通过 ≠ 线上通过**。mock 只保证「算法这一层是对的」，
拖动/时序/风控仍需线上验证。
