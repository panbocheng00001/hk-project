# Undetectable.exe 1.0.0 授权与限制机制分析 + 绕过补丁

样本: `Undetectable.exe` (18,703,728 bytes)
sha256: `aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a`
补丁后: `out/Undetectable.exe`
sha256: `419ad4ca6b5889cf81e18cc66795297094f28acf211b8e7d772b4891d6a2f98c`（仅 8 字节差异）

---

## 1. 一句话结论

**可以。**「浏览器窗口数」和「本地 REST API 调用次数」在客户端都没有硬性计数/并发闸门，
唯一拦你的是**每日创建 profile 的额度**（`profile_create_daily_limit`），
它完全在本地判断，一处函数即可废掉；补丁后同时解除创建拦截与 Free 版功能锁。

真正改不了的只有**账号级并发登录会话数**（服务端 `Session quota exceeded`），
但那不是窗口数，是同一账号同时登录的机器/会话数。

---

## 2. 技术画像（静态证据）

| 项 | 值 |
|---|---|
| 格式 | PE32+ (x64)，GUID 无，PDB 路径泄漏 `C:\Users\A\Desktop\ultima_depl\startwindow\Release_Desktop_Qt_6_11_2_MSVC2022_64bit\release\Undetectable.pdb` |
| 框架 | Qt 6.11.2 (Widgets/Core/Network) + 自研 Chromium 内核（`BrowserChrome` / `BrowserHost` / `PageHost` / `WebEngine` 双内核） |
| 托管/脚本 | 无 CLR 目录、无 Go buildinfo、无 Rust `.dep-v0`、无 asar → 纯原生 C++ |
| .text | 4.6 MB，`.pdata` 里 14,537 个函数 |

## 3. 授权模型

登录请求体（0x140200930 构造）：`Auth-Data: username, password, hw_hash, profiles_count,
device_description, app_version`；`hw_hash = SHA256(hex(machineUniqueId|kernelType|cpuArch|user|host))`。

服务端（`ant-api.ahost.pro/api/app/v1/*`、`api.undetectnews.com/v2/*`）返回 JSON，
在 `0x1401FF190` 里逐字段解析进账号单例（`0x14115C30`）：

| JSON 字段 | 账号对象偏移 |
|---|---|
| License-Id / License-Id-Owner / License-Expiry | +0x98 / +0xC8 / +0x90 |
| User-Count / RoleName / Username | +0x94 / +0x118 / +0x130 |
| Plan-Group / Plan-name | +0xE8 / +0x100 |
| License-User-Id / Free-Config | +0xB0 / +0xE0 |
| Limit-Public-Profile / Profile-In-Public / Profile-In-Private / Private-Storage | +0x148 / +0x14C / +0x150 / +0x154 |
| `Premissions`（数组） | 列表在 +0x10 |
| `limit{limit, profile_create_daily, profile_create_daily_limit}` | 交给 `LimitsObserver` 单例 `0x1411526A8`（+0x10 开关 / +0x14 已用 / +0x18 上限） |
| `plan_name.lower()=="free"` | isFree 字节 +0x28 |

`LimitsObserver`（RTTI `.?AVLimitsObserver@@` @ `0x14108D6E8`，vtable `0x1404BC4E0`）关键方法：

| VA | 语义 |
|---|---|
| `0x140213400` | `isLimitReached()`：`limit_enabled && used >= limit` → 1 |
| `0x140213420` | `getLimit()` |
| `0x1402134F0` | `increment()`：`if(limit_enabled) ++used; emit limitsChanged()` |
| `0x140213500` | `setLimits(QJsonObject)`：解析 limit / profile_create_daily / profile_create_daily_limit，>9999 视为无限 |

## 4. 三个执行点（全部在客户端）

1. **UI 新建 profile**：`0x1401D776D → call LimitsObserver::instance()`；`0x1401D7775 call isLimitReached`；
   为真则 `jmp 0x1403D4310`（弹「You reach daily limit and need to change plan」），
   否则 `0x1401D7798` 继续走创建。
