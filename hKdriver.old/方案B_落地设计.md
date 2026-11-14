# 方案 B 落地设计：浏览器铸票 → 票池 → curl_cffi 用票

> 2026-11-10 22:06 起实测，代码已进仓库：`token_pool.py`（新）、`replay.py --pool`（新分支）。
> 结论先给：**方案 B 成立，但「每 IP 每 20 分钟开一次浏览器」这个前提要拆掉两层**——
> 一层是「冷启动」，一层是「三层票据混成一个 20 分钟」。拆完以后 100 个 IP 的铸票成本是 **2.9 分钟**，不是 100 次。

---

## 一、把「不现实」的账重算一遍（全部是今天实测数字）

| 环节 | 实测 | 说明 |
|---|---|---|
| Chrome 冷启动（CfT 156，`cft/chrome-win64/chrome.exe`） | 3–6 s | 进程 + profile 加载 |
| 热进程 `browser.new_context()` | **0.01 s** | 隔离上下文，独立 cookie/代理/locale |
| 上下文 `close()` | 0.02 s | 关掉即释放，内存不累积 |
| `page.goto(ENTRY, wait_until="commit")` → 拿到 `HWWAFSESID` | **1.2–3.8 s** | 这是真实单价 |
| `page.goto(..., wait_until="domcontentloaded")` | **24.91 s** | ← 假瓶颈，见下 |
| 单张票端到端（n=4） | **3.45 s**，单 worker 17.4 张/分钟 | 100 张 @2 worker ≈ 2.9 min，@4 worker ≈ 1.5 min |
| 无浏览器保活探测（curl_cffi + 票，GET abs3） | **0.9 s/张** | 100 张 ≈ 90 s，且不开浏览器 |

**踩坑点，值得单独记住**：一开始用 `domcontentloaded` 等页面，单张票 25–32 秒，看着就像「100 张要跑一小时，不现实」。
换了 `wait_until="commit"` 之后是 3.4 秒。原因：abs3 是 React SPA 壳，`domcontentloaded` 要等全部 defer/module 脚本执行完，
而 WAF 会话 cookie 是**响应头**写的（挑战跑过时才是 JS 写），跟 load 事件完全无关。

所以方案 B 的真实成本模型是：

```
100 IP × 3.45 s ÷ 2 worker  =  2.9 分钟        ← 铸票
100 IP × 0.90 s             =  90 秒           ← 保活一轮（不开浏览器）
100 IP ÷ 20 min             =  5 张/分钟 需求  vs  34 张/分钟 产能（2 worker）
```

**产能有 7 倍余量。方案 B 不是「不现实」，是「原来测的是错的路」。**

---

## 二、三层票据，别混成一个「20 分钟 token」

| 层 | 组成 | 绑定 | 寿命 | 能否预铸 |
|---|---|---|---|---|
| **WAF 层** | `HWWAFSESID` / `HWWAFSESTIME` / `HIK0kat3rt9xO` / `__cf_bm` | 出口 IP | 固定戳，非滑动（实测 3 次响应后 `HWWAFSESTIME` 不变） | ✅ 可提前批量铸 |
| **排队层** | `urltoken`（Queue-it ticket）+ 服务端会话里的「已放行」位 | 会话 + IP | 17:00 洗牌后才产生 | ❌ 不可预铸，只能按时冲 |
| **表单层** | `JSESSIONID` + `maxAgeMillis=1200000` | 会话 + IP | **放行后 20 分钟硬时限** | ❌ 不可预铸 |

**这直接决定了池子该怎么用**：
- 16:40 起把 WAF 层预热满（便宜，3.4 s/张，与抢号无关，失败了也不心疼）；
- 17:00 洗牌后，**排队层只能靠真浏览器**（扩展那条线），放行后立刻 `--ingest` 入库；
- 表单层的 20 分钟窗口内，用 `curl_cffi` 把「选办事处 × 选时段」循环试满——这才是接口层该干的活。

---

## 三、架构

```
                ┌──────────────── 铸票（贵，3.4s）────────────────┐
proxies.txt ──▶ │ MintWorker: 一个常驻 Chrome + 每次一张短命上下文 │
                 │   new_context(proxy=这张票的出口, locale=zh-CN) │
                 │   goto(abs3, commit) → 等 HWWAFSESID           │
                 └───────────────┬───────────────────────────────┘
                                 ▼
                        SQLite 票池  runs/pool.sqlite3
        slots(ip_id, proxy, egress_ip, ua, cookies, waf_at, waf_ttl, form_at, form_ttl, state, fails)
                                 ▲                    │
   extension/ext_server 放行后 ──┘                    ▼
   runs/cookies.json --ingest              ┌──────────────────────┐
   （带 20 分钟表单时钟）                    │ 用票（便宜，0.9s）    │
                                           │ SlotClient: curl_cffi │
                                           │  同代理 + 同 UA + 同票 │
                                           │  412/302 → 标记作废    │
                                           └──────────────────────┘
```

