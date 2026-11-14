# booking-lab —— 预约流程辅助工具

一个本机运行的浏览器自动化工具，用来把**重复的填表步骤**压缩掉：
打开预约页 → 选服务 → 填申请人资料 → 上传证件影像 → 选日期时段 → 提交。
验证码、排队等待、OTP 这类必须由人判断的环节，工具只负责**发现并交接**，不代替判断。

> 本文所有示例域名都是 `example.test` 占位符。
> 真实目标地址通过本机 `config.json` 提供，不入库。见 `docs/SECURITY_MODEL.md`。

---

## 1. 它解决什么问题

手工做一次完整预约，有 40 多个字段要填、4 张图要传、一次 OTP 要收。
真正需要人做的只有两件事：**核对资料对不对**、**完成验证码**。
其余都是机械复制粘贴，且必须在很短的会话有效期内完成。

工具的定位就是把机械部分自动化，把判断部分留给人：

| 环节 | 谁做 | 说明 |
|---|---|---|
| 打开页面、走服务选项 | 工具 | 固定路径的点选 |
| 填写申请人资料 | 工具 | 从本机配置读，字段与人给的资料逐字一致 |
| 上传证件影像 | 工具 | 本地文件按表单要求塞入 |
| 选择日期时段 | 工具 | 按 `bureau_preference` 顺序取最早可用 |
| **验证码** | **人** | 工具识别类型、抬窗口、响铃通知；滑块/点选/图形码一律不猜 |
| **OTP** | **人** | 工具等待并填入，取码动作由人完成 |
| 提交 | 工具 | 填完即提交，回执留档 |

---

## 2. 目录结构

```
booking-lab/
├─ captcha_flow/            验证码交互状态机（本仓库的核心，纯逻辑、可单测）
│  ├─ model.py              分类 / 结果 数据类型
│  ├─ detect.py             只读 DOM 探测
│  ├─ policy.py             纯函数决策：什么能自动、什么必须交人
│  ├─ session.py            状态机：按决策执行，管超时与交接节奏
│  ├─ adapters.py           唯一的浏览器耦合层（Playwright / 测试假实现）
│  └─ tests/                30 项离线用例，不依赖浏览器与网络
├─ extension/               浏览器扩展（页面内脚本，不经过调试协议）
│  ├─ captcha_guard.js      验证码分类与交接（与 captcha_flow 同一套策略）
│  ├─ content.js            页面内状态机：判断当前在哪一步、执行该步
│  ├─ background.js         注入调度、本地服务通信、网络记录
│  ├─ netrec.js / netrelay.js  接口记录与转发
│  └─ manifest.json         权限声明
├─ fill_form.py             表单填写主流程（Playwright）
├─ captcha_handoff.py       Python 侧的验证码交接入口
├─ http_flow.py             无浏览器流程：纯 HTTP 走完排队与名额查询
├─ session_pool.py          会话票据池（预热 / 续期 / 取用）
├─ edge_challenge.py        边缘挑战（412 JS 挑战）识别与退避
├─ edge_token.py            挑战通过后的 token 落库
├─ check.py                 名额查询（只读，不提交）
├─ flow_verify.py           端到端自检
├─ throttle.py              全局节流与熔断（防止把自己打到限流）
├─ journal.py / runlog.py   全链路事件账本与逐步回放
├─ notifier.py              关键节点通知（webhook，未配置则静默）
├─ ext_server.py            扩展的本地数据服务（127.0.0.1）
└─ docs/                    见 docs/_index.md
```

---

## 3. 怎么跑

### 3.1 准备配置

```bash
cp config.example.json config.json
```

`config.json` 里填三类东西：

1. `applicants[]` —— 申请人资料。**必须与证件逐字一致**，工具不做任何规整。
2. `files_dir` —— 证件影像目录。
3. `notify` —— 可选。webhook 地址从环境变量读，不写进文件：

```bash
set BOOKING_WEBHOOK_URL=https://webhook.example.test/robot/send?access_token=...
set BOOKING_WEBHOOK_SECRET=SEC...
```

没有配置就静默跳过，不影响主流程。

### 3.2 只读检查（不提交、不占会话）

```bash
python check.py                # 探一次
python check.py --loop 180     # 每 3 分钟探一次
```

退出码：`0` 有名额 / `10` 无名额 / `20` 未探到 / `30` 站点故障 / `40` 出口受限。

### 3.3 走完整流程

```bash
# 起本地数据服务（扩展要用）
python ext_server.py

# 起浏览器（带扩展）
pwsh -File run_local.ps1 -Instance w1

# 或者不用浏览器，纯 HTTP 走排队 + 查名额
python http_flow.py --probe
```

### 3.4 验证码测试（离线）

```bash
python -m pytest captcha_flow/tests -q
```

30 项用例覆盖：分类映射、策略边界、自动预算上限、超时降级、
「滑块路径不产生任何自动点击」这条硬约束。

---

## 4. 设计上刻意不做的事

写在这里，是因为这些"不做"是设计决策，不是待办：

1. **不识别验证码内容**。滑块位移不由算法推算，图形码不做 OCR，点选题不做目标检测。
   这类实现会把"人判断"变成"机器猜"，猜错污染会话，在短窗口里得不偿失。
2. **不伪造输入可信度**。不使用调试协议一类能生成 `isTrusted=true` 事件的通道。
   页面看到的输入就是脚本合成的事件，不伪装成硬件输入。
3. **不改环境参数冒充他人设备**。UA、时区、语言、DPR 都取本机真实值。
   配置里 `locale`/`timezone` 只允许设成本机实际值，见 `preflight.py` 的自检。
4. **不做无限重试**。每次自动动作有次数上限，触顶即转人工。
   `throttle.py` 对整体请求速率做熔断。
5. **不并发轰炸**。`workers` 默认 2，多会话只是增加被抽中的机会，
   不改变排队本身由服务端随机分配的事实。

---

## 5. 关于"能不能用这个工具"

工具本身是一套表单自动化 + 会话管理代码。它是否适合用在某个具体站点，
取决于那个站点的服务条款与当地规则，这不属于代码能回答的问题，
所以在文档里也不假装回答。

代码层面能保证的是：
- 不伪造身份、不修改浏览器指纹、不绕开人机校验
- 所有自动化动作有次数上限与节流
- 真实资料、凭据、目标地址都不在仓库里

---

## 6. 变更记录入口

- 状态机的设计取舍：`docs/CAPTCHA_DESIGN.md`
- 安全边界与威胁模型：`docs/SECURITY_MODEL.md`
- 归档说明（哪些原始记录被移出、为什么）：`ARCHIVE_MANIFEST.md`