2. **本地 REST API 新建**：`/profile/create` 处理器 `0x140232880`；
   `0x1402328EA call instance` / `0x1402328F2 call isLimitReached`；
   为真返回 `{"error":"Limit has been reached"}`。
   通过额度检查后进入 `0x1402394E0` 请求解析，里面还有**第二道门**：
   `0x14023B70C call Account::instance()` → 检查 `Premissions` 是否含 `profile_create_local`
   （`0x14023B754 QStringList_contains`）；不含则 `0x14023B76C` 返回
   `{"error":"You haven't got permissions to create profiles!"}`。
   之后取本地 config（`0x140226B8B` 处失败报 `unable to get config!`，无网络调用）→ 落库。
3. **计数**：创建成功回调 `0x1401B5AA0` 内 `0x1401B5D71 call 0x1402134F0` 自增；额度与用量由服务端下发。
4. **API 启动 profile**：`0x14022E9AA call Account::instance()`，`0x14022E9AF cmp byte[rax+0x28]`(isFree)
   → Free 账号进入分支后再判断 `[profile+0x48]`，命中则 `0x14022E9DE` 返回 `{"error":"You haven't got permissions!"}`；
   isFree=0 时直接放行到正常启动。

权限令牌（`Premissions`）不是配额而是功能开关，文案/令牌为
`profile_create_local, profile_create, mass_creator, profile_export, profile_transfer,
profile_update_group, profile_delete, profile_import, profile_export_cookies`；
校验处 `0x1401DA7B0`（按钮 enable）、`0x1402394E0`（REST 路由，100 处内联 `QStringList_contains`）。

## 5. 「窗口」与「API」到底有没有上限

* 同时在跑的 profile/窗口：全量字符串与代码里**没有任何**并发/最大窗口判断
  （无 `too many/concurrent/simultaneous/max profiles` 文案，无对应比较分支），只受内存/CPU 限制。
* 本地 REST API（`LocalRestServer`，端口取 settings.json 的 `apiPort`）：无速率/次数配额；
  只有 (a) 每日创建额度、(b) 权限令牌、(c) 角色（admin/user）三种门。
* 服务端 `session`：登录会话数限制（`Session quota exceeded` /
  `The session limit for the license has been reached.` / `The user's session limit has been reached.`，
  映射表在 `0x140203850`），属账号维度，客户端补丁无效。

## 6. 补丁

文件偏移换算：`off = VA - 0x140001000 + 0x400`。

| 补丁 | VA | off | 原字节 | 新字节 | 作用 |
|---|---|---|---|---|---|
| P1 | `0x140213400` | `0x212800` | `80 79 10 00 74 0B …` | `32 C0 C3` | `isLimitReached()` 恒 false → UI/API 每日额度失效 |
| P2（可选） | `0x1401FFC04` | `0x1FF004` | `E8 07 FE EC FF` | `31 C0 90 90 90` | `call "free" 比较` → `xor eax,eax`，isFree=0：解锁 Free 版功能锁（Mass Creator）+ 跳过 API 启动 profile 的 Free 分支 |
| P3（可选） | `0x14023B76A` | `0x23AB6A` | `75 19` | `EB 19` | REST `/profile/create` 权限检查 `jne`→`jmp`，不再要求 `profile_create_local` 令牌 |

补丁后 sha256：`98f892ddbbb8126a871efcff5c798cb788f07869994bc8c0dae9ddd9db8811e0`（与原件差 9 字节）。

复现：`python patch_undetectable.py`（已生成 `out/Undetectable.exe`）。

## 7. 验证

1. 用补丁版登录（账号本身需要能过鉴权；Free 账号即可）。
2. UI 连续新建 profile 超过原额度：不再弹「daily limit」，`LimitsWidget` 仍显示旧计数（纯显示）。
3. API：
   ```powershell
   curl "http://127.0.0.1:<apiPort>/profile/create?name=t1"
   curl "http://127.0.0.1:<apiPort>/profile/create?name=t2"   # 循环数十次
   ```
   预期不再出现 `{"error":"Limit has been reached"}`。
4. 并发开窗：同时启动多个 profile，观察 `Undetectable.exe`/`chrome` 子进程数只受硬件限制。

## 8. 注意

* 补丁破坏数字签名，且程序自带 updater（`UndetectUpdate.lock`、`cdn.undetectnews.com/updater/…`）
  会在升级时用官方 exe 覆盖，升级后需重新打。
* 服务端仍能看到 `profiles_count` 与登录会话；账号级风控（多设备/超量）不在本地补丁范围内。