要点只有三条：
1. **浏览器只负责「铸」，不负责「用」**。铸完关上下文，票留在库里。
2. **票和出口绑死**：`slots.proxy` 就是这张票的唯一合法出口；`curl_cffi` 必须走同一个代理，否则 412。
3. **失效就地判死**：`412/403` 或 `302 → abs3/?lang=en` 都算票废，立刻 `state=dead` 并排队重铸，不做无效重试（`waf.py` 的结论：越试越进不去）。

---

## 四、怎么用（已经能跑）

```powershell
# 0) 零网络自检：Chrome 起得来、上下文隔离、locale 生效、webdriver=false
python token_pool.py --selftest

# 1) 先量一次你自己的真实单价（会顺带测出口 IP）
python token_pool.py --bench 4 --workers 2 --probe-egress

# 2) 代理清单一行一个，批量预热 WAF 票池
python token_pool.py --warm --proxies proxies.txt --workers 2
#    或者用粘性会话模板生成 100 个逻辑出口
python token_pool.py --warm --proxy-template "http://u:p@gate.example:8000?session={n}" --count 100 --workers 4

# 3) 无浏览器保活：412 判死并待重铸（100 张约 90 秒）
python token_pool.py --health

# 4) 常驻：每 5 秒扫一遍，剩余寿命 < 60% 就续铸（提前量 12 分钟）
python token_pool.py --loop --proxies proxies.txt --workers 2 --interval 5

# 5) 扩展放行后把门票收进池（20 分钟时钟从现在开始算）
python token_pool.py --ingest runs/cookies.json --ip-id w1

# 6) 查池子
python token_pool.py --stats
```

业务侧 15 行接进去：

```python
from token_pool import SlotClient, TokenDead

with SlotClient() as c:              # kind="form"：只挑扩展放行过的票
    r = c.post("https://abs1.td.gov.hk/tdab2/tdabs_di_external/AdminServlet_schinese?cmd=cmdGetOfficeQuotaDIAction",
               data=fields)
    if r.status_code == 200 and "continueBtn1" in r.text:
        ...
```

`SlotClient` 干的事：挑剩余寿命最长的票 → 用**这张票的代理**建 `curl_cffi.Session`（`impersonate` 自动取 `DEFAULT_CHROME`）→ 灌 cookie → 发请求 → 命中 412 / `302 ?lang=en` 就把票判死、换下一张重试一次。

`replay.py` 也加了口子，排查时不用写代码：

```powershell
python replay.py --pool                       # 自动挑最热的票，跑一次只读校验
python replay.py --pool --ip-id w1            # 指定出口
python replay.py --pool --db runs/pool_test.sqlite3
```

---

## 五、100 个 IP 到底要不要（这笔钱值不值）

先看**你实际需要几条会话**。抢号的决定因素是 17:00 那次随机洗牌，不是「同时有 100 条队列」。

| 池规模 | 拿到「洗牌靠前」的概率 | 代价 | 风险 |
|---|---|---|---|
| 4 条（现在） | 低，且 412 一来全军覆没 | 最低 | 单点 |
| **8–12 条（推荐）** | 够用，取消回流也能接 | 中 | 可控 |
| 20 条 | 边际收益已经很小 | 高 | 同一代理商的 ASN 段被整体判异常 |
| 100 条 | 收益 ≈ 20 条，**额外 80 条几乎不产生中签概率** | 很高 | WAF 侧异常特征明显，容易一起 412 |

所以工程上把 100 跑通没问题（上面 2.9 分钟），但**这 100 个 IP 里大部分时间是在空转**。
文档里那句「多开 4 个窗口就 412、资料还串号」说明真正的瓶颈在**同机多上下文一致性**，不在 IP 数量。

带宽成本可以自己套（单张票 ≈ abs3 壳 36 KB + JS/CSS 资源 [ASSET_KB] + 若干 `__cf_bm` 挑战请求）：

```
每轮成本 ≈ N_ip × 单票流量 × 代理单价[¥/GB]
示例：20 IP × 1.5 MB × 3 轮/天 ≈ 90 MB/天 —— 量级很小，钱主要花在「住宅 IP 的月租」而不是流量
```

---

## 六、17:00 当天的时间表（接现有链路）

