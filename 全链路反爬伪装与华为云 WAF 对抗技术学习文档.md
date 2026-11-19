# 全链路反爬伪装与华为云 WAF 对抗技术学习文档

**免责声明**：本文仅用于网络安全技术学习、合法的网站兼容性测试与授权渗透测试研究，所有技术手段均需在获得目标站点书面授权的前提下使用，严禁用于未经授权的数据爬取、恶意访问等违反《网络安全法》《数据安全法》及相关法律法规的行为。本文算法分析基于公开防护机制、业界同类 WAF 架构推导及公开技术资料整理，核心加密逻辑随 WAF 版本迭代动态更新。

本文档完整汇总了从「curl\_cffi 门控 \+ 浏览器执行」基础框架，到 OSI 七层全链路参数级对齐，再到华为云 WAF 专项检测体系拆解、JS 挑战校验算法逆向的全部内容，供技术学习与对照参考。

---

# 第一篇 全链路反爬伪装方案：curl\_cffi \+ 智能门控 \+ 浏览器协同

## 核心认知：为什么单纯组合还不够

反爬检测是**七层全链路的特征一致性校验**，curl\_cffi 仅解决了「TLS JA3/JA3S \+ HTTP/2 传输层指纹」，而真实风控会覆盖网络层、TCP 层、HTTP 协议层、JS 主动指纹、行为指纹、环境一致性等近百个检测点。伪装度不足的核心原因是**特征不自洽**和**缺少 JS 执行能力**，必须从底层到应用层做全维度补全。

下面从底层传输到上层行为，结合门控架构，给出完整的技术实现方案。

## 一、底层拉满：curl\_cffi 传输层指纹极致优化

很多人仅使用 `impersonate` 参数，浪费了 curl\_cffi 的细粒度指纹控制能力。传输层是所有伪装的基础，必须先做到 100% 对齐。

### 1\. TLS 指纹核心配置

- 优先使用官方维护的最新浏览器指纹，确保 JA3/JA3S、扩展顺序、密码套件、椭圆曲线完全对齐真实浏览器。

- 禁止自定义 TLS 扩展、密码套件，避免破坏指纹一致性。

- 开启 TLS 会话复用、OCSP 装订，对齐真实浏览器的握手行为。

### 2\. HTTP/2 指纹对齐

curl\_cffi 的 impersonate 已默认对齐浏览器的 HTTP/2 特征，包括：

- 伪头顺序（:method / :path / :authority / :scheme）。

- SETTINGS 帧参数（初始窗口、最大并发、帧大小）。

- 流优先级、PING 帧周期、WINDOW\_UPDATE 行为。

- 注意：**非必要不要手动修改 HTTP/2 参数**，任何自定义都会破坏指纹。

### 3\. 基础客户端最优配置

```python
from curl_cffi import requests
from curl_cffi.requests import Session

def create_curl_session(impersonate_target: str = "chrome130", proxy: str = None) -> Session:
    session = requests.Session(
        impersonate=impersonate_target,  # 核心：TLS+HTTP2指纹对齐
        timeout=30,
        allow_redirects=True,
        max_redirects=5,  # 和浏览器默认重定向次数一致
        verify=True,      # 必须开启证书校验，爬虫常关此选项是强特征
        http2=True,       # 对应目标浏览器的HTTP协议版本
    )

    # 代理配置（必须和指纹、IP地理位置对应）
    if proxy:
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }

    # Cookie Jar 自动管理，禁止手动硬编码Cookie
    session.cookies.set_policy(requests.cookies.DefaultCookiePolicy())

    return session
```

## 二、网络层补全：TCP 与 IP 指纹伪装

高级 WAF（Cloudflare Enterprise、Akamai）会做深度包检测，TCP 栈指纹、IP 信誉是第一道门槛，curl\_cffi 无法控制这一层，需要单独补全。

### 1\. IP 代理三要素一致性

这是最容易忽略的强特征：

- **IP 地理位置 ↔ 时区 ↔ 语言** 必须完全匹配。例：美国洛杉矶住宅 IP → 时区 `America/Los_Angeles` → `Accept-Language: en-US,en;q=0.9`。

- 代理类型优先级：住宅 ISP 代理 \> 纯住宅代理 \> 数据中心代理。

- 禁止使用公开免费代理，IP 进入风控黑名单的概率接近 100%。

- 轮换策略：按「失败次数 \> 请求数 \> 时长」优先级轮换，避免单 IP 高频请求。

### 2\. TCP 指纹对齐

不同操作系统的 TCP 栈参数（初始窗口大小、TTL、MSS、分段偏移）有明显差异，如果 Linux 服务器模拟 Windows Chrome 的 TLS 指纹，TCP 指纹却暴露 Linux，会直接触发特征不一致。

- 低成本方案：在 Windows 容器/虚拟机中运行爬虫，天然对齐 Windows TCP 指纹。

- Linux 优化方案：通过 `sysctl` 和 `iptables` 修改 TCP 初始窗口、TTL、MSS，模拟目标系统。

- 进阶方案：使用透明代理网关，在转发层修改 TCP 报文指纹。

### 3\. DNS 解析伪装

- 使用代理节点的本地 DNS 解析，不要使用爬虫常用的公共 DNS（114、阿里 DNS）。

- 开启 DNS 缓存，缓存时长对齐浏览器默认值。

- 避免 DNS 解析速度过快，可加入微小随机延迟模拟真实解析耗时。

## 三、HTTP 协议层：头顺序 \+ 值双维度严格对齐

TLS 指纹对了但 HTTP 头乱序，是 90% 新手被拦截的原因。HTTP 层检测点超过 30 项，必须逐点对齐。

### 1\. 请求头顺序是核心

- 真实浏览器的请求头有固定的物理顺序，curl\_cffi 的 impersonate 仅对齐了默认头的顺序，**手动添加的头会追加到末尾，直接破坏顺序特征**。

- 正确做法：用浏览器 F12 抓包，按从上到下的顺序完整复刻所有 Header，新增头也要插入到对应位置。

- 禁用爬虫特征头：不要随意加 `X-Requested-With`、`Cache-Control: no-cache`、`Pragma` 等非浏览器默认头。

### 2\. 关键 Header 真实性校验

|Header 系列|对齐要求|
|---|---|
|Sec\-CH\-UA|必须和 UA 版本、品牌、平台完全对应，引号、空格、顺序不能改|
|Accept|不同请求类型（文档/图片/脚本/接口）用对应的值，不能全局统一|
|Accept\-Encoding|对齐浏览器支持的压缩算法（Chrome 130: gzip, deflate, br, zstd），顺序一致|
|Referer|必须是上一级页面完整 URL，同域跳转必须带，不能空也不能错|
|Origin|仅跨域请求携带，同域请求不要加|

### 3\. Cookie 生命周期模拟

- 禁止硬编码 Cookie，全程用 Session 的 CookieJar 自动管理。

- 遵守 Cookie 的 Domain、Path、Secure、HttpOnly、SameSite 属性。

- 获取路径合理：先访问首页种基础 Cookie，再逐步访问页面，不能一上来就携带业务 Cookie。

- JS 生成的风控 Cookie（`cf_clearance`、`__jsl_clearance`、设备指纹 ID）**绝对不能伪造**，必须由真实浏览器执行生成。

### 4\. 请求体细节对齐

- POST 请求的 `Content-Type` 与 Body 编码严格对应。

- `application/x-www-form-urlencoded` 的字段顺序、特殊字符转义与浏览器一致。

- JSON 请求的键顺序、空格、换行格式与浏览器输出完全一致。

- 文件上传的 multipart 边界、字段顺序、文件名、MIME 类型逐字节对齐。

## 四、核心短板补全：浏览器 JS 指纹协同

curl\_cffi 是纯 HTTP 客户端，**无法执行 JavaScript**，这是伪装度不足的核心瓶颈。所有 JS 主动指纹、人机挑战、动态令牌，都必须通过真实浏览器环境生成，再同步给 curl\_cffi 使用。

### 1\. 必须浏览器生成的指纹清单

|指纹类型|检测内容|curl\_cffi 能否模拟|解决方案|
|---|---|---|---|
|基础环境|navigator、screen、history 等 BOM 对象|不能|浏览器获取后保证全链路一致|
|Canvas 指纹|2D 绘图渲染结果哈希|不能|浏览器生成稳定指纹，或注入混淆脚本|
|WebGL 指纹|显卡信息、渲染参数、着色器输出|不能|浏览器环境对齐，或注入修改|
|字体指纹|系统安装字体列表与渲染尺寸|不能|对齐代理对应系统的字体库|
|音频指纹|Web Audio API 处理结果|不能|真实浏览器环境生成|
|WebRTC 指纹|本地 IP、ICE 候选信息|不能|浏览器配置代理或禁用 WebRTC|
|JS 挑战|5 秒盾、瑞数、极验等动态令牌|不能|浏览器执行 JS 获取 clearance Cookie|

### 2\. 最优协同架构：浏览器拿凭证 \+ curl\_cffi 跑流量

兼顾伪装度与性能的工业级方案，比纯浏览器性能高 5\~10 倍，比纯 curl\_cffi 伪装度高一个量级：

1. **浏览器前置节点**：Playwright 启动加固后的浏览器，完成页面加载、JS 挑战、人机验证，获取全量合法 Cookie、请求头、令牌。

2. **凭证同步**：将浏览器的 Cookie、完整请求头、对应浏览器版本，同步给 curl\_cffi 客户端。

3. **批量请求执行**：curl\_cffi 以完全一致的指纹发起批量数据请求，承担主要流量。

4. **自动续命**：凭证过期或触发风控时，自动回退到浏览器节点重新获取凭证。

### 3\. 浏览器端反检测加固

- 去除无头特征：覆盖 `navigator.webdriver`、补全 plugins/mimeTypes、修复语言与插件空值。

- 环境一致性：浏览器 UA、时区、语言、平台，必须和 curl\_cffi 指纹、IP 地理位置完全对应。

- 持久化上下文：使用用户数据目录，模拟真实用户浏览器环境，避免每次全新无痕窗口。

- 资源加载控制：禁止全量禁用静态资源，按需屏蔽广告脚本，保留风控相关第三方脚本。

## 五、智能门控系统：分级伪装 \+ 动态降级

门控的核心是「按需分配伪装强度」，既不浪费性能，也不突破风控阈值，同时实现异常熔断与自动降级。

### 1\. 四级伪装等级门控

