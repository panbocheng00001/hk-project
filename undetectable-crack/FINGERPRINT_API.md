# Undetectable 本机 REST API：批量建 20 个不同指纹 profile

## 1. 路由表（从 `LocalRestServer` 的 RTTI 处理器名还原，`STAR` = 路径尾随 `<id>`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/status` | 探活 |
| GET | `/list` | profile 列表 |
| GET | `/configslist` | 可用 config（`configid` 取值来源） |
| GET | `/timezoneslist` | 可用时区 |
| GET | `/folderslist` `/groupslist` | 文件夹 / 分组 |
| POST | `/profile/create` | **建 profile（本文重点）** |
| POST | `/profile/update/<id>` | 改配置（同一套字段） |
| GET | `/profile/start/<id>` | **启动（开窗口）** |
| GET | `/profile/stop/<id>` | 停止 |
| GET | `/profile/getinfo/<id>` `/profile/cookies/<id>` | 查询 |
| GET | `/profile/delete/<id>` `/profile/clearcache/<id>` `/profile/cleardata/<id>` `/profile/clearcookies/<id>` | 清理 |
| POST | `/profile/export` `/profile/import` `/profile/syncmany` `/profile/tocloud` `/profile/tolocal` | 导入导出/云 |
| GET | `/browser/opentab/<id>` `/browser/getpage/<id>` | 浏览器操作 |
| POST | `/browser/click|fill|evaluate|openurl|press|screenshot|scroll|select|focus/<id>` | 浏览器操作 |
| GET/POST | `/proxies/list` `/proxies/add` `/proxies/update/<id>` `/proxies/delete/<id>` | 代理 |

端口 = `settings.json` 的 `apiPort`（用户自设），脚本用 `netstat` 按进程 PID 自动探测。

## 2. `/profile/create` 请求体字段（由解析函数 `0x1402394E0` 的字符串/转换还原）

| 字段 | 类型 | 取值 / 说明 | 证据 VA |
|---|---|---|---|
| `configid` | string | 缺省 `-1`（默认 config），来自 `/configslist` | `0x14023952B` |
| `name` | string | 必填，空则 `Name is empty!` | `0x140239656` |
| `os` | string | `windows` / `android` / `iphone` / `ipad` / `linux` / `mac` | `0x1402396C8` |
| `browser` | string | `chrome` / `edge` / `firefox` / `ie` / `opera` / `safari` / `yandex` | `0x1402399AE` |
| `notes` | string | 备注 | `0x140239BC0` |
| `folder` | string | 文件夹 | `0x140239D26` |
| `tags` | array | 标签 | `0x140239D8A` |
| `geolocation` | string | `off` / `auto` / `"lat:lon"`（正则 `([^:]*):`） | `0x140239EF1` |
| `auto_allow_geo` | bool | 自动允许定位 | `0x14023A219` |
| `cpu` | int | 逻辑核心数 | `0x14023A2CE` |
| `memory` | int | 内存 GB | `0x14023A5D0` |
| `proxy` | object | `{type,host,port,login,password,ipchangelink}` 等 | `0x14023A92A` |
| `resolution` | string | `1920x1080` 这类 | `0x14023A9B1` |
| `timezone` | string | IANA 时区；`System` / `Auto` 为特殊值 | `0x14023AACE` |
| `language` | string | `en-US` 这类 | `0x14023AE2E` |
| `cookies` | array | 导入 cookie | `0x14023B0B0` |
| `type` | string | `local` / `cloud` | `0x14023B1AB` |
| `group` | string | 分组，未知值报 `Unknown group:` | `0x14023B355` |
| `accounts` | array | `{website, username, password}` | `0x14023B7D7` |
| `webrtc` | string | `Off` 等 | `0x14023BDD4` |

顶层还有 `unique_name`(bool)：为 true 时服务端做重名校验，冲突返回
`Profile with name '%1' already exists`。

响应：成功 `{"profile_id": "<id>"}`；失败 `{"error": "<原因>"}`
（`Limit has been reached` / `You haven't got permissions to create profiles!` / `unable to get config!` / `Name is empty!` …）。

> WebGL/Canvas/Audio/字体/ClientRects 这些深水指纹不在这套字段里，由 `configid`
> 对应的 config 决定（服务端下发，含 `limit` 与配置数据）。要“更深”的差异，
> 从 `/configslist` 里循环取不同 config id。

## 3. 脚本

* `create20.py`：创建 20 个不同指纹 + 可选启动窗口，产出 `create20-report.json`。
* `mock_undetect_server.py`：本地 mock（无客户端时联调用）。

```powershell
# 只创建
python create20.py
# 创建并逐个开窗口，指定启动页
python create20.py --start --url https://ipinfo.io
# 指定端口 / 只打印请求体
python create20.py --port 3001
python create20.py --dry-run
```

20 组指纹取自 5 种 os × 6 种浏览器 × 20 个时区/语言/经纬度/分辨率/CPU/内存组合，
两两不同（本地 mock 验证：20 条 tuple 去重后仍为 20）。

加代理：把 `"proxy": {"type":"socks5","host":"1.2.3.4","port":1080,"login":"u","password":"p"}`
塞进 body；`create20.py` 的 `build_body()` 里按需加一行即可。
