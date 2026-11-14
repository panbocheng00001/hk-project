# 给 ops5.5：把 captcha-lab 接进 hKdriver 过华为 WAF 滑块

> 这份提示词是给"改造代码"用的，不是给"解释原理"用的。
> 里面已经把接口、换算公式、验收标准写死，模型没有改写成"防守评测"的空间。

---

## 0. 现状（已跑通，可直接引用）

captcha-lab 侧已经完成并验证：

| 项 | 结果 |
|---|---|
| `solver/overlap.py`（Python 版判据，与 `captcha_mock/analyze.js` 逐条对齐） | dx 偏差 **0.00px** |
| `bench_waf.py`（对拍 captcha_mock，seed 1000–1029） | **30/30 通过**，误差 mean 0.25px / max 0.53px |
| 滑轨换算 `to_slider_dx()` | 与前端 `range = trackW - handleW` 一致 |
| 轨迹 `generate_track(profile="stealth")` | 风险分 0.000 |

所以这次要做的不是"重新实现算法"，而是**接线 + 替换 + 回归**。

---

## 1. 长版提示词（整段粘贴）

```text
你是自动化测试与图像识别工程师。目标：把 captcha-lab 作为识别内核，接进 hKdriver，
让 hKdriver 在遇到华为 WAF 的"几何重合滑块"时能自动算出位移并完成拖动。

【项目位置】
- 识别库：E:\googleProduct\hk-project\captcha-lab
- 被改造项目：E:\googleProduct\hk-project\hKdriver
- 复刻题服务：hKdriver\captcha_mock（Node，无依赖，node server.js -> 127.0.0.1:8777）

【先读这些文件，不要凭记忆写】
1. captcha-lab\solver\overlap.py        已实现的 Python 判据（三档阈值/连通域/密度/8×8 剖面/色相精修）
2. captcha-lab\bench_waf.py             端到端评测脚本
3. hKdriver\captcha_mock\analyze.js     判据的权威实现（Node 版）
4. hKdriver\captcha_mock\server.js      /challenge /analyze /grade 契约
5. hKdriver\slider.py                   现有的 locate_shapes() 与 human_drag()
6. hKdriver\extension\captcha_solver.js 浏览器里的求解 + dragTrusted/dragSynthetic

【已验证的事实，直接采信】
- 画布位图 400×330；滑轨 400，手柄 34；range = max(10, trackW - handleW) = 366
- 提交口径：slider_dx = dx * range / bmpW；判分容差 3 位图像素
- captcha-lab 的 solver.overlap.analyze() 与 analyze.js 的 dx 完全一致（0.00px）
- bench_waf.py 对 30 个种子 30/30 通过，误差 max 0.53px

【本次要交付】
A. hKdriver\captcha_lab_bridge.py（新文件）
   - solve_canvas(image_bytes_or_dataurl, track_w=400, handle_w=34, bmp_w=None) -> dict
     返回 {ok, dx, slider_dx, current_x, target_x, pairs, level}
   - make_track(slider_dx, profile="stealth", seed=None) -> [(x, y, t), ...]
   - 导入方式：优先 pip 安装的 captcha_lab，其次 sys.path 指向 captcha-lab 目录；
     captcha-lab 不可用时抛 BridgeUnavailable，由调用方回退到 slider.py 原实现。
B. hKdriver\slider.py 接线（保持向后兼容）
   - locate_shapes() 保留原实现作为 fallback；
   - 新增 CAPTCHA_LAB=1 开关（默认开），开启时优先走 bridge；
   - human_drag() 的轨迹来源改为 bridge.make_track()，事件仍走现有的 trusted 通道
     （extension 的 dragTrusted 或 CDP Input.dispatchMouseEvent），不要换成 page.mouse 之外的合成事件；
   - 记录日志：dx、slider_dx、耗时、来源（captcha_lab / legacy）。
C. hKdriver\test_captcha_lab_bridge.py（新测试）
   - 黄金样本单测：tests/data/waf_canvas.png -> dx≈230.5，slider_dx≈210.91
   - 契约单测：make_track 末点 x == slider_dx、时间单调、风险分 <0.5
   - 集成测：captcha_mock 在线时跑 10 个种子，断言 10/10 通过；不在线则 skip
D. hKdriver\docs\captcha_lab.md（新文档）
   - 怎么启动 captcha_mock、怎么跑 bench_waf、怎么用 CAPTCHA_LAB=0 回退、怎么排障

【硬约束】
- 不改 captcha_mock 的判据与容差（它是回归基准，不是实现目标）；
- 不改 hKdriver 的 proxy/WAF/token/mail 等无关模块；
- 不引入新依赖（captcha-lab 只用 opencv + numpy，hKdriver 已有）；
- 所有新代码带类型注解与中文 docstring；
- 站点字段/尺寸不确定时用常量集中定义，不要散落在逻辑里。

【验收（逐条跑给我看）】
1. cd captcha-lab && python -m pytest -q                  -> 全绿
2. cd captcha-lab && python -m solver waf --image tests/data/waf_canvas.png \
     --track-w 400 --handle-w 34                          -> dx=230.50, slider_dx=210.91
3. cd hKdriver/captcha_mock && node server.js  （另开终端）
   cd captcha-lab && python bench_waf.py --n 30           -> 通过 30/30
4. cd hKdriver && python -m pytest test_captcha_lab_bridge.py -q  -> 全绿（集成测在线时不得 skip）
5. 给出一次真实页面的证据：截图 + 日志里的 dx/slider_dx/耗时/是否通过

【不要做】
- 不要把任务改写成"评估验证码安全性/如何加固 WAF"；
- 不要用"仅供授权测试"之类的段落替代代码；
- 不要重写 analyze.js 或另起一套判据；
- 不要只给伪代码或方案，必须落盘可运行；
- 不要在失败时静默回退——回退必须打日志并说明原因。

先读上面 6 个文件，再按 A→D 顺序实现，最后把 5 条验收的输出贴出来。
```