|等级|方案组合|适用场景|相对性能|
|---|---|---|---|
|L1 轻量|纯 curl\_cffi \+ 基础 Header \+ 代理轮换|无风控静态站点、公开 API|100%|
|L2 标准|curl\_cffi \+ 预生成 JS 指纹 Cookie \+ 全 HTTP 头对齐|轻度 JS 风控、普通企业站点|85%|
|L3 增强|浏览器前置拿凭证 \+ curl\_cffi 执行业务请求 \+ 行为模拟|中度风控、Cloudflare 免费版|40%|
|L4 极致|全浏览器真实模拟 \+ 人工行为链路 \+ 住宅代理|高强度风控、企业版 WAF|10%|

### 2\. 风控触发判定（升级依据）

通过响应特征自动判断风控强度，触发则升级伪装等级：

- 状态码触发：403、429、503、405。

- 内容触发：响应包含「验证」「机器人」「Access denied」、JS 挑战代码。

- 业务触发：接口返回异常错误码、数据为空、强制跳转登录。

- 特征触发：响应 Set\-Cookie 包含风控标识、响应头携带 WAF 标记。

### 3\. 降级与熔断机制

- 单代理连续失败 3 次 → 轮换代理，同时提升一级伪装等级。

- 单指纹连续失败 5 次 → 切换浏览器 impersonate 版本。

- 单 IP 连续失败 10 次 → 熔断冷却 30 分钟，加入临时黑名单。

- 全站风控升级 → 批量任务整体从 L1 切换至 L3 等级。

### 4\. 流量调度门控

- 请求间隔：使用正态分布生成随机间隔（如均值 2s，标准差 0\.5s），禁止固定 sleep。

- 访问路径：模拟真实用户链路「首页 → 列表页 → 详情页」，禁止直接跳深层接口。

- 并发控制：单 IP 并发数不超过 3，符合普通用户浏览器并发上限。

- 时段控制：匹配目标站点用户活跃时段，避免凌晨大批量请求。

## 六、行为指纹伪装：模拟真实用户操作

所有技术指纹都对齐，行为模式不符合人类依然会被识别。行为指纹是高阶反爬的核心检测点。

### 1\. 请求行为模拟

- 页面停留时间：详情页 \> 列表页 \> 首页，符合阅读逻辑。

- 请求深度：单会话页面跳转深度 3\~8 层，避免单页无限刷接口。

- 异常行为混入：少量重复请求、返回上一页、页面刷新，模拟用户误操作。

- 资源加载：每个页面请求配套加载 1\~2 个核心静态资源（主 JS、主 CSS），避免只刷接口。

### 2\. 浏览器端交互行为（L3/L4 等级）

- 鼠标移动：使用二阶贝塞尔曲线生成轨迹，带随机加速度，禁止直线移动。

- 点击行为：点击前有 100\~300ms 悬停，点击后有停留，不是瞬间点击。

- 键盘输入：单字符间隔 50\~150ms，模拟真实打字速度，偶尔有删除修正。

- 滚动行为：滚动速度随机变化，有回滚、停顿，不是匀速滚到底。

## 七、完整工程化代码示例

下面给出可运行的最小完整框架，包含 curl\_cffi 客户端、浏览器凭证提供、门控控制器三部分。

```python
import time
import random
from curl_cffi import requests
from curl_cffi.requests import Session
from playwright.sync_api import sync_playwright

# ========== 1. curl_cffi 强化客户端 ==========
class CurlCffiClient:
    def __init__(self, impersonate: str = "chrome130", proxy: str = None):
        self.impersonate = impersonate
        self.proxy = proxy
        self.session = self._init_session()

    def _init_session(self) -> Session:
        session = requests.Session(
            impersonate=self.impersonate,
            timeout=30,
            allow_redirects=True,
            max_redirects=5,
            verify=True,
            http2=True,
        )
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        return session

    def sync_browser_credentials(self, cookies: list, headers: dict):
        """同步浏览器的Cookie和请求头，保持指纹一致"""
        self.session.cookies.clear()
        for cookie in cookies:
            self.session.cookies.set(
                name=cookie["name"],
                value=cookie["value"],
                domain=cookie["domain"],
                path=cookie["path"],
                secure=cookie.get("secure", False),
                httponly=cookie.get("httpOnly", False),
            )
        # 注意：按浏览器顺序更新Header，不能直接update，避免破坏顺序
        self.session.headers.clear()
        for k, v in headers.items():
            if k.lower() not in ("content-length", "host"):
                self.session.headers[k] = v

    def request(self, method: str, url: str, **kwargs):
        # 正态分布随机延迟
        delay = max(0.5, random.gauss(2, 0.5))
        time.sleep(delay)
        return self.session.request(method, url, **kwargs)
```

```python
# ========== 2. 浏览器凭证提供者 ==========
class BrowserCredentialProvider:
    def __init__(self, proxy: str = None, headless: bool = True):
        self.proxy = proxy
        self.headless = headless

    def get_credentials(self, target_url: str, impersonate: str = "chrome130") -> tuple[list, dict]:
        """打开目标页面，通过JS挑战，返回合法Cookie和请求头"""
        with sync_playwright() as p:
            # 启动浏览器，对应模拟的浏览器内核
            browser = p.chromium.launch(
                headless=self.headless,
                proxy={"server": self.proxy} if self.proxy else None,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )

            # 去除无头特征
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            page = context.new_page()
            page.goto(target_url, wait_until="networkidle")

            # 等待JS挑战完成（可根据站点调整等待条件）
            page.wait_for_timeout(5000)

            # 获取Cookie和请求头
            cookies = context.cookies()
            # 从页面请求中提取真实浏览器请求头
            request_headers = {}
            def capture_request(request):
                nonlocal request_headers
                if request.url == target_url:
                    request_headers = request.headers
            page.on("request", capture_request)
            page.reload(wait_until="networkidle")

            browser.close()
            return cookies, request_headers
```

```python
# ========== 3. 智能门控控制器 ==========
class GateKeeper:
    def __init__(self):
        self.level = 1  # 初始L1轻量等级
        self.fail_count = 0
        self.fail_threshold = 3

    def check_risk(self, response) -> bool:
        """检测响应是否触发风控"""
        if response.status_code in (403, 429, 503):
            return True
        if "challenge" in response.text.lower() or "robot" in response.text.lower():
            return True
        return False

    def upgrade_level(self):
        """升级伪装等级"""
        if self.level < 4:
            self.level += 1
            self.fail_count = 0
            print(f"风控触发，伪装等级升级至 L{self.level}")

    def record_fail(self):
        self.fail_count += 1
        if self.fail_count >= self.fail_threshold:
            self.upgrade_level()

# ========== 4. 主流程示例 ==========
if __name__ == "__main__":
    TARGET_URL = "https://example.com"
    PROXY = "http://user:pass@proxy_ip:port"

    # 初始化组件
    gatekeeper = GateKeeper()
    curl_client = CurlCffiClient(impersonate="chrome130", proxy=PROXY)
    browser_provider = BrowserCredentialProvider(proxy=PROXY, headless=True)

    # 门控执行逻辑
    def fetch_url(url: str):
        if gatekeeper.level <= 2:
            # L1/L2 直接用curl_cffi请求
            resp = curl_client.request("GET", url)
        else:
            # L3+ 先从浏览器获取凭证，再用curl_cffi请求
            cookies, headers = browser_provider.get_credentials(url)
            curl_client.sync_browser_credentials(cookies, headers)
            resp = curl_client.request("GET", url)

        # 风控检测
        if gatekeeper.check_risk(resp):
            gatekeeper.record_fail()
            return fetch_url(url)  # 降级后重试

        gatekeeper.fail_count = 0
        return resp

    # 执行请求
    response = fetch_url(TARGET_URL)
    print(f"请求状态码: {response.status_code}")
```

## 八、学习与进阶建议

1. **先抓包再写代码**：永远先用目标浏览器真实访问抓包，把所有特征抄下来，再写代码对齐，不要凭感觉写。

2. **单一变量原则**：调试时一次只改一个参数，定位具体是哪个特征导致被拦截。

3. **特征一致性优先**：所有维度必须自洽，指向同一台真实设备、同一个浏览器、同一条网络链路。

---

# 第二篇 全链路反爬伪装终极方案：从物理层到行为层的零特征差对齐

承接基础框架，本篇从 OSI 七层全链路做参数级补全，覆盖绝大多数商业 WAF（Cloudflare Enterprise、Akamai、瑞数、顶象、数美等）的检测维度。核心原则：**所有层特征必须自洽，指向同一台真实设备 \+ 同一个浏览器 \+ 同一条网络链路**，任何一层的特征矛盾都会触发风控。

## 一、物理/网络层：IP 全维度一致性 \+ 链路指纹对齐

多数人只关注 IP 是否被封，忽略了 IP 本身携带的几十项附属特征，这是风控的第一道被动画像。

### 1\. IP 属性全维度对齐

|检测维度|对齐要求与实现细节|
|---|---|
|IP 类型与 ASN|优先住宅 ISP 代理，其次移动网络代理；ASN 必须对应真实运营商，禁止云厂商 ASN（阿里云、AWS、腾讯云等）冒充住宅 IP；避免 Tor 出口节点、公开 VPN IP，这类 IP 默认进入高风险池。|
|地理位置精度|城市级经纬度必须与时区、系统语言、邮编区号、运营商覆盖区域严格对应；例如洛杉矶 IP 必须配 `America/Los_Angeles` 时区、`en-US` 语言、太平洋时区夏令时规则。|
|IP 协议栈|真实用户环境多为 IPv4/IPv6 双栈，纯单栈环境特征明显；若使用双栈代理，需同时对齐 IPv6 的地理位置与运营商。|
|IP 风险评分|提前对接 IP 信誉库（如 Spamhaus、IPinfo、MaxMind），排除滥用 IP、爬虫出口 IP、攻击源 IP；新 IP 先做灰度验证。|
|DNS 一致性|必须使用代理节点本地 DNS 解析，禁止使用公共 DNS（114、阿里、8\.8\.8\.8）；防止 DNS 泄漏真实地理位置；开启 DNS 缓存，缓存时长对齐浏览器默认（Chrome 默认 1 分钟）。|

### 2\. 链路层与网络层指纹

