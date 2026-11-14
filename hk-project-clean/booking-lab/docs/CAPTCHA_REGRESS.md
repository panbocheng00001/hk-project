# CAPTCHA_REGRESS —— 复刻题回归闸门

> 这份文档回答一个问题：**改了验证码相关代码之后，"这条链路还能跑通吗"能不能用一条命令、一个退出码回答。**
>
> 命令：`python tools/captcha_regress.py --n 30`
> 它只做三件事：**固定题号、连跑、给退出码**。不含任何识别逻辑。

---

## 1. 它解决什么问题

`tools/bench_captcha_auto.py` 能跑分，但**不可复现**：复刻题页面加载时自己调 `newChallenge()`
且不传 seed，服务端每次随机出题，脚本里的 `?n=<i>` 页面并不解析。所以"通过 20/20"这个数字
换一次跑就变，没法当回归依据。

`captcha_regress.py` 把题号钉死：页面里 `newChallenge(seed)` 是全局函数，脚本用
`page.evaluate("(s) => newChallenge(s)", seed)` 出题，再读 `#seed` 回显确认服务端确实用了该题号。
同一 `--start/--n` 重复跑，题号段与逐题结果完全一致。

---

## 2. 怎么跑

```bash
# 1) 起复刻题服务（零依赖，仅监听 127.0.0.1:8777）
cd booking-lab/captcha_mock && node server.js

# 2) 另开终端，跑闸门
cd booking-lab
python tools/captcha_regress.py --n 30                      # 默认题号段 1000..1029
python tools/captcha_regress.py --n 30 --json runs/regress.json
python tools/captcha_regress.py --n 5 --headed              # 排障用，有头看拖动
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--n` | 30 | 题数 |
| `--start` | 1000 | 起始题号；题号段固定 ⇒ 结果可复现 |
| `--base` | `http://127.0.0.1:8777` | 复刻题服务地址 |
| `--fail-under` | 1.0 | 通过率门槛 |
| `--json` | 无 | 逐题结果落盘（至少含 `{n, seed, ok, pass, submitted_dx, truth_dx, diff, elapsed_ms}`） |
| `--headed` | 关 | 有头运行 |

健康检查使用标准库 `urllib` 的空代理表访问 `127.0.0.1`，不继承系统代理。

---

## 3. 退出码（可直接挂 CI）

| 码 | 含义 | 通常是 |
|---|---|---|
| 0 | 通过率达到 `--fail-under` | 正常 |
| 1 | 未达标，或一题都没跑到 | 看 `失败题号` 与 `note` |
| 3 | 复刻题服务没起 | 按提示执行 `node server.js` |
| 4 | 浏览器不可用（playwright 未装 / 起不来） | `pip install playwright && playwright install chromium` |

---

## 4. 输出长什么样（2026-11-15 实测）

```text
题号段 1000..1029（30 题），服务 http://127.0.0.1:8777
 n  seed        ok    pass  submitted   truth    diff  elapsed  note
----------------------------------------------------------------------------------------
 1  1000        True  True      230.50  230.50    0.00    3985ms
  ...
----------------------------------------------------------------------------------------
摘要：通过 30/30 (100.0%) | 误差 mean=0.2xxpx p95=0.4xxpx max=0.5xxpx | 总耗时 ~115s
逐题结果：runs\regress.json
```

误差口径来自页面自己的 `window.__grade`（`truth_dx` / `submitted_dx` / `diff`），
判据与容差仍由 `captcha_mock` 与 `captcha-lab/solver` 决定，本脚本不重算位移。
`seed` 取页面 `#seed` 的实际回显；与请求题号不一致时该题记失败，不调用 `solve_page`。

---

## 5. 离线用例

```bash
cd booking-lab && python -m pytest tests/test_captcha_regress_offline.py -q   # 14 passed
```

覆盖：题号段生成、统计口径（通过数 / mean / p95 / max / 空输入）、退出码 0/1、
服务没起 → 3、浏览器缺失 → 4、页面题号校验、回环禁用系统代理、`--json` 落盘结构。
全部离线，不启动浏览器、不连网络。

---

## 6. 边界（这条脚本不做什么）

| 不做 | 原因 |
|---|---|
| 不实现 / 不修改任何识别算法、判据、容差 | 它只调用 `captcha_auto.solve_page` 与内核，不产生位移 |
| 不加新题型、不改重试策略 | 那是加速层的事，改它要单独评审 |
| 不改 `captcha_mock` 的接口与判据 | 它是基准，不是实现目标 |
| 不碰 `captcha_flow/` | 决策层契约由 30 项测试守着，本脚本一行不涉及 |

它证明的是**既有链路在同一批题上仍然通过**，不证明任何外部环境下的可用性。
