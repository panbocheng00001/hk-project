# 明天（2026-11-12）本地 IP + 单实例 全流程 Runbook

> 与 `明天_runbook.md`（4 窗口 + 代理版）的区别：**这条只用本机家宽、只开一个窗口、不走代理**。
> 原因见 `代理_来源IP限制_结论.md`：IPWO 香港住宅代理在大陆来源 IP 上直接 403
> （`access denied, china IP is not allow`），出口一换就全灭。本机出口是 `14.127.x.x / CN / AS4134`（深圳电信），
> 正好是官方 FAQ Q20 允许的那类「真实家宽」。

---

## 一、今晚已经修好并实测过的三件事（2026-11-11 23:10–23:35）

| # | 问题 | 现在的行为 |
|---|---|---|
| 1 | **country 阶段卡死**（今天 17:20 就死在这） | 不再在 `document_start` 抓空壳快照：先等 React 渲染（body 有孩子 + 可交互元素 > 8），渲染后才存快照；「中国内地」每 1.5s 重试点击（最多 12 次），第 2 次自动补 pointer 序列。实测 23:15 一次点中并点掉「继续」。 |
| 2 | **每一步没有画面** | 扩展 → `tabs.captureVisibleTab` → `POST /shot` → `runs/steps/<日期>/<时分秒>_<窗口>_<步骤>.png`。每个点击/填表/上传/OTP/阶段切换各留一张。实测已落 12 张，`SelectCountry`（中国内地已选中）与 JSP 无名额页都能看清。 |
| 3 | 扩展改了代码却不生效 | 只清 `Default\Extensions` 不够，MV3 的 SW 还有一份 V8 脚本缓存（`Default\Service Worker`）。`launch_ext.ps1` 现在两处一起清，并给 SW 打了版本号（日志出现 `shots=v3` 才算新代码）。 |

另外一个坑：`tabs.captureVisibleTab` 要求 `<all_urls>` 或 `activeTab` 权限，
**只给具体域名不够**（会报 `Either the '<all_urls>' or 'activeTab' permission is required`）。
`manifest.json` 的 `host_permissions` 已加 `<all_urls>`（unpacked 扩展自动授予，无安装提示）。

实测全程（23:15:02 → 23:15:30，28 秒）：

```
unknown → entry(勾同意) → 预约 → auth(不用智方便) → 继续
      → abs4 排队（放行约 30s）→ SelectCountry（点中国内地 → 继续）
      → abs1 JSP：步骤一 认证 ✓，红字「现时没有可供预约的时段」
      → TrackId 20261111-231530-11-cc7c84c8，cookie 10 条已存
```

深夜无号，所以流程终点就是 JSP 页红字。**有号时同一条链路会继续走步骤二→三→四**。

---

## 一补、23:30–23:45 又踩到并修掉的三个坑（都跟「记录」直接相关）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 4 | 步骤越跑越多，但**有些步没有截图** | `tabs.captureVisibleTab` 有「每窗口每秒 2 次」的硬配额，超了直接报 `MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND` | background 里做**串行队列 + 600ms 间隔**，失败 450ms 后重试一次；标签不在前台就先 `tabs.update(active)` 再拍 |
| 5 | 重启浏览器后**扩展静默不加载**（页面一条注入日志都没有） | 反复删 `profile/Default/Service Worker` 把 profile 弄坏 | 不再自动删缓存；`launch_ext.ps1` 加 `-FreshProfile`，`run_local.ps1` **默认重建 profile**（cookie 丢了只是重过 WAF） |
| 6 | 浏览器在跑，但 `ext_status.jsonl` 一条新日志都没有 | 本地服务被连带杀掉（被 job/父进程收走），扩展所有上报静默失败 | 服务改用 **WMI `Win32_Process.Create` 启动**，父进程是 WmiPrvSE，彻底脱离调用方进程树 |

> 判断「服务还活着吗」只需要一条：`curl http://127.0.0.1:8765/ping`。

---

## 二、明天的时间表（HKT）