- **MTU/MSS 对齐**：真实宽带 MTU 通常为 1500，MSS 为 1460；PPPoE 链路为 1492/1452；移动网络 MTU 更小（如 1420）。Linux 默认值与 Windows 不同，需通过 `iptables` 或路由配置修改，避免 TCP 握手时暴露系统栈。

- **TTL 初始值**：Windows 默认 TTL=128，Linux 默认 TTL=64；若模拟 Windows 浏览器，需通过 `iptables --ttl-set 128` 修改出口报文 TTL。

- **IPID 生成方式**：Windows IPID 为递增生成，Linux 部分内核为随机生成；可通过 `nftables` 修改 IPID 生成策略，对齐目标系统。

- **ICMP 行为**：禁止全量禁 ping，真实家用网络通常响应 ICMP；响应速率、报文大小对齐目标操作系统。

### 3\. 代理层指纹消除

- 必须使用**高匿代理**，禁止透明/普通匿名代理，避免代理注入 `X-Forwarded-For`、`X-Real-IP`、`Via` 等暴露真实 IP 的请求头。

- SOCKS5 代理优先级 \> HTTP 代理；HTTP 代理可能在请求头、TCP 握手中留下特征，SOCKS5 更接近原生网络行为。

- 禁止代理链路多层叠加，多层代理的 RTT 波动、路由跳数与普通用户差异极大，极易被识别。

- WebRTC 泄漏封堵：浏览器端必须禁用非代理路径的 ICE 候选，或替换 WebRTC 返回的 IP 为代理 IP，防止通过 WebRTC 拿到真实出口 IP。

## 二、传输层（TCP）：栈指纹逐参数对齐

高级 WAF 会做深度包检测（DPI），通过 TCP 握手报文反推操作系统，若 TLS 模拟 Chrome 但 TCP 栈暴露 Linux，直接判定为爬虫。

### 1\. TCP 握手核心参数对齐

|参数项|Windows 10/11 默认值|Linux 默认值|对齐方式|
|---|---|---|---|
|初始窗口大小|65535 或 64240|29200|通过 `ip route` 修改 `initcwnd`|
|MSS 协商值|1460|1460（但协商顺序不同）|通过 `iptables` 钳制 MSS|
|窗口缩放因子|8|7|`sysctl net.ipv4.tcp_window_scaling`|
|SACK 支持|开启|开启|保持开启，对齐顺序|
|TCP 时间戳|开启，时钟频率 1000Hz|开启，时钟频率 100Hz|修改内核时间戳频率|
|拥塞控制算法|CTCP / BBR|cubic / BBR|切换为对应算法|
|SYN 重传次数|2 次|5 次|`sysctl net.ipv4.tcp_syn_retries`|

### 2\. TCP 行为特征

- **保活行为**：TCP keepalive 的间隔、探测次数、超时时间对齐目标系统；禁止无保活或过于频繁的保活包。

- **重传策略**：丢包后的重传间隔、指数退避系数，不同系统实现不同；完美零丢包的链路反而可疑，可模拟极少量随机丢包。

- **连接复用**：同域名请求复用 TCP 连接，符合 HTTP keep\-alive 机制；禁止每个请求新建 TCP 连接，这是脚本爬虫的典型特征。

- **连接生命周期**：单 TCP 连接持续时长、请求数符合浏览器默认；Chrome 默认一个域名最多 6 个并发连接，HTTP/2 下单连接多路复用。

## 三、TLS 层：超越 JA3 的全握手特征对齐

curl\_cffi 核心解决了 JA3，但 TLS 握手的检测维度远不止 JA3 字符串，仅 JA3 一致只能过初级检测。

### 1\. 基础指纹：JA3 \+ JA3S 双对齐

- **JA3**：ClientHello 指纹（密码套件、扩展、椭圆曲线、点格式的顺序与值）。

- **JA3S**：ServerHello 响应指纹，由服务端选择的密码套件、扩展决定；相同服务端对不同客户端的 JA3S 不同，仅 JA3 一致无法通过 JA3S 校验。

- curl\_cffi 的 `impersonate` 参数已内置完整 JA3/JA3S 对齐，**禁止自定义 ****`cipher_list`****、****`tls_version`****、扩展列表**，任何修改都会破坏指纹一致性。

### 2\. 进阶 TLS 扩展指纹（核心漏检点）

|扩展项|检测要点|对齐要求|
|---|---|---|
|ALPN|应用层协议协商顺序|Chrome: `h2, http/1.1`；顺序不能反，不能多不能少|
|ALPS|应用层协议设置（TLS 1\.3）|新版 Chrome 默认携带，对应 HTTP/2 SETTINGS 参数|
|GREASE|随机扩展填充|Chrome 会插入随机 GREASE 值，模拟浏览器的扩展性测试；自定义 TLS 配置几乎都会丢失|
|SNI|服务器名称指示|必须与 Host 头完全一致；ECH 加密客户端 hello 需对齐支持情况|
|签名算法列表|支持的签名算法顺序|如 `rsa_pss_rsae_sha256, ecdsa_secp256r1_sha256` 等，顺序必须与目标浏览器完全一致|
|支持组（椭圆曲线）|密钥交换曲线顺序|如 `x25519, secp256r1, secp384r1`，顺序是强特征|
|密钥共享|预共享密钥对应的曲线|必须与支持组中的首选曲线对应|
|会话票证|Session Ticket|是否支持、票证数量、大小；浏览器默认开启会话复用|
|OCSP 装订|证书状态请求|是否请求 OCSP 响应，对齐浏览器默认行为|
|填充扩展|ClientHello 填充|真实浏览器会填充到固定字节大小，避免长度指纹|

### 3\. curl\_cffi 高级参数强化配置

```python
from curl_cffi import requests
from curl_cffi.const import CurlOpt

def create_ultimate_curl_session(
    impersonate_target: str = "chrome130",
    proxy: str = None,
    dns_server: str = None,
    iface: str = None
) -> requests.Session:
    session = requests.Session(
        impersonate=impersonate_target,
        timeout=(5, 30),  # 连接超时+读取超时，模拟真实网络波动
        allow_redirects=True,
        max_redirects=5,
        verify=True,  # 必须开启证书校验，爬虫关此选项是顶级特征
        http2=True,
        # 关键：禁止自动解压，保留浏览器一致的Accept-Encoding行为
        decompress=True,
    )

    # 代理配置
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    # 底层CURL选项精细化控制
    session.curl.setopt(CurlOpt.TCP_NODELAY, 1)
    session.curl.setopt(CurlOpt.TCP_KEEPALIVE, 1)
    session.curl.setopt(CurlOpt.TCP_KEEPIDLE, 60)
    session.curl.setopt(CurlOpt.TCP_KEEPINTVL, 10)

    # 指定出口网卡（多IP环境），避免多IP混用
    if iface:
        session.curl.setopt(CurlOpt.INTERFACE, iface)

    # 指定DNS服务器，对齐代理本地DNS
    if dns_server:
        session.curl.setopt(CurlOpt.DNS_SERVERS, dns_server)

    # TLS会话复用：启用会话票证与会话ID缓存
    session.curl.setopt(CurlOpt.SSL_SESSIONID_CACHE, 1)
    session.curl.setopt(CurlOpt.SSL_OPTIONS, 0)  # 不修改默认SSL行为

    # Cookie持久化，对齐浏览器Cookie存储规则
    session.curl.setopt(CurlOpt.COOKIEFILE, "")
    session.curl.setopt(CurlOpt.COOKIEJAR, "")

    return session
```

### 4\. TLS 行为特征

- **握手耗时**：TLS 1\.2 需 2RTT，TLS 1\.3 需 1RTT；握手耗时必须符合网络 RTT，不能快到不符合物理距离。

- **会话复用率**：真实浏览器会复用 TLS 会话，连续请求的握手耗时会显著降低；每次都完整握手是强爬虫特征。

- **证书校验**：必须完整校验证书链、域名、有效期、吊销状态；忽略证书错误的客户端会被直接标记。

## 四、HTTP 协议层：头/体/时序/缓存全维度精确对齐

TLS 指纹正确但 HTTP 层乱序，是 90% 拦截的根因。本节覆盖所有协议细节，做到字节级对齐。

### 1\. 请求头：顺序 \+ 值 \+ 出现场景三重对齐

**核心原则**

- **物理顺序不可变**：必须与浏览器 F12 中「从上到下」的顺序完全一致；手动添加的头必须插入到对应位置，不能追加到末尾。

- **按需出现**：不是头越多越好，比如 `Origin` 仅跨域请求携带，同域表单 POST 不携带；`Referer` 遵循 Referrer\-Policy 规则，不是所有请求都带。

**必对齐的关键头族**

|头字段|对齐细则|
|---|---|
|Sec\-CH\-UA 系列|`Sec-CH-UA`、`Sec-CH-UA-Mobile`、`Sec-CH-UA-Platform`、`Sec-CH-UA-Full-Version` 等；值、引号、空格、顺序必须与 UA 完全对应；高版本 Chrome 使用 GREASE 值，不能写错。|
|Sec\-Fetch 系列|`Sec-Fetch-Site`、`Sec-Fetch-Mode`、`Sec-Fetch-Dest`、`Sec-Fetch-User`；不同请求类型（导航、图片、脚本、接口、iframe）对应不同的值，必须严格匹配。|
|Accept 系列|文档、图片、脚本、样式、接口、字体分别对应不同的 Accept 值与 q 因子；不能全局用同一个 Accept。|
|Accept\-Encoding|对齐浏览器支持的压缩算法顺序（Chrome 130: `gzip, deflate, br, zstd`）；禁止只写 gzip。|
|Accept\-Language|与系统语言、IP 地理位置对应；q 值顺序、精度对齐浏览器默认。|
|Cache\-Control|普通请求为 `max-age=0` 或无；强制刷新为 `no-cache`；不能全局 `no-cache`。|
|If\-None\-Match / If\-Modified\-Since|二次访问缓存资源时必须携带；对应服务端返回的 ETag 和 Last\-Modified；模拟浏览器缓存行为。|

### 2\. Cookie 深层细节

- **发送顺序**：Chrome 按「路径长度降序 \> Cookie 名称升序」排列发送顺序；顺序错误是强特征。

- **属性遵守**：严格遵守 Domain、Path、Secure、HttpOnly、SameSite、Partitioned 属性；第三方 Cookie 携带规则对齐浏览器隐私设置。

- **生命周期**：会话 Cookie 关闭会话即清除，持久 Cookie 保留；禁止所有 Cookie 永久有效。

