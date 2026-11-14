# IPWO 代理连不上：结论与落地步骤

> 2026-11-10 23:14–23:45 实测 + 厂商文档核对。对象：`us.ipwo.net:7878` 那批香港住宅代理。

## 一、结论（不是过期、不是密码、不是批次）

请求在**来源 IP 检查那一层**就被打回，永远走不到"用你的 sid 去选香港出口"这一步：

```
真 sid（time_180）  → 403 {"code":403,"msg":"access denied,china IP is not allow"}
假 sid（sid_00000000）→ 同一句
乱写账号（abc:def）  → 同一句          ← 账号根本没被校验
不带认证             → 407             ← 说明"要认证"，但校验顺序在 IP 之后
```

穷举过的变量，全部同结果：

| 变量 | 试了什么 | 结果 |
|---|---|---|
| 入口 IP | `51.81.143.209–215`、`.240–247` 共 14 个 | 14/14 同一句 |
| 端口 | 7878 / 8443 / 3128 / 8888 / 9000 / 10000 | 全部同一句（443、80 直接拒连） |
| 有效期 | `time_10` 与 `time_180` 两批 | 完全一致 |
| 跳板 | 本机 10809（Cloudflare WARP，出口 NL） | 403 / 400 |

本机出口固定 `14.127.169.56`（CN / AS4134 深圳），就在被拒的那一侧。

## 二、厂商自己的文档就是这么写的

- [海外网络环境说明](https://docs.ipwo.net/huan-jing-zhun-bei/hai-wai-wang-luo-huan-jing-shuo-ming.md)：
  > 「由于网络政策及运营环境等因素影响，IPWO 代理服务目前仅支持在海外网络环境下使用。」
  > 「国内网络环境下无法正常使用代理服务。」
- [为什么需要海外网络环境](https://docs.ipwo.net/dai-li-ji-chu-zhi-shi/wei-shen-me-xu-yao-hai-wai-wang-luo-huan-jing.md)：
  > 「香港 / 日本 / 新加坡 / 美国」为推荐地区；推荐形态是「海外 VPS → 住宅代理 → 浏览器/程序」。
  > 官方给的测试命令：`curl -x "us.ipwo.net:7878" -U "username:password" ipinfo.io`

所以这不是哪一批 IP 的问题，是**运行环境必须不在大陆**。本机那个 WARP 跳板（Cloudflare 出口）也不行 —— 它属于机房/加速器出口，不是文档说的"海外 VPS"形态。

## 三、落地路径（两条，选一条）

### A. 程序直接跑在海外 VPS 上（推荐，最省心）

```
香港 VPS（最低配 1C1G 够用）
  ├─ python ext_server.py / token_pool.py / grab.py
  ├─ 浏览器（CfT）+ 扩展跑在同一台
  └─ 出口 = ipwo 给的香港住宅 IP
```

先验证环境，两条命令，30 秒出结果：

```bash
curl ipinfo.io                                   # 必须是 HK/JP/SG/US，不能是 CN
curl -x "us.ipwo.net:7878" -U "_custom_zone_HK_sid_xxx_time_180:" ipinfo.io
```

### B. 浏览器留本机，只把代理那一跳送出去

适合还要人手过验证码、盯着钉钉的场景：

```bash
# VPS 上
ssh -N -D 1080 root@VPS_IP

# 本机：每条代理一个本地口 8900…8909，下游只连 127.0.0.1:89xx
python proxy_chain.py --upstream-file proxies.txt --listen-base 8900 --hop socks5://127.0.0.1:1080
```

## 四、验收顺序（每步都别跳）

```powershell
# 1) 来源通了没
python proxy_check.py --file proxies.txt
#    要看到：可用 10/10、country=HK、两次同 IP、abs3 200/412

# 2) 能不能过 WAF（这才是硬指标）
python token_pool.py --warm --proxies proxies.txt --workers 4

# 3) 过闸的票入池，17:00 直接用
python token_pool.py --stats
```

`--warm` 全灭 = 出口被 WAF 判死，跟排队无关；过了 6 成以上才值得谈抢号。