| 时刻 | 动作 | 命令 |
|---|---|---|
| **16:00** | 站点体检（412/超时正常，只有 502/维护页才停手） | `curl -s -o NUL -w "%{http_code}" https://abs3.td.gov.hk/?lang=sc` |
| **16:05** | 出口自检：必须是 CN 家宽，不能是 VPN/WARP/代理 | `pwsh -File .\run_local.ps1 -DryRun -RestartService` |
| **16:30** | 正式起服务 + 起浏览器（本地 IP、无代理、单实例 w1） | `powershell -NoProfile -ExecutionPolicy Bypass -File .\run_local.ps1 -Instance w1 -RestartService` |
| **16:30–16:57** | 挂着别关。扩展自己走：同意 → 预约 → 不用智方便 → 中国内地 → 排队 | 看 `http://127.0.0.1:8765/shot/latest` |
| **16:57** | 坐到屏幕前，钉钉/声音打开，速填卡、两张图、邮箱页面摆好 | — |
| **17:00** | 洗牌放行。**弹滑块先等 10 秒让扩展自动解**；解不动就去窗口里手动拖 | 见第三节 |
| **17:00–17:20** | 有号：步骤二填资料（身份/驾照/电邮/上传两张图/OTP）→ 步骤三选处/日期 → 步骤四递交 | 扩展自动，人盯异常 |
| **17:20 之后** | 没抢到就挂着等回流；窗口别关 | `python check.py --loop 180 --ring` |

---

## 三、验证码（中途一定会遇到，按这个顺序处置）

| 类型 | 出现位置 | 谁来解决 | 判断依据 |
|---|---|---|---|
| **华为 WAF 滑块**（拖动让实心/空心重合） | 16:30 后进 abs3/abs4 时；也可能在排队前后 | **扩展自动解**；失败才叫人 | 日志 `waf_captcha` → `captcha_solved`（成功）或 `need_human`（失败） |
| **MTCaptcha 勾选框** | 步骤二/四提交前（业务页） | 扩展自动点勾；升级成图片点选题就**必须人工** | 日志 `mtcaptcha` + 快照 `runs/session_*mtcaptcha*.json` |
| **图片点选 / 短信 OTP** | 极少见 / 步骤二 | 人工（OTP 由 `mail_watch.py` 抓邮件填） | 日志 `need_human` |

**人工介入的正确姿势**（不要刷新、不要关窗口、不要重复点提交）：

1. 先看 `http://127.0.0.1:8765/shot/latest` —— 最后一张图就是现场。
2. 到浏览器窗口里把验证码拖完/点完。
3. 扩展下一次 tick（1.2 秒）会发现阶段前进，自动继续；不需要重启任何东西。

> 规矩：滑块 6 秒一次重试就够，**连按 F5 会触发限流**（见 `明天_runbook.md` 红线 3）。

---

## 四、每一步记录了什么（跑完可以逐步分析）

### 4.1 一步一个目录：`runs/runlog/<日期>/<序号>_<时分秒>_<窗口>_<标签>/`

| 文件 | 内容 |
|---|---|
| `meta.json` | **状态**：stage、URL、标题、`document.readyState`、viewport、滚动位置、正文长度、**可见文本前 4000 字**、**全部表单控件**（name/id/type/value/checked/disabled/可见/标签）、**可点元素清单**、iframe 列表、**cookie 全量**（含 HttpOnly）、截图与 DOM 的字节数 |
| `dom.html` | **整页 HTML**（阶段切换 + 关键动作必存；JSP 页 26 KB、带 TrackId 的那种） |
| `shot.png` | 当时的画面（串行截图，每步一张） |

两种触发点：
- **阶段切换**：`entry / auth / country / form / confirm / otp / calendar / mtcaptcha / waf_captcha / queue / unknown` 各记一次（带 DOM）
- **明确动作**：点击/填表/上传/OTP/验证码每次 `step` 都记（关键动作带 DOM）

### 4.2 接口与接口返回

| 数据 | 来源 | 位置 |
|---|---|---|
| 业务请求（表单 POST 的全部字段 / XHR / fetch 的 body） | `netrec.js` 钩页面主世界 | `runs/net/net.jsonl` |
| **接口返回体**（XHR / fetch 的响应文本，截断 12 KB） | 同上（`xhr_resp` / `fetch_resp`） | 同上 |
| 表单 POST 的返回 | 就是**下一页的整页 HTML** → 落在下一条步骤的 `dom.html`（含 TrackId、红字、全部 hidden 字段） | `runs/runlog/<日期>/…/dom.html` |
| 每个请求的状态码 / content-type / length / location | `webRequest`（观察型） | `runs/netlog.jsonl`（`ph=done`/`hdr`） |
| 事后汇总 | — | `python runlog.py --api` |