- **获取路径**：必须通过页面访问逐步种植 Cookie，禁止一上来就携带业务 Cookie；风控 Cookie（如 `cf_clearance`）绝对不能伪造。

### 3\. 请求体：字节级格式对齐

- `application/x-www-form-urlencoded`：字段顺序与浏览器一致；空格编码为 `+` 而非 `%20`（表单提交场景）；特殊字符转义规则对齐。

- `application/json`：键的顺序、数字精度、Unicode 转义、换行缩进与浏览器 `JSON.stringify` 输出完全一致；禁止美化格式，禁止自定义键顺序。

- **multipart/form\-data**：边界字符串格式对齐浏览器（如 `----WebKitFormBoundaryXXX`）；每个表单项的头顺序、换行符（CRLF）、空行严格一致；文件项的 Content\-Type、文件名编码、内容顺序与浏览器一致；结束边界必须带 `--` 后缀。

### 4\. 缓存与重定向行为

- **缓存命中模拟**：二次访问静态资源返回 304，携带缓存验证头；不能每次都 200 OK。

- **重定向处理**：301/302/303/307/308 语义区分；307/308 保留请求方法和 body，302/303 转为 GET；重定向时 Referer 更新规则对齐浏览器。

- **状态码处理**：对 401、407、304 等状态码的处理逻辑与浏览器一致，不能所有状态码都当成功处理。

## 五、HTTP/2 \& HTTP/3：帧级指纹深度对齐

### 1\. HTTP/2 帧级特征

curl\_cffi 的 impersonate 已对齐基础特征，但深度检测会校验以下帧级行为：

- **SETTINGS 帧参数**：`SETTINGS_MAX_CONCURRENT_STREAMS`、`SETTINGS_INITIAL_WINDOW_SIZE`、`SETTINGS_MAX_FRAME_SIZE`、`SETTINGS_MAX_HEADER_LIST_SIZE` 的值与发送顺序；Chrome 默认初始窗口为 65535，会立即发送 WINDOW\_UPDATE 扩至 15663105。

- **伪头顺序**：`:method` → `:path` → `:authority` → `:scheme`；顺序不可调换。

- **WINDOW\_UPDATE**：触发阈值、增量大小、发送频率；不同浏览器的流量控制策略不同。

- **HPACK 动态表**：头部压缩的索引策略、动态表更新顺序；自定义 HTTP/2 客户端的 HPACK 行为与浏览器差异极大。

- **PING 帧**：发送间隔、载荷内容；浏览器通常不主动发 PING，服务端 PING 会立即响应。

- **流优先级**：Chrome 会给不同资源设置不同流优先级，核心文档优先级最高；纯接口请求无优先级是特征。

### 2\. HTTP/3 \(QUIC\) 补充

- 若目标站点支持 HTTP/3，真实 Chrome 会优先使用 QUIC；仅支持 HTTP/2 的客户端会暴露特征。

- QUIC 指纹包括：连接 ID 长度、传输参数、帧类型、拥塞控制、ACK 延迟；目前 curl\_cffi 对 HTTP/3 支持有限，极致伪装需配合浏览器或定制 QUIC 客户端。

## 六、浏览器环境指纹：百项 BOM/DOM/渲染/API 全量对齐

curl\_cffi 无法执行 JS，所有 JS 指纹必须由浏览器端生成并全链路同步；以下是商业设备指纹系统的核心检测点，缺失任何一项都可能导致评分异常。

### 1\. 基础系统与 BOM 指纹

- **navigator 全属性**：`hardwareConcurrency`（CPU 核心数）、`deviceMemory`（内存大小）、`platform`（系统平台）、`userAgentData`（高熵 UA）、`maxTouchPoints`（触摸点）、`onLine`、`plugins`、`mimeTypes`、`vendor`、`productSub`、`buildID`、`cookieEnabled`、`doNotTrack` 等，共 40\+ 项。

- **window 与 screen**：`innerWidth/innerHeight`、`outerWidth/outerHeight`、`screenX/screenY`、`devicePixelRatio`、`history.length`、`screen.width/height/availWidth/availHeight`、`colorDepth`、`pixelDepth`、屏幕方向。

- **语言与国际化**：`navigator.language`、`navigator.languages`、`Intl.DateTimeFormat` 输出格式、时区名称拼写、夏令时处理。

- **时间精度**：`performance.now()` 有浏览器级精度模糊（通常 100μs），高精度计时器是爬虫特征；`Date.now()` 的漂移与系统时间对齐。

### 2\. 渲染与硬件指纹

- **Canvas 2D 指纹**：文本渲染、阴影、渐变、抗锯齿、贝塞尔曲线绘制的像素级哈希；受显卡、驱动、系统影响，同一设备稳定唯一。

- **WebGL 指纹**：显卡型号、驱动版本、支持的扩展列表、着色器渲染结果、`getParameter` 返回值；WebGL2 支持情况。

- **WebGPU / WebXR**：新版浏览器支持情况；高端设备与低端设备的支持差异。

- **字体指纹**：不仅是字体列表，还包括字体度量（升部、降部、字宽）、不同字号下的渲染宽度；通常检测 40\+ 种常用字体。

- **音频指纹**：`OfflineAudioContext` 生成的音频波形哈希；受音频栈、采样率、硬件影响。

### 3\. 系统 API 与传感器指纹

- **网络信息 API**：`navigator.connection.effectiveType`、`rtt`、`downlink`；需与代理网络质量对应。

- **电池 API**：`navigator.getBattery()` 返回的电量、充电状态；真实设备电量是动态变化的，固定值易被识别。

- **设备方向/运动**：`DeviceOrientation`、`DeviceMotion` 事件；移动端必检，PC 端通常返回 null。

- **权限 API**：通知、地理位置、摄像头、麦克风的权限状态；默认值对齐浏览器。

- **存储 API**：`localStorage`、`sessionStorage`、`IndexedDB`、`Cache Storage`、`WebSQL` 的支持与容量。

### 4\. 反爬虫探针对抗

- **webdriver 检测**：不仅覆盖 `navigator.webdriver`，还要覆盖 `cdp_*`、`__playwright_*`、`__puppeteer_*` 等全局变量，以及原型链上的特征。

- **函数 toString 检测**：原生函数 `toString()` 返回 `[native code]`，被 Hook 的函数会暴露；需用 Proxy 或原型链重写伪装。

- **原型链污染检测**：检测 `Array.prototype`、`Object.prototype` 是否被修改；很多反检测脚本会污染原型链。

- **错误堆栈检测**：`new Error().stack` 的格式、行号、文件名；无头浏览器与真实浏览器堆栈格式不同。

- **DevTools 反调试**：debugger 断点、时间差检测、元素尺寸检测；需绕过风控的反调试逻辑。

### 5\. Playwright 极致加固示例（核心片段）

```javascript
// 初始化脚本：覆盖百项指纹，保持特征一致性
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 补全plugins与mimeTypes，模拟真实Chrome插件
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
    ]
});

// WebGL指纹伪装：修改显卡信息
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Google Inc. (NVIDIA)';
    if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    return getParameter.apply(this, arguments);
};

// 时区与语言强制对齐
Intl.DateTimeFormat.prototype.resolvedOptions = new Proxy(Intl.DateTimeFormat.prototype.resolvedOptions, {
    apply(target, thisArg, args) {
        const result = Reflect.apply(target, thisArg, args);
        result.timeZone = 'America/Los_Angeles';
        result.locale = 'en-US';
        return result;
    }
});

// 禁用WebRTC，防止IP泄漏
Object.defineProperty(navigator, 'mediaDevices', {
    get: () => ({
        enumerateDevices: () => Promise.resolve([]),
        getUserMedia: () => Promise.reject(new Error('NotAllowedError'))
    })
});
```

## 七、行为与时序指纹：从单请求到会话级的人类行为建模

所有技术特征都对齐后，行为模式是最终的风控门槛。机器学习模型会基于请求序列、时间分布、业务路径做异常评分。

### 1\. 请求级时序行为

- **请求间隔**：不是简单的随机 sleep，而是符合**泊松分布 \+ 突发模式**；用户浏览时会有连续点击（突发）和阅读停留（长间隔）。

- **加载时序**：页面资源加载顺序必须符合浏览器渲染流程：HTML → 首屏 CSS → 核心 JS → 首屏图片 → 次要资源 → 异步接口；不能接口先于页面加载。

- **RTT 波动**：请求耗时有自然波动，完美稳定的耗时是脚本特征；可基于代理延迟加入正态分布抖动。

- **失败重试**：遇到网络错误时，重试间隔符合指数退避 \+ 抖动；重试次数有限，不是无限重试。

### 2\. 会话级行为模型

- **访问路径**：符合马尔可夫转移概率，比如首页→列表页→详情页的跳转概率最高；禁止直接跳深层接口、反向跳转、循环跳转。

- **会话深度**：单会话页面数 3\~15 页，深度 3\~8 层；过深或过浅都异常。

- **会话时长**：单会话时长 5\~30 分钟，符合真实用户浏览时长；过短的高频请求易被识别。

- **资源比例**：一个页面请求中，文档、JS、CSS、图片、接口的比例约为 1:5:3:20:2；全是接口请求是强爬虫特征。

- **异常行为混入**：少量页面刷新、返回上一页、错误点击、中途离开（visibilitychange），模拟真实用户的不完美操作。

### 3\. 浏览器端交互行为（L3/L4 级）

- **鼠标轨迹**：二阶贝塞尔曲线 \+ 加速度 \+ 微抖动；移动速度符合人手操作（峰值 500\~1500 像素/秒）；有过冲、修正、悬停停顿。

- **点击行为**：点击前 100\~300ms 悬停；鼠标按下到释放间隔 50\~200ms；点击坐标有 ±3 像素的偏移，不是精准命中中心。

- **键盘输入**：单字符间隔 50\~150ms；常用字快、生僻字慢；偶尔有删除、修正、光标移动；整段粘贴是高风险行为。

- **滚动行为**：非匀速滚动，有加速度、惯性、回滚；滚动速度随页面内容变化；阅读区滚动慢，翻页时滚动快。

- **注意力模拟**：随机触发 `visibilitychange` 事件，模拟切换标签页、最小化窗口；页面不可见时停止交互。

## 八、主动检测对抗：反调试 / 反虚拟机 / 反爬虫框架

高级风控会主动探测客户端环境，而非被动收集指纹；必须对抗主动探针。

### 1\. 反调试对抗