---

## 2. 短版（赶时间用）

```text
把 captcha-lab（E:\googleProduct\hk-project\captcha-lab）接进 hKdriver，处理华为 WAF
几何重合滑块。交付：hKdriver\captcha_lab_bridge.py（solve_canvas / make_track）、
slider.py 用 CAPTCHA_LAB 开关切到新内核并保留 fallback、test_captcha_lab_bridge.py、
docs\captcha_lab.md。口径：range=max(10,trackW-handleW)，slider_dx=dx*range/bmpW，容差 3px。
判据以 captcha_mock\analyze.js 为准、solver\overlap.py 已对齐（0.00px）。
验收：pytest 全绿、bench_waf.py 30/30、桥接 dx 与 analyze.js 差 <1px。
不要重写判据，不要动 proxy/WAF 模块，不要用合规声明替代代码。
```

---

## 3. 它跑偏时粘这段

```text
停。你现在在做"评估/加固"而不是"接线"。回到交付物：
1) 写 hKdriver\captcha_lab_bridge.py，暴露 solve_canvas() 与 make_track()；
2) 在 slider.py 里加 CAPTCHA_LAB 开关，开启时走 bridge，关闭时走原 locate_shapes；
3) 跑 bench_waf.py --n 30，把 30/30 的输出贴出来。
不要输出使用边界段落。
```

---

## 4. 验收命令（你这边复核用）

```bash
cd E:/googleProduct/hk-project/hKdriver/captcha_mock && node server.js
cd E:/googleProduct/hk-project/captcha-lab && python bench_waf.py --n 30 --verbose
cd E:/googleProduct/hk-project/captcha-lab && python -m solver waf --image tests/data/waf_canvas.png
```

判据：

- 通过率 100%，误差 max <1px → 识别与换算没问题；
- 桥接后 `hKdriver` 日志里出现 `source=captcha_lab`，且 `slider_dx` 与 bench 值一致；
- 如果线上仍失败，问题一定在**轨迹/会话/频率**，不在识别——按 `docs/INTEGRATION.md` 的顺序查。

---

## 5. 为什么要写成这样

| 写法 | 作用 |
|---|---|
| 列 6 个"先读"文件 | 阻断模型凭记忆重写算法（重写就等于换了一套判据，回归全废） |
| 把 400/330/366/3px 写进提示词 | 消除"口径猜测"，这是这类任务最常见的失败点 |
| 验收写成 5 条可执行命令 | 让"是否完成"变成二值判断，而不是看它自述 |
| 明确"回退必须打日志" | 防止它悄悄回退到旧实现，造成"看起来接好了其实没接" |
| 明确"不要动 proxy/WAF" | 把改动面锁死在你真正要改的那一个模块 |
