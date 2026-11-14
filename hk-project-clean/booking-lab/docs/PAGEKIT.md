# PAGEKIT —— 把 SPA 页面渲染出来、压成摘要、脱敏后再外发

> 这份文档回答什么问题：**怎么把一个 JS 渲染的预约站点跑起来、按页抓内容、把抓到的内容变成一份可以贴给别的模型讨论的摘要，而不把个人资料与站点身份一起带出去。**
>
> 所有示例地址用 `example.test` 占位。真实地址、主机名、口径词只在本机文件里。

---

## 1. 三条命令

```bash
cd booking-lab

# 1) 只抓一页，抓完关浏览器
python -m pagekit open --url "https://abs3.example.test/?lang=sc" --name home

# 2) 把站点留在窗口里（操作者手动继续，进程保活；Ctrl+C 收工）
python -m pagekit open --url "https://abs3.example.test/?lang=sc" --name live-home \
    --headed --hold 0 --profile runs/pagekit/profile

# 3) 按步骤走流程，每个页面各出一份摘要
python -m pagekit walk --url "https://abs3.example.test/?lang=sc" \
    --steps pagekit/steps.example.json --name home
```

每次运行落在 `runs/pagekit/<时间戳>/`（`runs/` 已在 `.gitignore` 与 lint 的跳过名单里）。

---

## 2. 一次运行产出什么，哪一份能外发

| 文件 | 内容 | 能不能贴给别的模型 |
|---|---|---|
| `<名字>.html` | 渲染后的完整 DOM | 不能，含全量页面文本 |
| `<名字>.png` | 整页截图 | 不能，可能含已填资料 |
| `<名字>.snapshot.json` | 结构事实（字段名、按钮、链接、请求、正文） | 不能，正文未脱敏 |
| `<名字>.digest.md` | **摘要**：大纲 / 字段 / 按钮 / 链接 / 请求 / 正文，全部已脱敏 | 能，这是唯一的外发物 |
| `<名字>.digest.json` | 同一份摘要的机器可读版 | 能 |
| `index.md` | 本次抓了哪几页、各自摘要文件 | 能 |

判断标准只有一条：**过了 `python -m pagekit verify` 退出码为 0 的，才可以外发。**

```bash
python -m pagekit verify --in runs/pagekit/<时间戳>/home.digest.md
# 干净：… 没有可命中的残留（内置规则 + 词表）    → 退出码 0
# 仍有残留：N 处（只报规则与位置，不打印原文）    → 退出码 3
```

---

## 3. 词表：脱敏的开关都在本机

查找顺序：`--terms` 参数 → 环境变量 `PAGEKIT_TERMS` → `~/.hk-project/pagekit_terms.json`。
仓内只有 `pagekit/terms.example.json`，结构一样，值是占位。

```json
{
  "hosts":    [{ "host": "abs3.example.test", "placeholder": "<SITE_HOST>" }],
  "terms":    [{ "term": "示例发证机关", "placeholder": "<ISSUER>" }],
  "patterns": [{ "name": "ref", "pattern": "\\bREF-\\d{4}\\b", "placeholder": "<BOOKING_REF>" }]
}
```

| 段 | 作用 | 写错会怎样 |
|---|---|---|
| `hosts` | 命中主机换成占位；未登记的主机原样保留（第三方文档站） | 写漏了，站点主机名会出现在摘要里 |
| `terms` | 字面词替换，长词优先；同一词永远同一占位 | 写漏了，该词以原文外发 |
| `patterns` | 自定义正则，用于本地编号格式 | 正则写坏 → 该条跳过，`notes` 里记一笔 |

改完词表不用改代码；重跑 `digest` 即可把已有快照重新压一遍（不启动浏览器）：

```bash
python -m pagekit digest --snapshot runs/pagekit/<时间戳>/home.snapshot.json
```

---

## 4. 内置规则（不依赖词表）