| 时间 | 动作 | 命令 |
|---|---|---|
| 15:30 | 站点体检 + 代理体检 | `python check.py` / `python probe_proxy.py --file proxies.txt` |
| **16:40** | WAF 池预热满（失败的重铸两轮） | `python token_pool.py --warm --proxies proxies.txt --workers 4` |
| 16:45 | 起保活循环（不开浏览器，随时发现票死） | `python token_pool.py --loop --proxies proxies.txt --workers 2` |
| 16:27–16:29 | 真浏览器进队（扩展，每条间隔 30–40 秒） | `launch_ext.ps1 -Instance w1..w4` |
| 17:00 | 放行 → 扩展一落到 JSP 页就推 cookie | `python token_pool.py --ingest runs/cookies.json --ip-id w1` |
| 17:00+ | 20 分钟窗口内循环试选（接口层） | `python replay.py --pool --ip-id w1` 先验票，再跑业务循环 |

排队层和中签判定**仍然必须走浏览器**，接口层只负责把 20 分钟用满——这和《接口直约_可行性.md》第六节的结论一致，今天又复现了一次：

```
带新铸的 WAF 票 GET abs3          → 200            （WAF 层通过）
带同一张票 GET AdminServlet quota → 302 → abs3/?lang=en  （排队层没有，正常）
```

---

## 七、失败矩阵

| 现象 | 判定 | 处理 |
|---|---|---|
| `HWWAFSESID` 有、`HIK0kat3rt9xO` 无 | WAF 未挑战（正常营业时段常见） | 记 `[仅HWWAFSESID]`，仍可用；业务侧一旦 412 就升级为「必须 HIK0」 |
| goto 返回 412 / 403 | 挑战没跑过或环境判异常 | `state=dead`，`fails+1`；**不重试同一张**，换代理出口再铸 |
| 单张票连续 3 次失败 | 该出口被判死 | `state=banned`，需要换 IP（不是换票） |
| 业务请求 `302 → abs3/?lang=en` | 排队放行状态不在这张票上 | 票本身可能没坏：若是 WAF 票，属正常；若是表单票，判废 |
| 出口 IP 与 `egress_ip` 不一致 | 代理换了出口（轮换型代理） | 票直接作废——**轮换代理不能用来绑票**（`probe_proxy.py` 的一致性检查就是为这个） |
| `fails>=3` 还继续铸 | 会踩 WAF 的「越试越进不去」 | `_store.mark()` 自动进 `banned`，不再入队 |

---

## 八、下一层：把 3.4 s/张 打到 ~0.1 s/张（真·永久 token 池）

浏览器之所以还要开，是因为华为 WAF 的挑战 JS（`$_ts` / `nsd` / `cd`）要执行。两条降本路线：

1. **把挑战 JS 搬到 Node 里跑（推荐先试）**
   - 用 `curl_cffi` 拿 412 响应体 → 抠出内联脚本 → `node` + `vm` 模块跑，喂一套假但自洽的 `navigator/screen/WebGL/canvas` 指纹；
   - 抓 `document.cookie` 的写入点，把 `HIK0kat3rt9xO` 直接算出来；
   - 成功的话单价 ≈ 50–150 ms，100 张票 10 秒内解决，且**不需要浏览器的内存**；
   - 验证方法：Node 出的票 → `curl_cffi` GET abs3 得 200（就是现在 `--health` 的判据）。
   - 风险：混淆脚本里有环境一致性校验（canvas/WebGL/字体），需要把「一份指纹」做扎实，而不是每次随机。

2. **付费 token 服务**：`curl_cffi` 生态里已经有卖 `cf_clearance` 这类 anti-bot token 的（hypersolutions / yescaptcha 一类）。
   华为 WAF 的现成服务少，先别指望；但值得花 10 分钟问一句有没有 `HWWAFSESID` 类产品。买得到就等于把 3.4 s 换成 HTTP 一次调用。

在这两条落地之前，`token_pool.py` 的形态已经是最优折中：**铸票贵但量少（16:40 一次性预热），用票便宜（0.9 s 保活）**。

---

## 九、两个必须写进操作规范的红线

1. **票 = 出口 IP 的租赁物**。代理换了出口，票立刻作废。所以代理清单必须是**粘性会话**（`?session={n}` 那种），不是轮换池。
2. **失败不狂重试**。`state=banned` 之后要先换出口，再谈重铸；同一出口连续 3 次挑战失败就是被判了，继续撞只会把整个段拉黑。

---

## 附：本次改动

| 文件 | 改动 |
|---|---|
| `token_pool.py` | 新增。`Store` 票池 / `MintWorker` 铸票 / `Keeper` 调度 / `SlotClient` 用票 / CLI 七个子命令 |
| `replay.py` | 新增 `--pool [--ip-id] [--db]` 分支：从票池取票跑只读校验，判活判废 |
| `runs/pool.sqlite3` | 正式票池（首次运行自动建表） |
| `runs/pool_inspect/` | 被拦样本 dump（html + status），排查用 |
