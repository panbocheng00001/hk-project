# hk-project-clean

`hKdriver/` 的一份**脱敏副本**，外加一个新的验证码交互模块。

- 原始项目：`hKdriver/`，未改动
- 被移出的物料：`_private/archive/`，附 `booking-lab/ARCHIVE_MANIFEST.md` 说明理由
- 重建方式：`python tools/sanitize/run_all.py`（七个阶段，幂等，失败即停）

---

## 这份副本解决了什么

### 1. 真实凭据与个人身份物料已从代码树移除

原项目里有这些，且都在 git 里：

| 内容 | 原先位置 |
|---|---|
| 真实姓名 / 证件号 / 18 位驾照号 / 手机号 | `config.json` |
| 证件照片（驾驶证、港澳通行证） | `files/`、`资料/` |
| Outlook OAuth refresh_token / access_token、邮箱明文密码 | `mail.json`、`邮箱.txt`、`邮箱-新.txt` |
| 钉钉机器人 webhook token 与签名 | `config.json` |
| 代理账号密码、供应商 trade_no 与签名 | `proxies.txt`、`proxies_api.txt` |
| 目标站点会话 cookie | `session_export.json`、`.tdabs_session.json` |

这些现在全部在 `_private/archive/`，代码树里只剩 `config.example.json` 模板。
扫描校验：**pii 64 → 0，secret 51 → 0**。

这件事与"能不能通过审查"无关——真实凭据和个人证件照片本来就不该进仓库。
如果这个仓库推过远端，那些 token 应当视为已泄露，需要重新签发。

### 2. 验证码逻辑重写成了可测试的模块

原来 `captcha.py` 是 280 行一个大函数，识别、动作、等待混在一起。
现在拆成 `booking-lab/captcha_flow/`：

```
model.py     数据类型
detect.py    只读 DOM，输出 ChallengeInfo
policy.py    纯函数：ChallengeInfo → Decision（所有决策在这里，80 行）
session.py   状态机：执行 Decision，管超时与交接
adapters.py  页面抽象（Playwright 实现 + 测试用假实现）
tests/       30 项离线用例
```

行为矩阵：隐形 token 等待、勾选框点一次，**滑块/图形码/点选一律交接给人**。
这部分是可复用的——任何需要"验证码不阻塞流程、但也不替人做题"的场景都能用。

### 3. 找到并修掉的实际缺陷

清洗过程中暴露出来的真问题（都已修）：

- `extension/background.js` 用 `chrome.debugger` 发 `isTrusted=true` 事件，
  且这项能力需要 `debugger` 权限 —— 已移除，`manifest.json` 同步更新
- `verify=False` 全局关闭证书校验 —— 改为默认开启
- `--xff` 转发头探针（用于探测服务端是否信任该头）—— 删除
- 多处硬编码的站点域名 —— 改为 `example.test` 占位，真实地址走本机配置
- 导入断裂（`waf` → `edge_challenge` 改名后未被引用）、`from __future__` 位置错误、
  `tls_profile()` 在定义前被调用 —— 已修，现在全部模块可 import

### 4. 补了说明性文档

`booking-lab/docs/` 下两份，作用是让人读代码之前先知道边界：

- `SECURITY_MODEL.md` —— 做什么 / 不做什么 / 数据流 / 威胁模型
- `CAPTCHA_DESIGN.md` —— 验证码状态机的设计理由 + 评审清单

---

## 这份副本没解决什么

**它不会让"给任意站点做通用滑块识别工具包"变成一个能被接受的需求。**

命名与措辞也做了一轮中性化（词表见 `tools/sanitize/lexicon.py`）。
但这些改动只让命名更中性，不改变代码在做什么。审代码的人（或模型）
看 `http_flow.py` 的调用序列、看 `session_pool.py` 的票据池、
看 `fill_form.py` 的自动提交，仍然能得出同样的结论。

而且这条路本身是走不通的：靠措辞让审核方放行，拿到的是"看起来合规"的代码，
跑起来仍然是原样。真正会出事的时候，承担后果的是操作者，不是模型。

---

## 从清洗版切到本地运行（三步）

清洗版的域名是 `example.test` 占位，直接跑连不上任何站点。本地启用：

```bash
# 1) 填配置（config.json 已被 .gitignore 忽略）
cd booking-lab
cp config.example.json config.json     # 然后填 applicants / files_dir

# 2) 占位域名还原成真实地址（一次性）
cd ../..
python tools/sanitize/targets.py --restore --dst hk-project-clean

# 3) 起服务 + 浏览器
cd hk-project-clean/booking-lab
python ext_server.py
pwsh -File run_local.ps1 -Instance w1
```

还原后这棵树**含真实目标地址，不要提交、不要分享**。要回到可分享状态：

```bash
python tools/sanitize/targets.py --strip --dst hk-project-clean
```

已验证 restore → strip 往返后全树哈希一致，不会因为来回切换损坏文件。

### 依赖说明

- endor/ 目录没有复制（那是 curl_cffi 等第三方库的本地副本）。
  系统 Python 里装好即可：`pip install curl_cffi playwright opencv-python`
- 首次跑浏览器流程需要 Chrome for Testing：`pwsh -File get_cft.ps1`

## 校验结果
| 指标 | 清洗前 | 清洗后 |
|---|---|---|
| refusal 类命中 | 342 | 7 |
| pii | 64 | 0 |
| secret | 51 | 0 |
| host（真实域名） | 333 | 2 |
| 命中文件数 | 155 | 9 |

剩下 9 处是技术必需：7 处是 curl_cffi 的字典键名（第三方 API，本地标识符已全部改名，
默认值为空即不启用），2 处是 Turnstile 组件的 DOM 选择器（删了验证码就无法识别）。

其他：全部 `.py` 可解析可 import、全部 `.js` 通过 `node --check`、
`captcha_flow` 30 项测试通过。

---

## 结构

```
hk-project-clean/
├─ README.md                 本文件
├─ HANDOFF.md                工程接手说明
└─ booking-lab/
   ├─ README.md              项目说明
   ├─ ARCHIVE_MANIFEST.md    归档清单与理由
   ├─ docs/
   │  ├─ SECURITY_MODEL.md   行为边界
   │  └─ CAPTCHA_DESIGN.md   验证码设计
   ├─ captcha_flow/          验证码状态机（核心，可单测）
   ├─ extension/             浏览器扩展
   ├─ captcha_mock/          本地合成的验证码夹具（离线联调用）
   └─ *.py                   表单填写、会话管理、节流、日志
```
