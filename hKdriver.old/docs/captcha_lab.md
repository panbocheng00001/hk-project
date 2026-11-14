# captcha-lab 接线说明（华为 WAF 几何重合滑块）

把 `captcha-lab` 作为识别内核接进 hKdriver：遇到"拖动滑块让实心图形和空心图形
重合"的华为 WAF 验证码时，自动算出位移并完成拖动。

- 识别/轨迹内核：`../captcha-lab`（判据与 `captcha_mock/analyze.js` 逐条对齐）
- 桥文件：`captcha_lab_bridge.py`
- 接线：`slider.py`（`solve()` / `human_drag()`）
- 复刻服务（回归基准）：`captcha_mock`（`node server.js` → `127.0.0.1:8777`）

---

## 1. 组件关系

```
slider.solve(page)
  ├─ 截图 canvas
  ├─ captcha_lab_bridge.solve_canvas(png)   # 首选，source=captcha_lab
  │     └─ solver.overlap.solve_canvas       # 与 analyze.js 同判据
  ├─ slider.locate_shapes(png)              # 回退，source=legacy
  └─ slider.human_drag(page, handle, slider_dx)
        └─ captcha_lab_bridge.make_track()   # stealth 拟人轨迹 + 风控自检
              事件走 page.mouse（CDP Input.dispatchMouseEvent，isTrusted=true）
```

**提交口径**（已实测固定）：

- 画布位图 400×330；滑轨 400，手柄 34；`range = max(10, trackW - handleW) = 366`
- `slider_dx = dx * range / bmpW`
- 判分容差 3 位图像素

---

## 2. 桥接 API（`captcha_lab_bridge.py`）

```python
from captcha_lab_bridge import solve_canvas, make_track, BridgeUnavailable

res = solve_canvas(png_bytes_or_dataurl, track_w=400, handle_w=34, bmp_w=None)
# -> {ok, dx, slider_dx, current_x, target_x, pairs, level, bmp_w, error, elapsed_ms}

track = make_track(res["slider_dx"], profile="stealth", seed=None)
# -> [(x, y, t), ...]  末点 x == slider_dx，t 毫秒单调，风险分 < 0.5
```

导入策略（两级回退）：先按已 `pip install` 的 captcha-lab 直接 `import solver`；
失败则把源码目录塞进 `sys.path` 再试（目录取环境变量 `CAPTCHA_LAB_PATH`，
缺省为同级 `../captcha-lab`）；两级都失败抛 `BridgeUnavailable`，由 `slider.py`
打日志后回退 legacy，**不静默**。

---

## 3. 开关：`CAPTCHA_LAB`

| 值 | 行为 |
| --- | --- |
| 未设 / `1`（默认） | 识别走 captcha-lab 内核，轨迹走 `make_track` |
| `0` | 强制回退：识别用 `locate_shapes`，轨迹用本文件 `_track` |

```powershell
# 临时回退到 legacy 实现（排查内核问题时用）
$env:CAPTCHA_LAB = '0'
# 指定非默认的 captcha-lab 目录
$env:CAPTCHA_LAB_PATH = 'E:\path\to\captcha-lab'
```

日志（logger 名 `hkdriver.slider`）会打印：来源（`captcha_lab`/`legacy`）、
`dx`、`slider_dx`、识别耗时、拖动点数与耗时。

---

## 4. 启动复刻服务（回归基准）

```powershell
cd E:\googleProduct\hk-project\hKdriver\captcha_mock
node server.js
# [captcha-mock] http://127.0.0.1:8777  geom={...} tol=3px
```

端点：`/`（题目页）、`/challenge?seed=`、`/analyze`、`/grade`、`/healthz`。
**不要改它的判据与容差**——它是回归基准。

---

## 5. 跑评测与测试

```powershell
# 端到端对拍（captcha-lab vs analyze.js vs 真值），需服务在线
cd E:\googleProduct\hk-project\captcha-lab
python bench_waf.py --n 30            # 期望 30/30

# 桥接单测（在线跑集成用例，离线自动 skip 集成部分）
cd E:\googleProduct\hk-project\hKdriver
python -m pytest test_captcha_lab_bridge.py -q
```

---

## 6. 排障

- **httpx 连 127.0.0.1 返回 503 / 空响应**：本机配了系统代理，httpx 默认把
  localhost 也走代理（urllib/node 会自动绕过）。给命令加环境变量即可：
  ```powershell
  $env:NO_PROXY = '127.0.0.1,localhost'
  ```
- **`BridgeUnavailable`**：captcha-lab 没装也不在默认目录。设 `CAPTCHA_LAB_PATH`
  指向源码目录，或 `pip install -e ../captcha-lab`。确认 `opencv` 与 `numpy` 可用。
- **集成用例被 skip**：说明复刻服务没在线。先 `node server.js` 再跑 pytest。
- **想临时停用内核**：`$env:CAPTCHA_LAB = '0'`，行为回到改造前的 `slider.py`。
- **dx 对但判分不过**：检查 `track_w`/`handle_w`/`bmp_w` 是否与页面实测一致
  （实测 400/34/400）；`slider_dx` 换算错会差一个缩放系数。
- **拖动没反应**：确认 `human_drag` 用的是 `page.mouse`（可信输入），
  且手柄 selector 命中；日志会打印"取不到手柄 bounding_box"。
```