### 4.3 分析命令（跑完就这几条）

```powershell
python runlog.py --list            # 今天每一步一行：时刻/阶段/动作/控件数/有无DOM/有无图/URL
python runlog.py --step 12         # 第 12 步的全部状态 + 当时的接口 + HTTP 返回头
python runlog.py --step 12 --raw   # 连响应体一起打印
python runlog.py --api             # 今天所有接口：方法/次数/状态码/最大响应/字段样例
python runlog.py --report          # 生成 runs/runlog/report_<日期>.md（带截图引用，可直接贴复盘）
python runlog.py --day 2026-11-12 --list
```

### 4.4 其它痕迹

| 内容 | 位置 / 命令 |
|---|---|
| 事件流水（阶段/点击/网络/验证码/OTP） | `runs/journal/2026-11-12.jsonl` / `.log`；`python journal.py --timeline --min-gap 3` |
| 每步截图（扁平清单） | `runs/steps/2026-11-12/*.png`；`http://127.0.0.1:8765/steps?n=30` |
| 最新一张实时画面 | `http://127.0.0.1:8765/shot/latest` |
| 会话快照（cookie + 字段 + HTML） | `runs/session_<阶段>_w1_<时分秒>.json` |
| 阶段耗时 / 复盘报告 | `python journal.py --stages` / `--report` |
| 步骤清单（HTTP） | `http://127.0.0.1:8765/runlog?n=40` |

### 4.5 今晚实测的规模（参考量级）

81 步里：73 步带截图、42 步带整页 DOM，合计约 4.2 MB；业务接口抓到 8 条表单 POST + 5 条 fetch（含 4 条响应体）。
明天有号时会更长（步骤二填表/上传/OTP、步骤三选日期、步骤四递交都会各留一份）。

---

## 五、出问题怎么判（出口 / 挑战 / 名额）

| 现象 | 含义 | 处置 |
|---|---|---|
| 页面空白、`412`、日志 `waf_challenge` | WAF 挑战，不是封号 | 别刷；等 6 秒重试；一直不过就去窗口手动过 |
| `Restricted access` + `Ip: ...` | 出口被判死（VPN/机房/隐私中继） | 关掉一切代理/WARP，确认 `run_local.ps1` 打印的出口是 CN 家宽 |
| JSP 页红字「现时没有可供预约的时段」 | 当前无名额（17:00 前正常） | 挂着等 17:00；17:20 后改 `check.py --loop` 等回流 |
| 弹「你的排队号已失效」 | 20 分钟窗口过了 | 扩展会自动点「GET A NEW PLACE」，别手动刷新 |
| 走到步骤三没有可选日期 | 名额被抢完 | 换办事处（香港牌照事务处名额池更大） |

---

## 六、红线（照旧，别试）

1. **不要 kill `ext_server.py`**：扩展立刻拿不到配置/图片/OTP。
2. **不要连按 F5**，挑战页 6 秒重试一次就够。
3. **17:00–17:20 不要跑 `token_pool --warm` 的 headful 版本**（会弹窗抢焦点）。
4. **一个身份只能有一个有效预约**：单实例只完成一条，别开第二个窗口用同一份证件。
5. **别清 cookie**：WAF token 删了就要重新过挑战。

---

## 七、一句话开跑

```powershell
# 16:30 就这一条：起服务（WMI 独立进程）+ 打印出口 IP + 重建 profile + 起本地 IP 浏览器
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_local.ps1 -Instance w1 -RestartService

# 想保留上次的 cookie/WAF token（不重建 profile）就加 -KeepProfile
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_local.ps1 -Instance w1 -RestartService -KeepProfile
```

盯进度 / 跑完复盘：

```powershell
python runlog.py --list                 # 现在走到第几步
python runlog.py --step 12              # 某一步：状态 + 控件 + 接口 + 返回
python runlog.py --api                  # 接口总表
python journal.py --timeline --min-gap 3
python runlog.py --report               # 逐步 Markdown 复盘
```

服务是不是还活着：`curl http://127.0.0.1:8765/ping`（返回 `{"ok":true}` 就行）。