- 绕过 `debugger` 断点：定时器 debugger、函数 toString debugger、断点事件监听。

- 绕过时间差检测：DevTools 打开会导致代码执行变慢，通过时间差检测；需 Hook 时间函数。

- 绕过元素检测：风控会插入不可见元素，检测 DevTools 元素面板是否修改了尺寸。

- 绕过控制台检测：`console.log` 打印特殊对象，检测控制台是否打开。

### 2\. 虚拟机与沙箱对抗

- **硬件信息检测**：检测 CPU 型号、硬盘序列号、MAC 地址前缀、BIOS 信息，识别 VMware、VirtualBox、Hyper\-V。

- **进程与驱动检测**：检测虚拟机相关进程、驱动、服务。

- **性能检测**：沙箱环境的 CPU、内存、磁盘性能与真实设备有差异；通过计算耗时检测。

- **交互检测**：沙箱通常无真实用户交互，检测鼠标、键盘、触摸事件。

### 3\. 爬虫框架特征清除

- 清除 Playwright/Puppeteer/Selenium 的全局变量、CDP 通道特征、特殊协议头。

- 修复无头模式的特有缺陷：插件缺失、字体缺失、媒体支持异常、权限默认值错误。

- 持久化用户配置：使用真实用户数据目录，避免每次全新无痕窗口；积累浏览历史、书签、缓存，模拟真实用户环境。

## 九、工程化门控架构：指纹池 \+ 会话隔离 \+ 动态调度

单节点伪装再强，工程化不到位依然会被批量识别。工业级方案必须有完整的调度与风控体系。

### 1\. 指纹池管理

- **一一对应原则**：一个设备指纹 ↔ 一个浏览器指纹 ↔ 一个 IP ↔ 一个会话，四者绑定，禁止交叉混用。

- **指纹多样性**：指纹池覆盖不同操作系统、浏览器版本、屏幕分辨率、硬件配置；避免所有请求都是同一套指纹。

- **指纹生命周期**：定期淘汰高风险指纹，补充新指纹；模拟真实用户的设备迭代。

### 2\. 四级门控升级策略（强化版）

|等级|方案组合|触发场景|通过率目标|
|---|---|---|---|
|L1 轻量|纯 curl\_cffi \+ 基础头 \+ 代理轮换|无风控静态站点、公开 API|≥99%|
|L2 标准|curl\_cffi \+ 预生成设备指纹 \+ 全 HTTP 头对齐 \+ Cookie 管理|轻度风控、普通企业站|≥95%|
|L3 增强|浏览器前置拿凭证 \+ curl\_cffi 执行业务 \+ 基础行为模拟|中度风控、Cloudflare 免费版、普通验证码|≥85%|
|L4 极致|全浏览器真实模拟 \+ 完整行为链路 \+ 住宅 ISP 代理 \+ 人工兜底|高强度风控、企业版 WAF、行为验证码|≥70%|

### 3\. 动态调度与熔断

- **成功率监控**：实时统计每个指纹、每个 IP、每个代理节点的成功率、拦截率、风控码分布。

- **自动降级**：单节点连续失败 N 次，自动升级伪装等级、轮换代理、切换指纹。

- **熔断冷却**：高失败率节点进入冷却池，冷却时间随失败次数递增（指数退避）。

- **灰度验证**：新指纹、新代理、新策略先小流量灰度，验证通过率达标后全量上线。

### 4\. 一致性校验引擎

每次请求前执行特征自洽检查，杜绝低级错误：

- UA ↔ Sec\-CH\-UA ↔ TLS 指纹 版本一致。

- IP 地理位置 ↔ 时区 ↔ 语言 一致。

- 屏幕分辨率 ↔ 视口大小 ↔ devicePixelRatio 一致。

- Cookie 来源 ↔ 域名 匹配。

- 请求头顺序 ↔ 目标浏览器 一致。

## 十、风控对抗方法论：调试、验证、迭代的专业流程

### 1\. 调试工具链

- **传输层验证**：Wireshark 抓包分析 TCP、TLS、HTTP/2 帧级特征，与真实浏览器对比。

- **指纹验证站点**：`browserleaks.com`、`pixelscan.net`、`amiunique.org`、`f.vision`、`tls.peet.ws`（JA3 检测），逐项验证指纹一致性。

- **风控探针抓取**：用 Fiddler/Charles 拦截风控 JS 脚本，逆向分析检测点，针对性绕过。

- **A/B 测试**：单一变量原则，每次只改一个参数，定位具体被拦截的特征点。

### 2\. 迭代优化流程

1. **基线测试**：用真实浏览器访问，记录所有特征与通过率，作为基准线。

2. **特征对齐**：从底层到上层逐层对齐，每对齐一层验证一次通过率。

3. **批量压测**：小批量连续请求，观察是否触发频率风控、行为风控。

4. **特征分析**：被拦截后抓包对比，找出与真实浏览器的差异点。

5. **持续对抗**：风控规则会更新，需持续监控通过率，迭代伪装策略。

### 3\. 核心原则总结

- **自洽性优先**：宁可用低版本但全链路一致的指纹，也不用高版本但特征矛盾的指纹。

- **最小修改原则**：能不改的就不改，任何自定义都会引入特征；优先使用官方维护的指纹。

- **模拟而非伪造**：行为模拟要符合人类统计规律，不是越像越好，而是符合分布。

- **合规底线**：所有技术手段必须在法律与授权范围内使用，技术越强责任越大。

---

# 第三篇 华为云 WAF 专项对抗方案：四层检测全维度绕过与伪装强化

华为云 WAF 的反爬体系和 Cloudflare、Akamai 有显著差异，核心是**四层递进式 BOT 检测架构**，搭配 JA4 新一代 TLS 指纹、JS 挑战、动态 CC 防护三大能力，检测逻辑从静态指纹到动态行为逐步收敛。单纯的 curl\_cffi TLS 伪装只能过第一层基础检测，必须针对其特有检测逻辑做全链路定制化适配。

## 一、华为云 WAF 核心检测体系深度拆解

### 1\. BOT 管理：四层递进式检测（反爬核心）

这是华为云 WAF 高阶反爬的核心模块，按优先级逐层筛除恶意流量，越靠后检测强度越高：

1. **已知 BOT 检测（静态指纹库匹配）**

    - 依托官方维护的 UA 特征库，覆盖搜索引擎、扫描器、爬虫框架、监控工具等 10 大类 BOT 指纹，匹配即直接处置，特征库云端实时更新。

    - 同时识别主流 HTTP 客户端库（requests、urllib、默认 curl 等）的协议特征、特有请求头。

2. **请求特征检测（自定义静态规则）**

    - 基于 HTTP 全字段特征做组合匹配，包括请求头顺序、特殊标识头、Cookie 格式、请求体编码、URL 访问规律。

    - 支持用户自定义 30\+ 条件字段组合，可针对业务特有爬虫特征定向拦截。

3. **BOT 行为检测（AI 行为评分）**

    - 基于会话级请求序列做 AI 建模评分（0\~100 分），分值越高判定为 BOT 概率越大。

    - 检测维度：访问频率、路径跳转规律、静态资源比例、失败重试模式、请求间隔分布等，开启后引入少量时延。

4. **主动特征检测（人机交互校验）**

    - 向 HTML 页面注入风控 JS 脚本，主动采集浏览器环境指纹、鼠标/键盘/滚动交互行为，实时判断是否为真人操作。

    - 仅适用于网页场景，纯 API/接口场景不会触发该层检测。

### 2\. TLS 指纹：JA3 \+ JA4 双维度校验

华为云 WAF 是国内少数支持 **JA4 新一代 TLS 指纹**的商用 WAF，抗伪装能力远强于传统 JA3，也是绝大多数爬虫方案的漏检点：

- **JA3**：适配 TLS 1\.2 及以下，基于 ClientHello 的密码套件、扩展、椭圆曲线生成指纹，生态成熟但易被模拟。

- **JA4**：针对 TLS 1\.3 优化，新增密钥共享、扩展排列、QUIC/HTTP 版本等特征，抗随机化、抗伪装能力更强，识别精度更高。

- **触发条件**：独享模式 \+ 前端部署七层 ELB 时，ELB 会通过 `X-Forwarded-Tls-Ja3`/`X-Forwarded-Tls-Ja4` 头将客户端原始 TLS 指纹透传给 WAF，结合精准访问规则实现拦截/人机验证。

- 云模式下默认不开启强拦截，但会做特征记录，高频异常指纹会触发风控升级。

### 3\. JS 脚本反爬虫（JS 挑战机制）

核心是「能力检测 \+ 合法性校验」双阶段，纯 HTTP 客户端无法通过：

1. **JS 挑战阶段**：客户端首次请求时，WAF 返回一段混淆 JavaScript 代码。

2. **JS 验证阶段**：真实浏览器可自动执行 JS 并生成校验 Token，携带 Token 二次请求，WAF 校验通过后放行。

3. **Token 绑定机制**：生成的 Token 与客户端指纹、IP、会话绑定，伪造 Token 会直接被拦截并标记高风险。

4. **时效规则**：Token 有效期默认 30 分钟，可自定义；异步 Ajax 接口无法独立触发挑战，必须依赖父页面完成校验。

### 4\. CC 防护 \+ 动态阻断

- 支持 **IP、Cookie、Referer** 三个维度限速，超过阈值可触发阻断、人机验证、动态阻断三种动作。

- **动态阻断**：触发限速后，下一个周期会进一步降低放行频率，阶梯式加严，对抗脉冲式爬虫。

- **人机验证**：超过限速后弹出图形验证码，仅支持英文静态页面，不支持异步接口场景。

### 5\. 精准访问防护（多条件组合规则）

支持 IP、URL、请求头、Cookie、参数、TLS 指纹等 30\+ 条件字段自由组合，常见高危组合规则：

- 数据中心 IP \+ 非浏览器 TLS 指纹 → 拦截。

- 无 Referer 直接访问业务接口 → 人机验证。

- 异常 UA \+ 高频请求 → 永久封禁。

## 二、针对性全链路对抗方案：逐层击穿四层检测

### 1\. 传输层：JA3 \+ JA4 双指纹字节级对齐

华为云 JA4 检测是绝大多数方案的盲区，仅对齐 JA3 无法通过高配场景，必须做到 TLS 1\.3 全握手特征完全对齐。

**（1）核心对齐原则**

