# 明天（2026-11-12）抢号 Runbook

> ⚠ 2026-11-11 晚已改路线：**代理版（本文）作废，按 `明天_本地IP_单实例_runbook.md` 执行**。
> 原因：IPWO 香港住宅代理在大陆来源 IP 直接 403（`代理_来源IP限制_结论.md`），明天只用本机家宽。
> 本文保留作「4 窗口 + 代理」备选，里面的红线（别按 F5 / 别 kill 总控 / 别清 cookie）仍然有效。

> 时间表已写进 `race.py`：`python race.py --plan` 随时可看，`--dry-run` 先体检一圈不起浏览器。

## 一、时间表（默认参数就是这个）

| 时刻 | 动作 | 命令 |
|---|---|---|
| **16:00** | 站点体检 | `curl -s -o NUL -w "%{http_code}" https://abs3.td.gov.hk/?lang=sc`（412/超时都算正常；只有 502/维护页才停手） |
| **16:15** | 代理体检 | `python probe_proxy.py --file proxies.txt`（只留住宅、出口一致的） |
| **16:20** | 预热票池（**不弹窗**） | `python token_pool.py --warm --headless` |
| **16:22** 起 | 起 w1，之后每 30–35s 起 w2/w3/w4 | `powershell -File launch_ext.ps1 -Instance w1` |
| **16:35** | 确认 4 条都进了 queue | `Get-Content runs\watch.log -Tail 3` |
| **16:50** | 再刷一次票，覆盖到 17:20 之后 | `python token_pool.py --warm --headless` |
| **16:57** | 坐到屏幕前，钉钉/声音打开 | — |
| **17:00** | 洗牌放行 → 扩展自动走；卡在 SelectCountry 就手点「中国内地」 | — |
| **17:00–17:20** | 放行即自动 ingest，跑满 20 分钟窗口 | `python replay.py --pool --ip-id w1-form` |
| **17:20–18:00** | 等取消回流，窗口别关 | `python race.py --no-browser --until 18:00`（只盯 ingest） |

## 二、今天踩到并修掉的 6 个坑

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 4 个窗口卡在 412 挑战页干等 | 挑战页有 5.6KB，被当成「页面已加载」，只打心跳不重载 | 识别 `$_ts`/`nsd=` → 自动 reload（≤5 次） |
| 2 | 洗牌期 reset 后 3 次就放弃 | 空白/错误页 reload 上限 3 | 上限 12、间隔 6s |
| 3 | `country` 阶段卡死 | SelectCountry 是卡片式 DOM，没有 `input[type=radio]` | 新增文本兜底：点最内层含「内地/Mainland」的元素 |
| 4 | 停总控时 4 个窗口一起消失 | Windows job 对象：杀父进程连带收走子孙 | 启动器加 `DETACHED_PROCESS | start_new_session` |
| 5 | `--health` 把好票全判死 | 比对出口时**没走代理**，拿到本机 `14.127.x` | 新增 `SlotClient.raw_get()`，探测与比对都走票自带代理 |
| 6 | 挑战窗口里 412 被当票废 | 16:30 后 WAF 对所有无 JS 客户端返回 412 | 加基线：只有基线干净 200 时 412 才算票废；基线不通 = 不可判定 |

附：`due()` 现在把 `dead` 的票立刻排进重铸（原来要等 TTL 走 60%），否则「判死了却排不上队」。

## 三、操作红线（今天的血）

1. **不要 kill 总控进程**。它会连带收走它起的浏览器（已加 DETACHED，但别试）。
2. **17:00–17:20 不要跑 `token_pool --warm` 的 headful 版本**：会弹窗、抢焦点，还会被误认成队列窗口。刷新一律 `--headless`。
3. **不要连按 F5**。挑战页 6 秒重试一次就够；连按 15 次会触发限流，越按越进不去。
4. **票和出口必须同源**。浏览器直连（深圳 CN）铸的票只给它自己用；代理铸的票（`p001/p003/p005`）只走同一代理。
5. **别信 16:30–17:10 之间的 `--health`**。那个时段 curl 过不了挑战，判断全是噪声（已改为不可判定）。

## 四、开跑前的 30 秒自检

```powershell
python race.py --dry-run
```

期望输出：

```
本地服务 8765：在线
代理清单 proxies.txt：N 条
票池：x/N 张可用
```

三项都正常再执行：

```powershell
python race.py --stagger 32 --until 18:00
```

它会：起 4 个窗口（间隔 32s）→ 每 120s headless 刷票 → 盯 `runs/session_*.json` 自动 ingest → 全程记 journal。

## 五、明天还差的一块

`SelectCountry` 的文本兜底是**盲写**的（没拿到真实 DOM，快照拍在渲染前只有 39 字节）。
如果明天它还是点不动，日志会打出页面前 6 个可点元素的文本：

```
country 文本兜底也没找到「内地」，DOM 里可点元素：xxx | yyy | zzz
```

把那行发我，我按真实结构改一次就通。要更稳的话，明天 16:50 之前先手动走一遍到 SelectCountry 页，让扩展把完整 DOM 存成
`runs/session_country_*.json`（现在 country 阶段会自动 dump），我能提前改好。
