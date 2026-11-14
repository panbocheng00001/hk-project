# captcha-lab

**滑块验证码识别工具包** — 图片进，拖动距离 / 拟人轨迹 / 站点载荷出。

A public, production-oriented slider-captcha solver toolkit: gap localization, human-like
drag trajectories, per-vendor payload adapters, HTTP service and browser driver.

[![ci](https://github.com/your-org/captcha-lab/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

---

## 1. 它解决什么问题

滑块验证码是"图像理解 + 行为模拟"两道关。开源方案大多只覆盖其中一半：
要么能找缺口但轨迹一眼假，要么轨迹做得像但识别靠单一方形模板，换个站点就崩。

这个仓库把两半都做成可复用组件：

| 能力 | 模块 | 状态 |
|---|---|---|
| 缺口定位（多策略融合 + 保守精修 + top-k 重试） | `solver/gap.py` | ✅ 一次命中 96.7% |
| 几何重合滑块（实心图形对齐空心轮廓，华为 WAF 形态） | `solver/overlap.py` | ✅ 30/30，与 JS 判据 0.00px 偏差 |
| 统一求解引擎（图片 → 距离 → 轨迹 → 载荷） | `solver/engine.py` | ✅ |
| 拟人轨迹（最小加加速度 + 手颤 + 停顿 + 过冲回拉） | `solver/track.py` | ✅ 4 套档案，风险分 0.00 |
| 站点适配（极验 / 腾讯 / 易盾 / 阿里 / 顶象 / 通用） | `solver/profiles.py` | ✅ 数据驱动 |
| 对接层（回调流程 / JSON 接口 / 真实浏览器） | `solver/client.py` | ✅ |
| HTTP 服务（鉴权、限流、并发保护、Prometheus 指标） | `solver/serve.py` | ✅ |
| CLI（单张 / 批量评测 / 轨迹 / 起服务） | `solver/cli.py` | ✅ |
| 合成数据与 YOLO 训练脚手架 | `solver/synth.py`、`solver/train_yolo.py` | ✅ |
| 评测与对抗研究（可选） | `defense/` | ✅ 见第 9 节 |

---

## 2. 30 秒上手

```bash
pip install -e ".[server,http]"      # 或 pip install -r requirements.txt

# 1) 单张识别：打印距离、置信度、轨迹风险分，并生成标注图
python -m solver solve --bg bg.png --piece piece.png --display-width 340 --debug-out debug.png
#    题图在 CDN 上时可直接传 URL
python -m solver solve --bg "https://cdn.example.com/bg.png" --display-width 340

# 2) 只生成轨迹（给已有识别结果的链路用）
python -m solver track --distance 212 --profile stealth --json

# 3) 合成样本端到端评测
python -m solver bench --n 100

# 4) 起 HTTP 服务
python -m solver serve --port 8100     # http://127.0.0.1:8100/docs
```

Python 三行：

```python
from solver import SliderSolver

solver = SliderSolver(site="generic", track_profile="stealth")
result = solver.solve("bg.png", piece="piece.png", display_width=340)

print(result.distance, result.confidence)     # 212.4 0.93
print(result.payload)                         # {"x": 212.4, "track": [[0, 0, 0], ...]}
```

---

## 3. 实测精度与性能

环境：Windows / Python 3.12 / OpenCV 4.13 / 单线程 BLAS，样本为 `solver.synth` 生成的
340×212 拼图滑块（缺口 46px，含描边与阴影）。

```text
$ python -m solver bench --n 60
样本 60  容差 4px  平均 66.2ms/张
一次命中  58/60 = 96.7%
候选命中  60/60 = 100.0%（top3）
```

| 指标 | 数值 | 说明 |
|---|---|---|
| hit@1 ≤4px | **96.7%** | 多数站点容差 5–8px，4px 内基本一次过 |
| hit@top3 ≤4px | **100%** | top-k 是"业务成功率"的来源，配合重试 |
| 单张耗时 | **~66ms** | 340×212，纯 CPU，无模型 |
| 轨迹风险分 | **0.000** | 4 套档案经 `defense.trajectory` 自检 |

> 合成样本只能证明"流程正确、算法没退化"。真实站点要自己采集 50–200 张带标注的图，
> 用 `python bench_gap.py` 的口径复测；见第 8 节。

---

## 4. 架构

```
                 ┌──────────────────────────────┐
   bg.png ──────▶│  gap.locate  (多策略融合)      │
   piece.png ───▶│  box / canny / template /     │──▶ Candidate[] (top-k)
   full_bg.png ─▶│  shadow / projection / diff   │
                 └──────────────┬───────────────┘
                                │ 保守精修 ±2.5px
                                ▼
                 ┌──────────────────────────────┐
                 │  engine.SliderSolver          │
                 │  · answer_mode: center / left │──▶ SolveResult
                 │  · scale: natural → CSS px    │     x / distance / confidence
                 │  · softmax 置信度 + 候选去重   │     answers[] / payload
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐     ┌────────────────────┐
                 │  track.generate_track         │────▶│ profile.build_payload│
                 │  运动学 + 人类噪声 + 自检      │     │ 字段/格式/口径适配    │
                 └──────────────────────────────┘     └────────────────────┘
```

模块职责：

| 文件 | 职责 |
|---|---|
| `solver/gap.py` | 6 种定位策略、族内投票融合、保守精修、可视化 |
| `solver/overlap.py` | 几何重合滑块：前景三档阈值、实心/空心配对、8×8 剖面、dx → 滑轨换算 |
| `solver/engine.py` | 求解编排、口径换算、置信度、top-k 重试闭环 `solve_until` |
| `solver/track.py` | 4 套轨迹档案、6 种产物格式、轨迹体检 `analyze` / `audit` |
| `solver/profiles.py` | 站点档案（答案口径、字段名、缩放规则） |
| `solver/client.py` | `SliderFlow` / `JsonSliderFlow` / `PlaywrightSlider` |
| `solver/serve.py` | FastAPI：`/v1/solve`、`/v1/track`、`/metrics` |
| `solver/cli.py` | 命令行入口 |
| `solver/synth.py` | 合成样本 + YOLO 数据集生成 |
| `defense/` | 出题、行为风控、验题服务（评测用，可选） |

---

## 5. 识别内核：三个把命中率从 88% 拉到 96.7% 的点

**① 融合时按"策略族"投票，而不是逐条候选累加。**
多尺度盒能量会给同一个区域产出 3–6 条候选，逐条累加会让"碰巧被多个尺度命中"的
背景区域压过真缺口。现在先按族（box/canny/template/shadow/projection/diff）取最高票，
再跨族求和——每个策略对同一位置最多投一票。

**② 用滑块尺寸当先验。**
拿到拼图块时，其可见宽度就是缺口宽度的强先验；尺寸偏离的候选降权，
直接消掉"框大了"这一类最常见的错法。

**③ 精修只在 ±2.5px 内做。**
早期版本的"亚像素精修"会在 ±30% 宽度内搜索梯度峰，实测经常抓到缺口右下角的阴影边，
把 1px 误差放大到 5–9px。现在改成局部"四边梯度 − 内部梯度"最大化，位移超过
2.5px 一律不采纳。

另外修掉一个隐蔽 bug：`"diff" in method` 会命中 `tpl_sqdiff`，让模板候选白拿 0.6 分。
回归测试见 `tests/test_engine.py`。

---

## 6. 轨迹生成：能过规则型风控的可解释轨迹

```python
from solver import generate_track, analyze

pts = generate_track(212, profile="stealth", audit_risk=True)
print(analyze(pts)["risk"])          # 0.000
```

三层结构：

1. **运动学**：最小加加速度曲线 `10u³−15u⁴+6u⁵`，慢起—快中—慢收，符合人手点到点运动。
2. **人类噪声**：逐点手颤、低频 y 漂移、随机停顿、末段过冲回拉、混合小数位坐标。
3. **格式适配**：`to_payload(pts, fmt)` 输出 `xyt / xy / x / geetest / tencent / object`。

| 档案 | 点数 | 时长 | 适用 |
|---|---|---|---|
| `mouse` | 45–72 | 0.6–1.4s | 桌面站点，通用默认 |
| `touch` | 28–46 | 0.5–1.2s | 移动端 H5，y 漂移更大 |
| `fast` | 16–28 | 0.2–0.8s | 快手用户，仍保留手颤 |
| `stealth` | 46–70 | 0.7–1.5s | 对抗行为风控，噪声与停顿最足 |

`audit_risk=True` 时用仓库自带的规则风控自检，不达标自动重采样（最多 8 次）。
这把"轨迹像不像人"变成了可回归的指标，而不是靠感觉。

`python bench_detector.py` 的对照：

```
linear       catch=100.0%     # 匀速等间隔
human_track  catch=100.0%     # 旧版数学拟人轨迹
gen:fast     catch=  0.0%     # 新版生成器
gen:stealth  catch=  0.0%
```

结论很直接：**规则型风控只能拦低成本脚本**。真要对抗日志坚定的自动化，
防守方需要叠加设备指纹、事件间隔分布、页面环境一致性等维度，单看轨迹不够。
这个仓库的 `defense/` 就是用来量化这条边界的。

---

## 7. 站点适配

内置档案（`solver/profiles.py`）：

| 档案 | 口径 | 轨迹格式 | 容差 |
|---|---|---|---|
| `generic` | 缺口中心 | `xyt` | 5px |
| `geetest` | 拼图块左边缘 | 整数三元组 + `passtime` | 5px |
| `tencent` | 缺口中心 | 整数三元组 | 6px |
| `netease` | 拼图块左边缘 | `xyt` | 5px |
| `aliyun` | 缺口中心 | `xyt` | 6px |
| `dingxiang` | 拼图块左边缘 | `xyt` | 5px |

换站点通常只改两个字段：

```python
from solver.profiles import SiteProfile, build_payload

my_site = SiteProfile(
    name="my-site",
    answer_mode="left",              # 提交左边缘还是中心
    track_format="xyt",              # 轨迹序列化格式
    fields={"distance": "offset",    # 字段名
            "track": "mouse_track",
            "passtime": "cost"},
)
```

站点改版后的排查顺序：先看 `--debug-out` 的标注图确认缺口对不对 →
再看 `payload` 字段名和口径 → 最后才怀疑轨迹。90% 的"过不去"是口径错了半个滑块宽度。

---

## 8. 几何重合滑块（华为 WAF 形态）

题型：深色网格画布上散落若干几何图形，其中一对是「同形同色的实心 + 空心轮廓」。
拖动滑块使实心图形与空心轮廓完全重合。它没有"缺口"，要算的是**两个图形中心的水平差**。

```bash
python -m solver waf --image canvas.png --track-w 400 --handle-w 34 --debug-out debug.png
```

```
实心中心   = 104.5px    空心中心 = 335.0px
画布 dx    = 230.50px（配对 4 块，阈值档 0）
滑轨位移   = 210.91px（range = 400 - 34）
轨迹       = 56 点 / 1365ms / 风险分 0.000
```

前端提交的是滑轨像素，不是画布像素：

```python
from solver import to_slider_dx
slider_dx = to_slider_dx(dx=230.5, bmp_w=400, track_w=400, handle_w=34)   # 210.91
# 等价于 range = max(10, trackW - handleW);  slider_dx = dx * range / bmpW
```

判据与前端脚本逐条对齐（三档亮度/饱和度阈值 → 4 邻域连通域 → 填充密度 0.45 分实心/空心
→ 空心填充后比对 8×8 占用剖面 → 尺寸/高度/色相差加权评分 → 色相精修）。

与 `hKdriver/captcha_mock` 对拍（Node 版 `analyze.js` 同一套判据）：

```bash
cd E:/googleProduct/hk-project/hKdriver/captcha_mock && node server.js
python bench_waf.py --n 30
```

```
样本 30  容差 3px
通过      30/30 = 100.0%
误差(真值) mean=0.25px  p95=0.47px  max=0.53px
与 JS 判据对齐  mean=0.00px  max=0.00px
```

> 注意：在复刻题上 100% 只证明"识别 + 换算"正确，不证明能过 WAF 的风控层。
> 真实站点还要过轨迹、会话与频率三道关，见 [docs/INTEGRATION.md](docs/INTEGRATION.md)。

---

## 9. 三种对接形态

> 从“识别对”到“真的过”的完整链路（口径陷阱、失败归因、端到端脚本）见
> [docs/INTEGRATION.md](docs/INTEGRATION.md)。

**① 回调流程**（已有请求链路，最灵活）

```python
from solver import SliderSolver
from solver.client import SliderFlow

def fetch(attempt):
    d = api.get_challenge()
    return {"bg": d["bg"], "piece": d["piece"], "display_width": d["width"], "_token": d["token"]}

def submit(answer, payload):
    return api.verify(token=current_token, **payload)["ok"]

result = SliderFlow(SliderSolver(site="geetest"), fetch, submit).run(max_attempts=3)
```

**② 标准 JSON 接口**（题目下发 + x/track 校验）

```python
from solver.client import EndpointConfig, HttpxClient, JsonSliderFlow

flow = JsonSliderFlow(
    SliderSolver(site="generic"),
    HttpxClient("https://example.com", cookies={"session": SESSION}),
    EndpointConfig(challenge_path="/api/captcha/new",
                   verify_path="/api/captcha/check",
                   bg_field="bg", piece_field="piece",
                   token_field="token", success_field="code", success_value=0),
)
print(flow.run(max_attempts=3).ok)
```

**③ 真实浏览器**（页面渲染 + 真实鼠标事件流）

```python
from playwright.sync_api import sync_playwright
from solver import SliderSolver
from solver.client import BrowserSelectors, PlaywrightSlider

with sync_playwright() as p:
    page = p.chromium.launch(headless=False).new_page()
    page.goto(URL)
    PlaywrightSlider(SliderSolver(site="generic")).solve_page(
        page, BrowserSelectors(bg=".captcha-bg", piece=".captcha-piece",
                               handle=".slider-handle"))
```

`PlaywrightSlider` 用元素截图取图、用 `mouse.move/down/up` 按轨迹时间轴回放，
不注入 JS 补丁，事件流与真人操作同源。

---

## 10. HTTP 服务

```bash
CAPTCHA_API_KEY=dev-key CAPTCHA_RATE_PER_MIN=120 python -m solver serve --port 8100
```

| 接口 | 作用 |
|---|---|
| `POST /v1/solve` | 图片（base64/URL/路径）→ `x` / `distance` / `confidence` / `track` / `payload` |
| `POST /v1/track` | 距离 → 轨迹 + 载荷 + 特征 |
| `GET /v1/profiles` | 可用站点 / 轨迹档案 |
| `GET /metrics` | 调用量、错误数、p50/p95/p99 耗时（Prometheus 文本） |
| `GET /healthz` | 存活探针 |

```bash
curl -s http://127.0.0.1:8100/v1/solve \
  -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{"bg":"<base64>","piece":"<base64>","display_width":340,"site":"geetest"}'
```

生产注意：

- **并发**：识别是 CPU 密集型，`CAPTCHA_MAX_CONCURRENCY` 控制同时在跑的任务数，超限返回 503 而不是排队拖垮进程。
- **部署**：`docker build -t captcha-lab . && docker run -p 8100:8100 captcha-lab`，多副本时把密钥放在网关层，服务保持无状态。
- **降级**：置信度低于阈值（如 0.6）时直接返回候选列表，让上层决定是重试还是换通道，不要盲目提交。
- **观测**：以 `/metrics` 的 p95 和错误率做告警；识别失败率突增通常意味着站点改版（先看 `debug_image`）。

---

## 11. 训练与自建数据

冷启动没有标注时，合成数据够用：

```bash
python -m solver.synth dataset 500                 # 生成 YOLO 格式数据集
python -m solver.train_yolo --data dataset/data.yaml   # 需要 ultralytics
```

真实场景的正确姿势是"少量真实 + 大量合成"：

1. 采 50–200 张真实题图，人工标注缺口中心；
2. 用 `bench_gap.py` 的口径算 hit@1，确认你的站点属于哪一档难度；
3. 把真实图作为 hard sample，与合成数据混合训练检测器（`solver/train_yolo.py`）；
4. 检测器输出与 `gap.locate` 融合：模型给候选，几何给精度。

标注格式：`{"file.png": {"cx": 212, "cy": 96, "w": 46, "h": 46}}`，
`python -m solver batch --dir samples --gt labels.json` 直接出报表。

---

## 12. 测试

```bash
pytest -q                       # 166 个测试
python -m solver bench --n 60   # 精度回归
python bench_detector.py        # 轨迹风控对照
```

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_engine.py` | 命中率、口径换算、缩放、base64/bytes、重试闭环 |
| `tests/test_track_gen.py` | 终点精确、时间单调、4 档案自检、可复现 |
| `tests/test_profiles.py` | 字段映射、6 种轨迹格式、缩放规则 |
| `tests/test_serve.py` | 接口契约、鉴权、错误码、指标 |
| `tests/test_challenge.py` / `test_curve.py` / `test_trajectory.py` / `test_service.py` | 出题、曲线题、轨迹风控、验题服务 |

---

## 13. 路线图

- [x] 多策略融合 + 保守精修 + top-k 重试
- [x] 4 套轨迹档案 + 风控自检
- [x] 站点档案与三种对接形态
- [x] HTTP 服务（鉴权 / 限流 / 指标）
- [ ] ONNX 检测器（把 `gap.locate` 的 box 能量换成学习模型，目标 hit@1 ≥98%）
- [ ] 点选 / 文字点选题型的统一接口（`solver/points.py` 已有基线）
- [ ] 轨迹档案的真人样本拟合（用录制的真人轨迹回归参数）
- [ ] Redis 版任务队列与分布式限流
- [ ] 浏览器指纹与环境一致性检查（配合 `defense/` 做对抗评测）

---

## 14. 项目结构

```
captcha-lab/
├─ solver/
│  ├─ engine.py        # 求解引擎（入口）
│  ├─ gap.py           # 缺口定位
│  ├─ overlap.py       # 几何重合滑块（华为 WAF 形态）
│  ├─ track.py         # 轨迹生成
│  ├─ profiles.py      # 站点档案
│  ├─ client.py        # 对接层（回调 / JSON / 浏览器）
│  ├─ serve.py         # HTTP 服务
│  ├─ cli.py           # 命令行
│  ├─ synth.py         # 合成数据
│  ├─ train_yolo.py    # 检测器训练脚手架
│  ├─ points.py / shape_match.py / synth_match.py / curve_match.py   # 其它题型基线
├─ defense/            # 出题 / 行为风控 / 验题服务（评测用）
├─ tests/              # 166 个测试
├─ examples/           # 可直接运行的示例
├─ bench_gap.py / bench_match.py / bench_detector.py / bench_robustness.py
├─ bench_waf.py        # 几何重合滑块端到端评测（对拍 captcha_mock）
├─ Dockerfile / pyproject.toml / requirements.txt
```

---

## 15. 贡献

- 新的站点档案：在 `solver/profiles.py` 加一条 + 一个 payload 测试即可。
- 新的定位策略：实现 `_strategy_xxx(bg) -> List[Candidate]`，加入 `gap.locate`，
  在 `bench_gap.py` 上报 hit@1/top3 的变化。
- 新的轨迹档案：在 `solver/track.py` 的 `PROFILES` 加一条，`pytest tests/test_track_gen.py`
  会自动验证它是否能过风控自检。

MIT License。