- **JA4 优先级高于 JA3**：模拟 Chrome 110\+ 版本必须启用 TLS 1\.3，对应 JA4 指纹；禁止手动修改密码套件、扩展列表，任何自定义都会破坏指纹一致性。

- **指纹与浏览器版本强绑定**：优先使用 curl\_cffi 官方维护的最新稳定版 Chrome 指纹（如 chrome131），官方指纹会同步更新 JA4 特征，自定义指纹大概率不兼容 JA4 校验。

- **会话复用对齐**：真实浏览器会复用 TLS 会话，连续请求握手耗时会显著降低；每次都完整握手是强爬虫特征。

**（2）curl\_cffi 华为云专属优化配置**

```python
from curl_cffi import requests
from curl_cffi.const import CurlOpt

def create_huawei_waf_session(
    impersonate_target: str = "chrome131",  # 优先最新稳定版，JA4特征最完整
    proxy: str = None,
    force_tls13: bool = True
) -> requests.Session:
    session = requests.Session(
        impersonate=impersonate_target,
        timeout=(7, 35),  # 对齐国内网络延迟，避免握手过快暴露特征
        allow_redirects=True,
        max_redirects=6,
        verify=True,      # 必须开启证书校验，关闭是顶级爬虫特征
        http2=True,
        decompress=True,  # 保持浏览器默认解压行为
    )

    # 强制TLS版本与指纹对应，避免自动协商导致指纹偏移
    if force_tls13:
        session.curl.setopt(CurlOpt.SSLVERSION, CurlOpt.SSLVERSION_TLSv1_3)
    else:
        session.curl.setopt(CurlOpt.SSLVERSION, CurlOpt.SSLVERSION_TLSv1_2)

    # 代理优先SOCKS5，减少HTTP代理的头注入特征
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    # TLS会话复用：对齐浏览器会话复用行为
    session.curl.setopt(CurlOpt.SSL_SESSIONID_CACHE, 1)
    session.curl.setopt(CurlOpt.SSL_SESSION_REUSE, 1)

    # 禁用curl自带标识，禁止手动覆盖UA，完全由impersonate控制
    session.curl.setopt(CurlOpt.USERAGENT, "")

    return session
```

**（3）指纹验证方法**

1. 用真实目标浏览器访问 TLS 指纹检测站点，记录 JA3/JA4 哈希值。

2. 用配置好的 curl\_cffi 客户端请求同一站点。

3. 对比两组哈希完全一致，才算传输层对齐通过。

### 2\. HTTP 协议层：绕过已知 BOT 检测 \+ 请求特征检测

这是基础门槛，核心是**完全复刻真实浏览器的请求特征，消除所有自动化工具标识**。

**（1）请求头零特征化**

- **绝对禁止爬虫标识 UA**：`python-requests/xxx`、`curl/xxx`、`python-urllib/xxx` 均在华为云 BOT 特征库中，匹配即拦截。

- **Sec\-CH\-UA 严格对应**：UA 版本、品牌、GREASE 值、引号格式、顺序必须与对应 Chrome 版本完全一致；华为云会校验二者的匹配关系。

- **头顺序严格复刻**：按浏览器 F12 中从上到下的物理顺序逐行复刻；手动添加的头必须插入到对应位置，不能追加到末尾。

- **按需出现原则**：`Origin` 仅跨域请求携带，同域表单 POST 不携带；`Referer` 遵循 Referrer\-Policy 规则，不是所有请求都带。

**（2）Cookie 与 Token 处理红线**

- **绝对禁止伪造 JS 挑战 Token**：华为云 Token 与客户端指纹、IP、会话强绑定，伪造会直接触发拦截并标记高风险客户端。

- **正确获取流程**：必须先通过浏览器环境访问页面，完整执行 JS 挑战，拿到校验 Cookie 后，再同步给 curl\_cffi 使用。

- **生命周期管理**：严格遵守 Token 有效期（默认 30 分钟），到期前自动重新获取；Token 失效的典型表现：返回 302 跳转到验证页、响应体为混淆 JS 代码。

**（3）请求体与重定向细节**

- `application/x-www-form-urlencoded` 提交时，字段顺序、空格编码（`+` 而非 `%20`）与浏览器一致。

- JSON 请求的键顺序、数字精度、Unicode 转义与浏览器 `JSON.stringify` 输出完全一致。

- 重定向时自动更新 Referer，遵循浏览器跳转逻辑；禁止直接访问跳转后的目标地址。

- 配套加载核心静态资源（主 JS、主 CSS），避免只刷业务接口，提升请求特征真实性。

### 3\. 主动 JS 检测对抗：浏览器环境 \+ 行为双伪装

针对华为云 BOT 管理第四层「主动特征检测」，纯 curl\_cffi 完全无法对抗，必须通过真实浏览器环境处理。

**（1）浏览器环境指纹加固**

华为云注入的 JS 会采集上百项环境特征，核心加固点：

- **基础 BOM 全对齐**：navigator 全属性（CPU 核心数、内存、平台、插件）、screen 信息、时区、语言，必须与模拟设备、IP 地理位置完全对应。

- **渲染指纹稳定化**：Canvas、WebGL、字体指纹固定且符合真实设备特征，禁止使用通用随机指纹；同一设备指纹必须全程一致。

- **反探针加固**：覆盖 `navigator.webdriver`、补全 plugins/mimeTypes、修复函数 `toString`、清理无头浏览器错误堆栈特征；清除 Playwright/Puppeteer 全局注入变量。

- **WebRTC 封堵**：禁用非代理路径的 ICE 候选，确保 WebRTC 返回的 IP 与代理 IP 一致，防止真实 IP 泄漏。

**（2）交互行为模拟（对抗主动行为采集）**

华为云主动 JS 会实时采集键鼠交互数据，通过算法判断是否为真人：

- **鼠标轨迹**：二阶贝塞尔曲线 \+ 人类加速度模型，峰值速度 800\~1500px/s，包含微抖动、过冲修正、悬停停顿。

- **点击行为**：点击前 100\~300ms 悬停，按下到释放间隔 50\~200ms，点击坐标有 ±2\~5px 偏移，不精准命中元素中心。

- **滚动行为**：非匀速滚动，有停顿、回滚；阅读区域滚动慢，翻页区域滚动快；滚动距离与页面内容长度匹配。

- **注意力模拟**：随机触发标签页切换、窗口失焦，对应 `visibilitychange` 事件，避免全程页面聚焦。

**（3）JS 挑战自动通关标准流程**

1. 浏览器加载目标页面，触发 WAF 返回 JS 挑战代码。

2. 等待浏览器自动执行 JS，生成校验 Token 并写入 Cookie。

3. 检测页面加载完成、无验证提示、Cookie 中出现 WAF 校验字段。

4. 提取全量 Cookie 与标准请求头，同步给 curl\_cffi 客户端。

5. Token 有效期内，curl\_cffi 直接携带凭证发起业务请求。

### 4\. 行为层：绕过 AI 行为评分 \+ CC 防护

华为云 BOT 行为检测基于会话级 AI 评分，CC 防护基于多维度频率阈值，核心是模拟真实用户的统计分布。

**（1）AI 行为评分规避**

- **访问路径符合业务逻辑**：遵循「首页→列表页→详情页→业务接口」的跳转路径，禁止直接访问深层接口；路径深度控制在 3\~8 层。

- **请求间隔符合人类分布**：采用泊松分布 \+ 正态波动，列表页停留 1\~3s，详情页停留 5\~15s，禁止固定间隔。

- **资源比例真实**：单页面请求中，文档、JS、CSS、图片、接口的比例约为 1:4:2:15:3，纯接口请求会大幅提升风险评分。

- **会话特征稳定**：单会话内 IP、指纹、Cookie 一致，会话时长 5\~40 分钟，单会话页面数 3\~20 页，避免超短会话高频请求。

- **异常行为混入**：少量返回上一页、页面刷新、错误点击、重复请求，模拟真实用户的不完美操作。

**（2）CC 防护与动态阻断规避**

- **多维度限速**：不仅控制单 IP 频率，还要控制单 Cookie、单 Referer 的访问频率，华为云支持三个维度独立限速。

- **留足频率余量**：不要卡着阈值请求，保留 30% 以上余量；触发一次动态阻断后，下一个周期放行频率会阶梯式下降。

- **触发后处置**：返回 403/验证码页面后，立即停止该 IP/会话请求，冷却 10\~30 分钟重试；持续请求会被加严封禁。

- **并发控制**：单 IP 并发数不超过 3，单域名并发连接数对齐浏览器默认（Chrome 默认 6 个），高并发会直接触发 CC 防护。

### 5\. 网络层：IP 与链路特征对齐

- **IP 类型优先级**：住宅 ISP 代理 \> 纯住宅代理 \> 移动网络代理；避免数据中心 IP，华为云精准规则常配置「数据中心 IP \+ 非浏览器特征 → 拦截」。

- **三要素一致性**：IP 归属地 → 系统时区 → Accept\-Language 语言必须完全匹配，例如广东 IP 对应 `Asia/Shanghai` 时区、`zh-CN` 语言。

- **代理类型选择**：SOCKS5 代理 \> HTTP 高匿代理，避免透明代理注入 `X-Forwarded-For` 等暴露头；禁止多层代理叠加。

- **DNS 一致性**：使用代理节点本地 DNS，禁止使用公共 DNS，避免 DNS 地理位置与 IP 地理位置不符。

## 三、适配华为云 WAF 的智能门控架构

基于华为云四层检测的递进特性，定制四级门控策略，按需分配伪装强度，兼顾性能与通过率。

### 1\. 四级伪装等级与适用场景

|等级|方案组合|适配华为云防护场景|相对性能|预期通过率|
|---|---|---|---|---|
|L1 轻量|纯 curl\_cffi \+ 标准浏览器指纹 \+ 代理轮换|未开启 BOT 管理，仅基础 Web 防护、简单 CC 规则|100%|≥98%|
|L2 标准|curl\_cffi \+ 预获取 JS 挑战 Token \+ 全 HTTP 头对齐 \+ 频率控制|开启 JS 脚本反爬虫、基础 CC 防护、已知 BOT 检测|75%|≥90%|
|L3 增强|浏览器前置通关 JS 挑战 \+ curl\_cffi 执行业务 \+ 基础行为模拟|开启 BOT 管理前三层、中等强度 CC、精准访问规则|40%|≥80%|
|L4 极致|全浏览器真实模拟 \+ 完整交互行为 \+ 住宅 ISP 代理 \+ 动态指纹池|开启 BOT 管理全四层（主动行为检测）、高强度 CC、TLS 指纹拦截|15%|≥70%|