| 规则 | 遮什么 | 占位 |
|---|---|---|
| `hkid` | 香港身分证号 `A123456(3)` 形态 | `<HKID>` |
| `cn_id` | 18 位证件号 | `<ID_NO>` |
| `email` | 邮箱（`example.test` / `localhost` 除外） | `<EMAIL>` |
| `intl_phone` / `hk_phone` | 11 位内地手机 / 8 位本地号码（含 `+852`、空格、横线写法） | `<PHONE>` |
| `jwt` / `bearer` | 令牌本体 | `<TOKEN>` |
| `cookie_pair` | `HWWAFSESID=…`、`Queue-it=…` 一类会话键值 | `<COOKIE>` |
| `url_param` | `?token=`、`?hkid=`、`?refno=` 等参数的值 | `<PARAM>` |
| `secret_field` | `password=` / `pwd:` 后的值 | `<SECRET>` |
| `long_hex` | 32 位以上十六进制串（签名、追踪码） | `<HEX>` |

链接与请求行另外只保留「主机 + 路径 + 参数名」，查询值整段丢掉；
`mailto:` 一类非 HTTP 协议单独处理，不会拼出 `mailto://<SITE_HOST>…` 这种假地址。

---

## 5. MCP 工具面

`pagekit` 也能直接当 MCP 服务用（stdio，无第三方依赖），工具名与常见页面工具一致：

| 工具 | 参数 | 返回 |
|---|---|---|
| `page_open` | `url`、`name`、`headed`、`profile`、`terms` | 摘要（Markdown）+ 计数 + 运行目录 |
| `page_capture` | `name` | 当前页摘要 |
| `page_click` | `target`（`css=…` / `text=…` / 纯文本） | 点完后那一页的摘要 |
| `page_text` | — | 当前正文（已脱敏） |
| `page_digest` | — | 最近一页摘要全文 |
| `redact_text` | `text` | 脱敏结果 + 规则计数 |
| `verify_text` | `text` | 残留清单；有残留时 `isError` 为真 |
| `page_close` | — | 关闭会话 |

客户端配置（`cwd` 指向 `booking-lab`，路径按本机实际位置填）：

```json
{
  "mcpServers": {
    "pagekit": {
      "command": "python",
      "args": ["-m", "pagekit.mcp_server"],
      "cwd": "<repo>/booking-lab"
    }
  }
}
```

自检（不经客户端，直接用 stdin 打一帧）：

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m pagekit.mcp_server
```

---

## 6. 步骤文件

```json
{
  "steps": [
    { "click": "css=input[name=conditionsCheck]" },
    { "click": "text=预约" },
    { "wait": 1500 },
    { "snapshot": "02-auth" }
  ]
}
```

| 键 | 含义 |
|---|---|
| `click` | 点元素；`css=` 走选择器，`text=` 走可见文本，不写前缀时按「像选择器就当选器」判断 |
| `goto` | 直接跳转 |
| `fill` | `{"target": ..., "value": ...}`，只在操作者明确要填时才用 |
| `wait` | 毫秒 |
| `snapshot` | 抓取当前页并命名（不产生动作时也能用，用于同一页的不同状态） |

**每一页抓完都会立刻出摘要**，所以中途失败也留下了前几页的产物。

---

## 7. 退出码

| 码 | 含义 | 处理 |
|---|---|---|
| 0 | 完成 | — |
| 2 | 参数/文件不存在 | 看提示改路径 |
| 3 | `verify` 发现残留 | 补词表后重跑 `digest` |
| 4 | Playwright 或浏览器不可用 | `python -m pip install playwright && python -m playwright install chromium` |
| 5 | 点不到元素、抽取失败 | 用 `--headed` 看着跑，确认选择器 |

---

## 8. 不变量

1. **原始快照不出本机。** 只有 `digest.*` 是外发物，其余留在 `runs/`。
2. **摘要在 `build_digest` 里一次成形。** 先脱敏再拼装，不存在「先拼好再补遮」的路径。
3. **命中记录不含原文。** 统计行写的是占位符（`term:<PRIVACY>×2`），连报告本身都能外发。
4. **同一词同一占位。** 跨页比对时不会出现「这页叫 `<T1>`、那页叫 `<T3>`」。
5. **未登记主机原样保留。** 站点身份由词表负责，工具不猜。
6. **节流仍在。** 本模块只按操作者的步骤走，不循环、不批量、不并发；批量行为仍归 `throttle.py` 管。

---

## 9. 自检

```bash
cd booking-lab
python -m pytest tests/test_pagekit_redact.py tests/test_pagekit_digest.py -q   # 16 passed
python -m pytest captcha_flow/tests tests -q                                    # 96 passed
cd .. && python tools/wording_lint.py --root .                                 # block 0
```
