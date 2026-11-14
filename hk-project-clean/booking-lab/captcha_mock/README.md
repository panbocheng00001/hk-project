# 验证码复刻服务（captcha_fixture）——让你在本地打靶练脚本

一套零依赖的 Node 服务：**几何、画布、接口形态都与线上那道华为 WAF 滑块一致**，真值只在本地返回。
用途只有一个——你的脚本先在它身上跑通，再拿去打真站，避免「线上一次失败就得等下次弹码」。

```
captcha_fixture/
  server.js      HTTP 服务（/challenge /analyze /grade /specimens /specimen/:id）
  challenge.js   按实测参数生成同构题面（400×330 深色网格 + 干扰轮廓 + 同形同色实心/空心对）
  analyze.js     求解算法的 Node 版（与 extension/captcha_solver.js 同一套判据）
  png.js         极简 PNG 编解码（只有 zlib，不装任何原生库）
  selftest.js    生成 N 道题 → 求解 → 与真值比对
  package.json
```

## 跑起来

```bash
cd captcha_fixture
node server.js          # http://127.0.0.1:8777
node selftest.js        # 自检：默认 30 道题，容差 ±3px
```

实测自检结果：

```
样例 seed=1000: dx=230.5 真值=230.07 slider_dx=210.9 期望=210.5
通过 30/30（容差 ±3px），最大误差 0.53px
```

## 接口（与线上 `/captcha` 同形）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 复刻题页（canvas + 轨道 + 手柄，可手拖） |
| GET | `/challenge?seed=&dpr=` | 取一道题：`{seed, bmpW, bmpH, dataUrl, sha256, geom}`，**不含真值** |
| POST | `/analyze` | 入参与线上完全一致：`{dataUrl, bmpW, bmpH, cssW, cssH, dpr, inFrame, trackW, handleW, seed}` → `{ok, dx, slider_dx, truth_dx, err}` |
| POST | `/grade` | `{seed, dx}` 或 `{seed, sliderDx, bmpW, trackW, handleW}` → `{pass, truth_dx, diff, tol}` |
| GET | `/specimens` | `runs/captcha_specimens/manifest.json` |
| GET | `/specimen/:id` | 回放历史样本位图；`?meta=1` 取该样本的完整 JSON |

**唯一需要改的接入点**：脚本里把 `/captcha` 的地址从 `http://127.0.0.1:8765` 换成 `http://127.0.0.1:8777`，
其余字段一个都不用动。`seed` 传同一个值即可复现同一道题（回归用）。

## 真值策略（别搞混）

- `/analyze` 在本地会附带 `truth_dx` 与 `err`，只为本机调试算法偏差；
- `/grade` 才是判分口，容差 `TOL` 默认 3 位图像素，可用环境变量覆盖：`TOL=2 node server.js`；
- 线上永远拿不到真值——所以「在复刻题上过」只证明你的**识别 + 拖动换算**是对的，不证明能过 WAF 的访问频率控制。

## 回放历史样本

`runs/captcha_specimens/` 里有 23 个样本（含 1 个实时上报的 live 样本）。回放：

```bash
curl -s http://127.0.0.1:8777/specimens | head -c 400
curl -s -o challenge.png http://127.0.0.1:8777/specimen/live_20261111-154435_w1
curl -s http://127.0.0.1:8777/specimen/live_20261111-154435_w1?meta=1
```

样本 `live_*` 带完整几何（`bmp_w/h`、`css_w/h`、`track_w`、`handle_w`）、环境（UA/locale/timezone/DPR）、
DOM 片段和当时的 `scan` 结果——这类样本最适合当回归基准。

## 为什么复刻服务必须和线上同构

这三点如果对不上，脚本在复刻题上过、真站上照样挂：

1. **位图坐标系**：`dx` 是在 `canvas.width/height` 的位图里量的，不是 CSS 像素；DPR=2 时位图翻倍（实测有 800×660 的样本）。
2. **行程换算**：`slider_dx = dx × (track_w − handle_w) / bmp_w`；实测真实轨道 430、手柄 34、位图 400。
3. **判据**：三档阈值排掉深色网格 → 连通域 → 填充密度分实心/空心 → 空心「填满」后比 8×8 剖面 → 综合打分。
   少任何一步，干扰图形就会把外接框带偏（实测 dpr 样本误差会到 10px 以上）。