### 2\. 风控触发判定（华为云专属特征）

通过响应特征精准判断触发了哪一层防护，对应升级伪装等级：

- **已知 BOT/特征拦截**：直接返回 403，响应体包含“访问被拒绝”“WAF”等字样，或直接重置连接。

- **JS 挑战触发**：响应体为混淆 JS 代码，无正常页面内容，或 302 跳转到验证页面。

- **行为/CC 触发**：返回 403、429 状态码，或弹出图形验证码页面。

- **主动检测触发**：页面加载后二次跳转验证，Cookie 有效期极短，频繁弹出人机验证。

### 3\. 降级与熔断策略

- 单会话连续 2 次触发 JS 挑战 → 升级到 L3 等级，用浏览器重新获取凭证。

- 单 IP 连续 3 次返回 403/429 → 轮换 IP，冷却 15 分钟。

- 单指纹连续 5 次被拦截 → 切换浏览器指纹版本。

- 批量任务整体触发风控 → 全局从 L2 升级到 L3，降低并发与请求频率。

### 4\. 指纹池与会话管理

- **一一绑定原则**：一个浏览器指纹 ↔ 一个 IP ↔ 一个会话 Cookie ↔ 一个 JS Token，四者绑定，禁止交叉混用。

- **指纹多样性**：覆盖不同 Chrome 版本、屏幕分辨率、系统版本，避免所有请求指纹高度同质化。

- **会话复用**：Token 有效期内复用同一会话，减少 JS 挑战次数，降低被检测概率。

## 四、工程化实现：华为云 WAF 专项代码框架

### 1\. 浏览器凭证提供者（自动通关 JS 挑战）

```python
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

class HuaweiWAFCredentialProvider:
    def __init__(self, proxy: str = None, headless: bool = True):
        self.proxy = proxy
        self.headless = headless
        self.chrome_ver = "131.0.6778.86"
        self.ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.chrome_ver} Safari/537.36"

    def get_valid_credentials(self, target_url: str) -> tuple[list, dict]:
        """获取通过华为云WAF JS挑战的Cookie和标准请求头"""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                proxy={"server": self.proxy} if self.proxy else None,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ]
            )

            context = browser.new_context(
                user_agent=self.ua,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
            )

            # 注入反检测脚本
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                    ]
                });
                const _toString = Function.prototype.toString;
                Function.prototype.toString = function() {
                    const res = _toString.call(this);
                    return res.includes('[native code]') ? res : `function ${this.name}() { [native code] }`;
                };
            """)

            page = context.new_page()

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                # 等待JS挑战执行完成
                page.wait_for_function(
                    "() => !document.body.innerText.includes('验证') && document.readyState === 'complete'",
                    timeout=10000
                )
                page.wait_for_timeout(2000)

                cookies = context.cookies()
                # 捕获真实请求头
                request_headers = {}
                def capture(req):
                    nonlocal request_headers
                    if req.url == target_url and req.method == "GET":
                        request_headers = req.headers
                page.on("request", capture)
                page.reload(wait_until="networkidle")

                browser.close()
                return cookies, request_headers

            except PlaywrightTimeoutError:
                browser.close()
                raise RuntimeError("华为云WAF JS挑战超时，验证失败")
```

### 2\. 门控调度核心逻辑

```python
class HuaweiWAFGateKeeper:
    def __init__(self):
        self.level = 1
        self.fail_count = 0
        self.fail_threshold = 2  # 华为云风控较严，阈值调低

    def is_blocked(self, response) -> bool:
        """判断是否被华为云WAF拦截"""
        if response.status_code in (403, 429, 503):
            return True
        if "challenge" in response.text.lower() or "验证" in response.text:
            return True
        return False

    def upgrade_level(self):
        if self.level < 4:
            self.level += 1
            self.fail_count = 0

    def record_fail(self):
        self.fail_count += 1
        if self.fail_count >= self.fail_threshold:
            self.upgrade_level()
```

## 五、调试与验证方法论

### 1\. 分层验证流程

1. **传输层验证**：请求 TLS 指纹检测站点，确认 JA3/JA4 哈希与真实浏览器完全一致。

2. **协议层验证**：用抓包工具对比真实浏览器与脚本的请求头顺序、值、Cookie 格式。

3. **JS 挑战验证**：单独测试浏览器通关成功率，确认 Token 有效期与绑定规则。

4. **行为层验证**：小批量连续请求，观察是否触发 CC 防护与行为评分拦截。

5. **全链路压测**：模拟业务流量，统计通过率、拦截类型分布，针对性优化。

### 2\. 常见拦截原因排查

- **直接 403**：大概率 UA 命中 BOT 库、TLS 指纹异常、IP 在黑名单，优先换指纹和 IP。

- **返回 JS 代码不跳转**：JS 挑战执行失败，检查浏览器环境是否有异常特征、JS 执行是否报错。

- **前几次正常，后续频繁拦截**：触发行为 AI 评分或 CC 防护，降低请求频率、优化访问路径。

- **频繁弹出验证码**：触发主动行为检测，升级到 L4 等级，加强交互行为模拟。

---

# 第四篇 华为云 WAF JS 挑战校验算法全量逆向与工程化对抗

华为云 WAF 的 JS 挑战体系经历了两代架构演进：早期轻量版 `wzws` 体系逻辑简单可完全逆向；当前主流的 `HWWAF` 全栈体系结合了 BOT 管理、TLS 指纹、环境指纹、行为校验，是多层校验的复合体系。本篇从底层算法、校验流程、对抗边界三个维度完整拆解。

## 一、两代 JS 挑战架构演进

### 1\. 旧版轻量体系：wzws 挑战（2017\-2020）

这是华为云最早的 JS 反爬虫方案，逻辑完全透明，可通过纯算法模拟 100% 绕过，也是逆向学习的入门样本。

- **核心 Cookie 字段**：`wzwsconfirm`、`wzwsvtime`、`wzwstemplate`、`wzwschallenge`、`ccpassport`。

- **校验逻辑**：纯数学运算 \+ Cookie 校验，无环境指纹采集，无 JS 引擎检测。

- **局限性**：仅能拦截无法执行 JS 的纯 HTTP 客户端，极易被算法模拟绕过。

### 2\. 新版全栈体系：HWWAF 复合挑战（当前主流）

结合 BOT 管理四层检测架构，从「纯运算校验」升级为「环境 \+ 运算 \+ 时序 \+ 指纹」四维复合校验，也是当前绝大多数站点使用的版本：

- **核心 Cookie 字段**：`HWWAFSESID`（会话 ID）、`HWWAFSESTIME`（会话时间戳）、认证 Token Cookie（动态命名）。

- **校验维度**：TLS 指纹一致性 \+ JS 运算正确性 \+ 环境指纹合法性 \+ 执行时序合理性 \+ 会话绑定性。

- **防护分级**：基础版仅运算校验，高级版（BOT 主动检测）叠加全量环境指纹与行为采集。

## 二、新版 JS 挑战完整校验算法全链路拆解

整个流程分为**挑战下发、客户端计算、服务端校验、Token 发放**四个阶段，每个阶段都有对应的加密与校验逻辑。

### 阶段 1：挑战下发 —— 服务端生成挑战因子

客户端首次请求命中规则时，WAF 不转发源站，而是返回一段高度混淆的 JavaScript 代码，同时在响应头/响应体中注入挑战因子。

**（1）下发的核心挑战参数**

|参数|载体|作用|
|---|---|---|
|`challenge_seed`|JS 内联变量|随机挑战种子，每次请求唯一，是核心运算输入|
|`salt`|JS 内联变量|哈希盐值，周期性更新，防止固定算法伪造|
|`session_id`|Set\-Cookie: HWWAFSESID|会话唯一标识，绑定本次挑战与后续 Token|
|`timestamp`|JS 内联变量 / Cookie|服务端时间戳，用于时效性校验|
|`algorithm_flag`|JS 内联变量|算法标识，控制本次使用的哈希/加密组合，动态轮换|
|`difficulty`|JS 内联变量|运算难度系数，风控等级越高，计算量越大|

**（2）代码混淆机制**

- 采用**控制流平坦化 \+ 字符串加密 \+ 变量名混淆**三重混淆，核心函数名、变量名全部随机生成。

- 内置**反调试探针**：`debugger` 定时触发、`toString` 检测、控制台打开检测，阻止静态分析与动态调试。

- 代码分片加载：核心运算逻辑拆分到多个 JS 片段，按需执行，增加逆向成本。

### 阶段 2：客户端执行 —— 环境采集 \+ 挑战运算 \+ 结果提交

浏览器加载 JS 后，自动执行完整的采集与计算流程，最终将结果写入 Cookie 并触发二次请求。这是逆向的核心目标。

**第一步：浏览器环境指纹采集（高级版开启）**

先采集上百项环境特征，生成环境指纹摘要，作为挑战运算的输入因子之一。**纯算法模拟无法生成合法的环境指纹**，这也是新版方案对抗纯脚本的核心。采集项分为四级：

1. **基础 BOM 特征**：`navigator.userAgent`、平台、语言、时区、屏幕尺寸、CPU 核心数、设备内存。

2. **渲染能力特征**：Canvas 2D 绘图哈希、WebGL 显卡信息、字体列表与渲染度量、音频指纹。

3. **JS 引擎特征**：原生函数 `toString` 检测、原型链完整性、错误堆栈格式、ES6\+ API 支持度。

4. **反自动化特征**：`webdriver` 检测、插件列表检测、自动化框架全局变量检测、CDP 通道检测。

> 核心逻辑：所有采集项拼接后，通过 SHA\-256 哈希生成固定长度的**环境指纹摘要（fp\_hash）**，作为运算输入的一部分。服务端会校验该摘要是否在真实浏览器指纹分布内。
> 
> 

**第二步：核心挑战运算逻辑**

这是 JS 挑战的数学核心，输入参数固定，输出结果唯一，服务端可复现校验。

**运算输入因子（共 5 项）**：

1. 服务端下发的随机种子 `challenge_seed`。

2. 环境指纹摘要 `fp_hash`（基础版为空字符串）。

3. 客户端时间戳 `client_ts`（需与服务端时间差在合理范围）。

4. 盐值 `salt`。

5. 难度系数 `difficulty`。

**标准运算流程（以默认算法为例）**：

