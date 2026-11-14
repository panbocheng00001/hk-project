# hk-project-clean

本机预约流程自动化（`booking-lab/`）+ 滑块验证码识别内核（`captcha-lab/`），两个子项目各自独立、可单独测试。

> 仓库内所有域名都是 `example.test` 占位，端口是回环地址。真实地址与个人资料只在本机 `config.json`，不入库。
> 开工前先读 [`AGENTS.md`](AGENTS.md)，改动规范见 [`docs/AI_CODING_SPEC.md`](docs/AI_CODING_SPEC.md)。

---

## 1. 两个子项目

| 目录 | 是什么 | 入口 | 测试 |
|---|---|---|---|
| [`booking-lab/`](booking-lab/README.md) | 表单填写、会话票据池、412 挑战处理、验证码交接与可选加速、浏览器扩展 | `fill_form.py` / `http_flow.py` / `ext_server.py` | 58 passed |
| [`captcha-lab/`](captcha-lab/README.md) | 图片 → 位移 → 轨迹 → 载荷；HTTP 服务与 CLI；出题与鲁棒性评测 | `python -m solver` | 166 passed |

依赖方向只有一个：`booking-lab` 通过 [`captcha_lab_bridge.py`](booking-lab/captcha_lab_bridge.py) 调用 `captcha-lab`。
内核不可用时抛 `BridgeUnavailable`，由调用方回落人工交接。

---

## 2. 30 秒验证

```bash
# 文案与注释检查（必须 0 命中）
python tools/wording_lint.py --root .

# 宿主侧：30 项决策契约 + 25 项交接计时 + 3 项端到端
cd booking-lab && python -m pytest captcha_flow/tests tests -q

# 识别内核
cd captcha-lab && python -m pytest -q

# 识别口径回归
cd captcha-lab && python -m solver waf --image tests/data/waf_canvas.png --track-w 400 --handle-w 34
#   -> 实心中心 104.5px / 空心中心 335.0px / dx=230.50 / slider_dx=210.91

# 上面四条一次跑完
pwsh -File tools/check.ps1
```

实测基线（2026-11-15，本机）：`58 passed`（12s）、`166 passed`（13s）、文案 lint `0 block`。

---

## 3. 本地跑起来

```bash
cd booking-lab

# 1) 配置：模板复制成 config.json 后填自己的资料（该文件被 .gitignore 忽略）
cp config.example.json config.json

# 2) 只读查名额，不提交、不占会话
python check.py --loop 180

# 3) 起本地数据服务（扩展用，仅监听 127.0.0.1:8765）
python ext_server.py

# 4) 起带扩展的浏览器
pwsh -File run_local.ps1 -Instance w1

# 5) 不用浏览器，纯 HTTP 走排队与查名额
python http_flow.py --probe
```

复刻题服务（离线联调用，零依赖 Node）：

```bash
cd booking-lab/captcha_mock && node server.js      # 127.0.0.1:8777
node selftest.js                                   # 30/30，最大误差 0.53px
```

依赖：`pip install curl_cffi playwright opencv-python`；首次跑浏览器流程需要 Chrome for Testing（`pwsh -File get_cft.ps1`）。
`vendor/` 目录不入库。

---

## 4. 目录

```
hk-project-clean/
├─ AGENTS.md                  接手契约：不变量、往哪放、完成定义
├─ HANDOFF.md                 工程交接：现状、改动清单、下一步
├─ docs/
│  ├─ README.md               文档索引（先读哪份）
│  ├─ ARCHITECTURE.md         两层职责、六组模块、单向依赖
│  ├─ AI_CODING_SPEC.md       注释/文档/命名规范 + 词表 + 自检
│  ├─ PROMPT_PACK.md          交给模型改这个仓库时粘哪一段
│  └─ WORDING_LEXICON.md      词表（工具生成）
├─ tools/
│  ├─ wording_lint.py         文案与注释检查
│  ├─ wording_lexicon.json    词表数据源
│  └─ check.ps1               一条命令跑完全部检查
├─ booking-lab/               宿主：表单、会话、验证码、扩展、复刻题
└─ captcha-lab/               内核：识别、轨迹、站点适配、服务、评测
```

---

## 5. 边界是怎么写的

这套代码的行为边界不写在说明段落里，写在三个地方，都可以机械核对：

| 位置 | 形式 | 例子 |
|---|---|---|
| `captcha_flow/policy.py` | 纯函数 + 四条原则 | 滑块/图形码/点选一律 `HAND_TO_HUMAN` |
| `captcha_flow/tests/` | 断言 | `test_slider_never_triggers_automatic_action` |
| `docs/*` | 行为清单表 | [`SECURITY_MODEL.md`](booking-lab/docs/SECURITY_MODEL.md) 第 2–3 节 |

可选加速器 `captcha_auto.py` 单独一层，由 `CAPTCHA_AUTO` 开关控制（缺省启用，设 `0/false/off` 关闭），
关闭后决策层的行为 100% 不变；细节见 [`CAPTCHA_AUTO.md`](booking-lab/docs/CAPTCHA_AUTO.md)。

个人资料、证件影像、token、目标地址的处理约定见 [`SECURITY_MODEL.md`](booking-lab/docs/SECURITY_MODEL.md) 第 5 节：
它们在 `config.json` / 环境变量 / `_private/`（本副本之外），代码树里只有 `config.example.json` 模板。

---

## 6. 与上游副本的关系

本目录是脱敏后的工作副本：清洗脚本、原始物料与归档清单位于本副本之外的 `_private/`；
`booking-lab/ARCHIVE_MANIFEST.md` 记录了被移出的物料与理由。
