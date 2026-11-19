# 纯 HTTP 链路的身份一致性（waf_profile）

> 这份文档回答一个问题：**UA、Sec-CH-UA、TLS 画像、Accept-Language、请求头顺序由哪个文件决定，改一处会影响哪些请求，怎么核对没改坏。**

适用范围：`booking-lab/http_flow.py`、`booking-lab/session_pool.py`。
浏览器侧（Playwright / 扩展）的身份由各自 `new_context(...)` 的 `user_agent / locale / timezone_id` 决定，不经过本文件。

---

## 1. 为什么收在一处

同一套身份会被四个地方读到：

| 读它的地方 | 读什么 | 不一致时的表现 |
|---|---|---|
| curl_cffi `Session(impersonate=...)` | TLS/HTTP2 画像名 | 与 UA 版本打架：JA3/JA4 与 `Sec-CH-UA` 指向两个浏览器 |
| 请求头 `User-Agent` | 主版本号 | 与画像、`Sec-CH-UA` 打架 |
| 请求头 `Sec-CH-UA*` | 主版本号 | 服务端按 UA 校验客户端提示，版本对不上直接判异常 |
| `Accept-Language` | locale | 与出口 IP 归属地、时区打架 |

`booking-lab/waf_profile.py` 是唯一出口：`profile(主版本)` 一次生成 UA、`Sec-CH-UA`、画像名 `chrome<主版本>`；三者主版本号必然相同（`tests/test_waf_profile.py` 守着）。

---

## 2. 主版本号从哪来

| 来源 | 优先级 | 说明 |
|---|---|---|
| `BOOKING_CHROME_MAJOR` | 1 | 显式指定，例如 `131` |
| `waf_profile.DEFAULT_MAJOR` | 2 | 默认 136，与 curl_cffi 0.13 内置的最高 Chrome 画像同主版本 |

约束：主版本号必须能在 `curl_cffi` 的画像列表里找到同名画像。找不到时 `session_kwargs()` 返回 `{}`（用库默认栈），`audit()` 报「画像不在支持列表里」，调用方打日志后继续，不抛异常。

---

## 3. 请求类型与头部顺序

`header_plan(kind, profile)` 返回 `[(name, value), ...]`，顺序固定，不含随机项：

| kind | 用于 | `Sec-Fetch-Mode` | `Sec-Fetch-Dest` | `Origin` |
|---|---|---|---|---|
| `document` | 入口页、业务页、名额概览 GET | `navigate` | `document` | 不带 |
| `form` | 判号 / 名额表单 POST | `navigate` | `document` | 仅跨域（`cross_site=True`） |
| `json` | enqueue / status 的 fetch | `cors` | `empty` | 仅跨域 |
| `script` / `image` | 配套静态资源 | `no-cors` | `script` / `image` | 不带 |

前三条固定为 `sec-ch-ua` → `sec-ch-ua-mobile` → `sec-ch-ua-platform`；`Accept-Language` 固定在最后。
`Origin` 只在跨域时为真：`abs3` 页面提交到 `abs1` 的 servlet 是跨域，同域表单不带。

写入方式：`apply_headers(session.headers, plan)` 先清空再逐条写。用 `update` 会保留旧键位置，顺序特征就丢了。
新增头一律加进 `header_plan()`；每类请求要传的额外头由 `request_headers()` 生成，不要手写第二份字典。

---

## 4. 开关

| 变量 / 参数 | 作用 | 关闭后的行为 |
|---|---|---|
| `BOOKING_CHROME_MAJOR` | 选主版本 | 缺省 136 |
| `BOOKING_TLS_PROFILE` | 覆盖画像名 | `none/off/0/default` = 不指定画像 |
| `--tls-profile none --http1` | 走普通 TLS/1.1 | 认证型转发代理不支持 h2/ALPS 时用；此时自检会报「未指定 TLS 画像」 |
| `BOOKING_INSECURE_TLS=1` | 关证书校验 | 仅内网自签证书环境 |

换出口优先于换画像：同一出口换画像本身就是一次特征跳变。

复用浏览器票（`http_flow.py --cookies`）时，把 `BOOKING_CHROME_MAJOR` 设成铸票浏览器的主版本：
票里记着当时的 UA，UA 与 curl 发出去的不一致，票可能当场作废。

---

## 5. 票据与身份的关系（session_pool）

票据（`HWWAFSESID` + `HIK0kat3rt9x*`）由本机浏览器铸出，票里存了当时的 `ua`。
`SlotClient._build_session()` 按票复现 UA 与头部顺序，再把 cookie 灌进同一个 `Session`：

- UA 用票里的 `ua`，不改成别的版本；
- `BOOKING_TLS_PROFILE` 未设置时不套画像——`curl_cffi` 不一定内置与浏览器同主版本的画像，硬套一个更低的反而让「UA ↔ TLS」更矛盾；
- 这种情况下 `audit(..., require_tls=False)` 不把「未指定画像」当问题；显式设了画像且主版本对不上时，日志会逐条列出。

---

## 6. 核对

```bash
cd booking-lab
python -m pytest tests/test_waf_profile.py tests/test_throttle.py -q     # 19 passed
```

手工核对一条会话：

```python
import waf_profile as wp
p = wp.profile()
print(p.describe())                      # major / ua / tls / locale
print(wp.audit(p, tls_name=p.tls_name, ua=p.ua))
```

---

## 7. 与退出码的关系

`http_flow.py` 的 `classify()` 把 403/405/429 归为「出口被限流或命中静态规则」（退出码 40），412 仍是挑战（41）：

| 状态码 | 判定 | 处置 |
|---|---|---|
| 403 / 405 | 静态规则挡回 | 换出口，停手冷却 |
| 429 | 频率限制 | 降频，等窗口 |
| 412 | JS 挑战 | 等挑战窗口或浏览器接力，不重试 |
| 502 / 503 / 504 | 源站故障 | 退出码 30，按站点故障处理 |