```text
1. 拼接原始字符串：raw = challenge_seed + "|" + fp_hash + "|" + client_ts + "|" + difficulty
2. 第一轮加盐哈希：hash1 = SHA256(raw + salt)
3. 难度运算：根据difficulty对hash1进行N次迭代哈希（Proof of Work逻辑，风控越高N越大）
4. 第二轮签名：result = HMAC-SHA256(hash1, salt)
5. 最终提交值：answer = 结果前16位十六进制字符串
```

> 算法轮换机制：WAF 会周期性切换哈希算法（SHA1/SHA256/SHA512/MD5）、迭代次数、拼接顺序，对应 `algorithm_flag` 标识。固定算法模拟会在规则更新后立即失效。
> 
> 

**第三步：结果提交与二次请求**

计算完成后，JS 会做两件事：

1. 将运算结果、环境指纹摘要、客户端时间戳，写入指定 Cookie（名称动态生成，非固定）。

2. 通过 `location.reload()` 或 `location.href` 触发页面二次刷新，携带 Cookie 发起验证请求。

> 关键细节：二次请求必须与首次请求的**TLS 指纹、IP、UA、请求头顺序完全一致**，否则即使运算结果正确，也会因特征不一致被拦截。
> 
> 

### 阶段 3：服务端校验 —— 五维度复合校验

WAF 收到二次请求后，执行五层校验，全部通过才会发放 Token，任意一层失败均触发拦截。

1. **时效性校验**

    - 校验 `HWWAFSESTIME` 与服务端时间差，默认允许 ±300 秒偏差。

    - 校验挑战种子的有效期（通常 60 秒），过期种子直接作废。

    - 防止重放攻击：每个挑战种子只能使用一次，重复提交直接拦截。

2. **会话绑定校验**

    - `HWWAFSESID` 必须与首次挑战请求对应，跨会话复用挑战结果直接失败。

    - Token 与 IP、TLS 指纹（JA3/JA4）强绑定，更换 IP 或客户端指纹后 Token 自动失效。

3. **运算结果校验**

    - 服务端使用相同的种子、盐值、算法，重新执行完整运算流程。

    - 将服务端计算结果与客户端提交结果做精确比对，不一致则校验失败。

    - 这是最基础的校验，也是纯算法模拟唯一能通过的一层。

4. **环境指纹校验**

    - 校验环境指纹摘要是否符合真实浏览器统计分布。

    - 校验指纹是否在高风险黑名单（如无头浏览器、自动化工具指纹）。

    - 校验指纹与 UA、TLS 指纹的自洽性：例如 UA 是 Chrome 但 WebGL 指纹是 Firefox，直接判定异常。

5. **时序合理性校验**

    - 校验 JS 执行耗时：从挑战下发到二次请求的时间差，正常浏览器需 200\~2000ms。

    - 耗时过短（\<50ms）判定为脚本伪造，耗时过长（\>10s）判定为异常。

    - 纯算法模拟可在几毫秒内算出结果，时序特征极易暴露。

### 阶段 4：Token 发放与会话管理

全部校验通过后，WAF 向客户端下发认证 Token Cookie，后续请求携带该 Cookie 即可免挑战放行。

- **Token 属性**：默认有效期 30 分钟，支持自定义 60 秒\~24 小时；HttpOnly 标记，防止 JS 读取。

- **绑定维度**：Token = HMAC\(会话 ID \+ IP \+ TLS 指纹摘要 \+ 环境指纹摘要 \+ 过期时间, 服务端私钥\)。

- **动态续期**：Token 有效期内持续访问会自动续期；触发高风险行为时，Token 会提前作废并重新触发挑战。

## 三、旧版 wzws 体系完整逆向算法（可复现）

旧版方案逻辑简单，是理解 JS 挑战原理的最佳样本，以下为完整可复现的算法逻辑。

### 1\. 挑战响应结构

首次请求返回的 JS 中，包含三个核心动态变量，其余代码固定：

```javascript
var wzwschallenge = "随机字符串A";
var wzwschallengex = "随机字符串B";
var template = "动态模板字符串";
var dynamicurl = "/目标路径的base64编码";
```

### 2\. 客户端核心计算逻辑

```javascript
// 1. 生成wzwstemplate cookie：对template做简单编码运算
function genTemplate(tpl) {
    var res = "";
    for (var i = 0; i < tpl.length; i++) {
        res += String.fromCharCode(tpl.charCodeAt(i) ^ 0x5A);
    }
    return btoa(res);
}

// 2. 生成confirm值：两个挑战字符串拼接后取MD5
function genConfirm(challenge, challengex) {
    return md5(challenge + challengex);
}

// 3. 生成wzwschallenge cookie：对confirm做二次编码
function genChallengeCookie(confirm) {
    return btoa(confirm.split('').reverse().join(''));
}

// 4. 设置cookie后跳转到dynamicurl解码后的路径，发起验证请求
```

### 3\. 服务端校验逻辑

1. 解码客户端提交的 `wzwstemplate`，与下发的 template 对比。

2. 重新计算 `wzwschallenge` 的预期值，与客户端提交对比。

3. 校验通过则下发 `ccpassport` Cookie 作为通行凭证。

4. 校验失败则返回 403 或重新下发挑战。

### 4\. Python 纯算法实现（旧版）

```python
import base64
import hashlib

def wzws_solve(challenge: str, challengex: str, template: str) -> dict:
    """
    纯算法计算华为云旧版wzws JS挑战结果
    :param challenge: wzwschallenge 值
    :param challengex: wzwschallengex 值
    :param template: template 值
    :return: 计算完成的cookie字典
    """
    # 计算 wzwstemplate
    tpl_raw = ''.join(chr(ord(c) ^ 0x5A) for c in template)
    wzwstemplate = base64.b64encode(tpl_raw.encode()).decode()

    # 计算 confirm (MD5)
    confirm = hashlib.md5((challenge + challengex).encode()).hexdigest()

    # 计算 wzwschallenge
    wzwschallenge = base64.b64encode(confirm[::-1].encode()).decode()

    return {
        "wzwstemplate": wzwstemplate,
        "wzwschallenge": wzwschallenge
    }
```

> 注：该算法仅适用于早期旧版 WAF，新版 HWWAF 体系已完全废弃该逻辑，无法通过纯算法模拟通过。
> 
> 

## 四、新版挑战的逆向调试方法论

新版 JS 混淆程度高，且有反调试机制，需要系统化的逆向流程才能提取核心算法。

### 1\. 环境准备

- 浏览器：Chrome DevTools \+ 禁用断点插件 \+ 内存快照工具。

- 抓包工具：Fiddler / Charles，替换混淆 JS 为本地解混淆版本。

- 解混淆工具：AST 解混淆器（如 js\-deobfuscator）、控制流平坦化还原工具。

### 2\. 逆向步骤

1. **绕过反调试**：置空 `debugger` 语句，Hook `Function.prototype.toString`；覆盖 `console` 对象，绕过控制台检测；使用浏览器无头模式 \+ CDP 协议，绕过页面级反调试。

2. **定位核心运算函数**：搜索 Cookie 写入点（`document.cookie`），向上回溯调用链；下断点监听 `location.reload`，触发时查看调用栈；搜索哈希算法特征常量（如 SHA256 的魔数），定位加密函数。

3. **提取算法逻辑**：扣出核心运算函数，补全依赖的工具函数；输入固定参数，对比浏览器执行结果与本地执行结果，验证一致性；识别盐值、算法标识、难度系数的获取方式。

4. **验证与还原**：用真实挑战种子测试本地算法，确认计算结果与浏览器提交结果一致；测试不同难度、不同算法标识下的输出，覆盖全部分支。

### 3\. 核心难点与对抗点

- **算法动态轮换**：WAF 会不定期更新哈希算法、拼接顺序、盐值，逆向成果有保质期。

- **环境指纹强绑定**：即使算法完全逆向，没有合法的环境指纹，依然无法通过高级版校验。

- **服务端黑盒**：无法得知服务端的指纹校验规则，只能通过黑盒测试反推。

## 五、纯算法模拟的边界与工程化对抗方案

### 1\. 不同防护等级的绕过可行性

|防护等级|纯算法模拟|curl\_cffi \+ 预生成指纹|浏览器真实执行|
|---|---|---|---|
|基础 JS 挑战（仅运算校验）|可完全绕过|可|可|
|开启 TLS 指纹校验|缺少 TLS 指纹对齐|可|可|
|开启基础环境指纹校验|无法生成合法指纹|需稳定指纹池|可|
|开启 BOT 主动行为检测|完全无法通过|缺少行为数据|可|
|企业版全量防护|不可行|不可行|需高强度加固|

### 2\. 工程化最优方案：浏览器算题 \+ curl\_cffi 跑量

兼顾通过率与性能的工业级方案，也是对抗新版华为云 WAF 的标准架构：

1. **Worker 池**：Playwright 加固浏览器节点，负责触发挑战、执行 JS、获取合法 Token。

2. **凭证同步**：将 Token、Cookie、对应指纹、IP 绑定，同步给 curl\_cffi 客户端池。

3. **批量执行**：curl\_cffi 以完全一致的 TLS 指纹 \+ 请求头 \+ Token，执行业务批量请求。

4. **自动续命**：Token 过期或触发风控时，自动回退到浏览器节点重新获取。

### 3\. 算法模拟的适用场景

- 目标站点仅开启基础 JS 反爬虫，未开启 BOT 管理。

- 大规模轻量数据采集，性能优先级高于通过率。

- 作为多级门控的 L2 等级，降低浏览器资源消耗。

## 六、对抗升级：WAF 侧的反绕过机制

华为云 WAF 针对算法模拟与浏览器自动化，有持续的对抗升级，逆向时需要重点关注：

1. **算法每日动态更新**：盐值、拼接顺序、哈希组合每日轮换，固定算法快速失效。

2. **指纹库实时更新**：自动化工具、常见反检测脚本的指纹，会被快速加入黑名单。

3. **异常行为聚类**：同一算法生成的大量指纹会有统计特征，聚类后批量封禁。

4. **主动探针注入**：不定期注入特殊检测逻辑，识别脚本化的执行模式。

---

**结语**：以上内容为技术学习与授权测试研究整理。实际工程中请始终遵守目标站点的 robots 规则与法律法规，优先通过官方 API、数据授权等合规渠道获取数据。

> （注：部分内容由豆包工作 AI 生成）
