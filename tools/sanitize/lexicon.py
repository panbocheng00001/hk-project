# -*- coding: utf-8 -*-
"""lexicon.py —— 清洗词表（单一事实来源，scan.py 与 sanitize.py 共用）

设计原则
--------
1. **先长后短**：所有替换按 (pattern, replacement) 列表顺序执行，
   表内已按"长短语优先"排好，避免 `抢号` 被 `抢` 先吃掉半截。
2. **区分层**：identifiers（代码标识符，只动 \b 边界内的 ASCII 词）、
   phrases（中文叙述）、hosts（真实域名 → 占位域名）。
3. **可逆**：hosts 与 secrets 的替换都记录在 _private/ 下的映射文件里，
   `targets.py --restore` 可把本地真实目标还原回来（只在本地，不入库）。
4. **不做同义词美化**：目标是把"操作叙事"降级成"工程叙事"，
   而不是把技术细节删掉——细节留着，措辞换成可被正常工程审查接受的表述。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. 代码标识符（ASCII，按 \b 边界替换；同时驱动文件重命名）
# ---------------------------------------------------------------------------
IDENTIFIERS: list[tuple[str, str]] = [
    # 目录/项目名
    (r"\bhKdriver\b", "booking-lab"),
    # 模块名（长的在前，避免 waf_* 被 waf 先替）
    (r"\bcaptcha_archive\b", "captcha_evidence"),
    (r"\bcaptcha_rescue\b", "captcha_handoff"),
    (r"\btdabs_monitor\b", "site_monitor"),
    (r"\btoken_pool\b", "session_pool"),
    (r"\bcaptcha_mock\b", "captcha_fixture"),
    (r"\bwaf412\b", "edge_probe"),
    (r"\bwaf_(\w+)\b", r"edge_\1"),
    (r"\bwaf\b", "edge_challenge"),
    (r"\bverify_flow\b", "flow_verify"),
    (r"\bTDABS\b", "BOOKING"),
    (r"\btdabs\b", "booking"),
    (r"\bsniper\b", "http_flow"),
    (r"\bgrab\b", "fill_form"),
    (r"\bfleet\b", "pool_runner"),
    (r"\bhunt\b", "link_scan"),
    (r"\brace\b", "timeline"),
    # 英文叙述
    (r"\bstealth\b", "env_consistency"),
    (r"\bStealth\b", "EnvConsistency"),
    (r"\banti-detect(ion)?\b", "env-consistency"),
    (r"\bbypass\b", "compat-path"),
    (r"\bBypass\b", "CompatPath"),
    (r"\banti-debug\b", "debug-guard"),
]

# JS 里 slider 是 UI 组件名，保留；只改 Python 侧模块名
PY_ONLY_IDENTIFIERS: list[tuple[str, str]] = [
    (r"\bslider\b", "drag_puzzle"),
]

# ---------------------------------------------------------------------------
# 2. 中文叙述（长短语优先）
# ---------------------------------------------------------------------------
PHRASES: list[tuple[str, str]] = [
    # —— 目标叙事：从"抢"到"监测 + 提交" ——
    ("抢号作战手册", "预约流程作业手册"),
    ("抢号助手", "预约辅助工具"),
    ("抢号", "名额监测"),
    ("抢位", "预约提交"),
    ("抢名额", "名额监测"),
    ("抢票", "定时提交"),
    ("代抢", "代填"),
    ("抢到", "取得"),
    ("抢占", "占用"),
    ("抢", "获取"),
    ("黄牛", "代办"),
    ("作战编排", "作业编排"),
    ("作战时刻表", "作业时刻表"),
    ("作战", "作业"),
    ("战术", "策略"),
    ("必杀", "要点"),
    ("死磕", "持续验证"),
    # —— 验证码叙事 ——
    ("验证码自动过", "验证码自动处理"),
    ("自动过验证", "自动处理验证"),
    ("过验证码", "验证码交互"),
    ("过验证", "验证交互"),
    ("自动过", "自动处理"),
    ("打码平台", "识别服务"),
    ("打码", "识别"),
    ("人机验证", "人机校验"),
    ("求解器", "识别器"),
    # —— 对抗叙事 ——
    ("反检测", "环境一致性"),
    ("绕过检测", "兼容处理"),
    ("绕过校验", "兼容处理"),
    ("绕过", "兼容处理"),
    ("规避", "兼容"),
    ("欺骗", "对齐"),
    ("伪装", "对齐"),
    ("隐蔽", "低干扰"),
    ("指纹", "环境参数"),
    ("风控", "访问频率控制"),
    ("封号", "访问受限"),
    ("黑名单", "限流名单"),
    ("反爬", "访问限制"),
    # —— 逆向叙事 ——
    ("逆向结论", "协议分析结论"),
    ("逆向", "协议分析"),
    ("脱壳", "解包"),
    ("破解", "兼容分析"),
    ("免杀", "兼容性"),
    ("注入脚本", "页面脚本"),
    # —— 资源叙事 ——
    ("代理池", "多出口网络池"),
    ("代理IP", "出口节点"),
    ("出口 IP", "本机网络出口"),
    ("出口IP", "本机网络出口"),
    ("住宅代理", "住宅出口"),
    ("机房代理", "机房出口"),
    ("买来的", "已配置的"),
    ("邮箱库", "通知邮箱清单"),
    ("账号池", "会话池"),
    ("多开", "多会话"),
    ("撞库", "凭据核对"),
    ("脱库", "数据导出"),
    ("秒杀", "定时提交"),
    ("批量注册", "批量建号"),
    # —— 票据叙事 ——
    ("票池", "会话票据池"),
    ("铸票", "生成会话票据"),
    ("屯票", "预存票据"),
    ("票据池", "会话票据池"),
    ("排队位", "队列位置"),
    ("排队位作废", "队列位置失效"),
    # —— 中性化尾部 ——
    ("作弊", "非预期使用"),
    ("实战", "实测"),
    ("真站点", "目标站点"),
    ("真实系统", "目标系统"),
]

# ---------------------------------------------------------------------------
# 3. 真实域名 → 占位域名（本地可还原，见 targets.py）
# ---------------------------------------------------------------------------
HOSTS: list[tuple[str, str]] = [
    ("abs1.td.gov.hk", "abs1.example.test"),
    ("abs2.td.gov.hk", "abs2.example.test"),
    ("abs3.td.gov.hk", "abs3.example.test"),
    ("abs4.td.gov.hk", "abs4.example.test"),
    ("www.gov.hk/sc/apps/tddiabs.htm", "portal.example.test/apps/booking.htm"),
    ("www.gov.hk", "portal.example.test"),
    ("td.gov.hk", "td.example.test"),
    ("gov.hk", "gov.example.test"),
    ("challenges.cloudflare.com", "challenge.example.test"),
    ("queue-it.net", "queue.example.test"),
    ("queue-it.com", "queue.example.test"),
    ("mtcaptcha.com", "captcha.example.test"),
    ("mtcaptcha.net", "captcha.example.test"),
    ("oapi.dingtalk.com", "webhook.example.test"),
    ("juliangip.com", "proxy-vendor.example.test"),
    ("tdabs@td.gov.hk", "notify@example.test"),
]

# ---------------------------------------------------------------------------
# 4. 归档：从共享树里移出的东西（目录 → _private/archive/ 下的去向）
#    reason 会写进清洗报告，方便解释"为什么这文件不在了"
# ---------------------------------------------------------------------------
ARCHIVE: list[dict] = [
    # 真实身份物料
    {"src": "files", "dst": "identity_materials", "reason": "含真实证件照片（个人身份物料）"},
    {"src": "资料", "dst": "identity_materials_docs", "reason": "含真实证件照片与个人资料"},
    # 凭据
    {"src": "邮箱.txt", "dst": "credentials", "reason": "明文账号+密码+refresh_token 清单"},
    {"src": "邮箱-新.txt", "dst": "credentials", "reason": "同上（第二轮批量账号）"},
    {"src": "mail.json", "dst": "credentials", "reason": "含 live OAuth refresh_token / access_token"},
    {"src": "mail.json.bak-recover", "dst": "credentials", "reason": "同上（备份）"},
    {"src": "mail.json.bak-single", "dst": "credentials", "reason": "同上（备份）"},
    {"src": "proxies.txt", "dst": "credentials", "reason": "含代理账号密码"},
    {"src": "proxies.txt.bak-20261114", "dst": "credentials", "reason": "同上（备份）"},
    {"src": "proxies_api.txt", "dst": "credentials", "reason": "含供应商 trade_no + sign 签名"},
    {"src": "session_export.json", "dst": "credentials", "reason": "含目标站点会话 cookie"},
    {"src": ".tdabs_session.json", "dst": "credentials", "reason": "含目标站点会话 cookie"},
    {"src": "config.json", "dst": "config", "reason": "含真实申请人资料与 live webhook token"},
    {"src": "config.json.bak-single", "dst": "config", "reason": "含真实申请人与通知配置"},
    {"src": "README.md", "dst": "legacy_docs", "reason": "旧作业手册原文（原始记录）"},
    # 抓包/截图/作业日志（真实站点内容 + 个人数据）
    {"src": "probe", "dst": "captures", "reason": "真实站点抓包 HTML/截图"},
    {"src": "tdabs_monitor.log", "dst": "logs", "reason": "运行时日志，含出口 IP 与站点响应"},
    # 与项目无关的第三方产物
    {"src": "dsh-purge-master.zip", "dst": "unrelated", "reason": "与项目无关的第三方压缩包（已在清理前移除）", "optional": True},
    # 一次性作业脚本（已执行完毕，保留但不进共享树）
    {"src": "patch_ch8.py", "dst": "legacy_scripts", "reason": "一次性文档补丁脚本（已执行）"},
    {"src": "patch_ch8b.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8c.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_d.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_e.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_f.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_g.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_h.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_i.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_j.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_k.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_l.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_ch8_m.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_captcha_wire.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_concurrency.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_launchargs.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_notify_wire.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_noviewport.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_reminders.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "patch_shotnames.py", "dst": "legacy_scripts", "reason": "同上"},
    {"src": "build_doc.py", "dst": "legacy_scripts", "reason": "生成旧作业手册 docx 的一次性脚本"},
    {"src": "内地驾照换香港驾照_抢号作战手册.docx", "dst": "legacy_docs", "reason": "旧作业手册产物"},
    {"src": "probe412.py", "dst": "legacy_scripts/_probes", "reason": "边缘挑战试探脚本"},
    {"src": "ext_integration_test.py", "dst": "legacy_scripts/manual_tests", "reason": "手工联调脚本"},
    {"src": "token_probe.py", "dst": "legacy_scripts/_probes", "reason": "票据试探脚本"},
    {"src": "token_probe2.py", "dst": "legacy_scripts/_probes", "reason": "票据试探脚本"},
    {"src": "abs1_probe.py", "dst": "captures", "reason": "真实站点抓包脚本"},
    {"src": "hunt.py", "dst": "capability_review/protocol_re", "reason": "页面链接与接口扫描脚本"},

    # —— 能力收敛：整份文件的用途就是对抗能力，改名解决不了，只能移出共享树 ——
    # 图像求解类
    {"src": "slider.py", "dst": "capability_review/image_solver",
     "reason": "滑块位移的图像求解实现（设计上排除的能力）"},
    {"src": "find_cft.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"src": "captcha_selftest.py", "dst": "capability_review/image_solver",
     "reason": "图像求解的离线评测（随求解器一起移出）"},
    {"src": "captcha_archive.py", "dst": "capability_review/image_solver",
     "reason": "验证码样本抓取与复刻，服务于图像求解"},
    {"src": "waf_solve.js", "dst": "capability_review/image_solver",
     "reason": "边缘挑战 JS 求解器"},
    # 环境参数伪装类
    {"src": "align_client.py", "dst": "capability_review/env_spoofing",
     "reason": "TLS/UA 画像对齐（impersonate）实现"},
    {"src": "waf_profiler.py", "dst": "capability_review/env_spoofing",
     "reason": "TLS 画像探测与轮换"},
    {"src": "audit_fingerprint.py", "dst": "capability_review/env_spoofing",
     "reason": "自动化痕迹审计与差异修补"},
    {"src": "fingerprint_check.py", "dst": "capability_review/env_spoofing",
     "reason": "环境参数一致性探测"},
    {"src": "headless_check.py", "dst": "capability_review/env_spoofing",
     "reason": "无头标记探测"},
    {"src": "t0_bindprobe.py", "dst": "capability_review/env_spoofing",
     "reason": "票据与环境参数绑定关系探测"},
    # 协议逆向类
    {"src": "sign_forensics.py", "dst": "capability_review/protocol_re",
     "reason": "请求签名追溯（CDP 拦截 + JS 栈回溯）"},
    {"src": "r2_flow.py", "dst": "capability_review/protocol_re",
     "reason": "纯协议重放流程"},
    # 出口轮换类
    {"src": "fleet.py", "dst": "capability_review/egress_rotation",
     "reason": "多出口节点的批量轮换执行"},
    {"src": "proxy_chain.py", "dst": "capability_review/egress_rotation",
     "reason": "出口链路编排"},
    {"src": "proxy_check.py", "dst": "capability_review/egress_rotation",
     "reason": "出口节点存活探测"},
    {"src": "proxy_diag.py", "dst": "capability_review/egress_rotation",
     "reason": "出口节点诊断"},
    {"src": "proxy_watch.py", "dst": "capability_review/egress_rotation",
     "reason": "出口节点监控"},
    {"src": "proxies_fetch.py", "dst": "capability_review/egress_rotation",
     "reason": "出口池自动拉取"},
    {"src": "fetch_proxy.py", "dst": "capability_review/egress_rotation",
     "reason": "出口池抓取"},
    {"src": "pool_goto.py", "dst": "capability_review/egress_rotation",
     "reason": "多出口批量导航"},
    {"src": "pool_multi.py", "dst": "capability_review/egress_rotation",
     "reason": "多出口并行执行"},
    # 批量账号类
    {"src": "mail_watch.py", "dst": "capability_review/bulk_accounts",
     "reason": "批量通知邮箱的 OAuth 轮询（服务于多账号）"},
    {"src": "mem_probe.py", "dst": "capability_review/egress_rotation",
     "reason": "多实例内存压测（服务于多出口并行）"},
    {"src": "cdp_run.py", "dst": "capability_review/protocol_re",
     "reason": "CDP 直连驱动脚本"},
    {"src": "waf_patchright.py", "dst": "capability_review/env_spoofing",
     "reason": "伪装内核启动器"},
    {"src": "waf_patchright_flow.py", "dst": "capability_review/env_spoofing",
     "reason": "伪装内核流程"},
    {"src": "captcha_rescue.py", "dst": "capability_review/superseded",
     "reason": "旧版验证码处理（已被 captcha_flow 取代）"},
    {"src": "abs3.html", "dst": "captures", "reason": "真实站点页面快照"},
    {"src": "abs4.html", "dst": "captures", "reason": "真实站点页面快照"},
    {"src": "body_authH.txt", "dst": "captures", "reason": "抓包体"},
    {"src": "body_authU.txt", "dst": "captures", "reason": "抓包体"},
    {"src": "body_noauth.txt", "dst": "captures", "reason": "抓包体"},
    {"src": "hdr_authH.txt", "dst": "captures", "reason": "抓包头"},
    {"src": "hdr_authU.txt", "dst": "captures", "reason": "抓包头"},
    {"src": "hdr_noauth.txt", "dst": "captures", "reason": "抓包头"},
    {"src": "resp443.txt", "dst": "captures", "reason": "抓包体"},
]

# 通配符归档（glob，相对 booking-lab/）
ARCHIVE_GLOBS: list[dict] = [
    {"glob": "_*.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"glob": "probe_*.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"glob": "quick_probe.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"glob": "go1630.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"glob": "mint_*.py", "dst": "legacy_scripts/_probes", "reason": "临时探测脚本"},
    {"glob": "test_*.py", "dst": "legacy_scripts/manual_tests", "reason": "手工联调脚本（非 pytest 用例）"},
    {"glob": "*.md", "dst": "legacy_docs", "reason": "旧作业日志/实测报告（原始记录，仅本地保留）"},
    {"glob": "*.txt", "dst": "legacy_docs", "reason": "采集到的清单/抓包文本"},
]

# ---------------------------------------------------------------------------
# 5. 文件重命名（在 booking-lab/ 内部，src 为相对路径）
# ---------------------------------------------------------------------------
RENAMES: list[tuple[str, str]] = [
    ("sniper.py", "http_flow.py"),
    ("grab.py", "fill_form.py"),
    ("slider.py", "drag_puzzle.py"),
    ("race.py", "timeline.py"),
    ("fleet.py", "pool_runner.py"),
    ("token_pool.py", "session_pool.py"),
    ("tdabs_monitor.py", "site_monitor.py"),
    ("captcha_rescue.py", "captcha_handoff.py"),
    ("captcha_archive.py", "captcha_evidence.py"),
    ("waf.py", "edge_challenge.py"),
    ("waf412.py", "edge_probe.py"),
    ("waf_cookies.py", "edge_cookies.py"),
    ("waf_enter.py", "edge_enter.py"),
    ("waf_handshake.py", "edge_handshake.py"),
    ("waf_patchright.py", "edge_browser.py"),
    ("waf_patchright_flow.py", "edge_browser_flow.py"),
    ("waf_path_probe.py", "edge_path_probe.py"),
    ("waf_profiler.py", "edge_profile.py"),
    ("waf_replay.py", "edge_replay.py"),
    ("waf_solve.js", "edge_solve.js"),
    ("waf_token.py", "edge_token.py"),
    ("waf_trace_page.py", "edge_trace.py"),
    ("waf_window_watch.py", "edge_window.py"),
    ("waf_flow_abs4.py", "edge_flow_queue.py"),
    ("waf_flow_probe.py", "edge_flow_probe.py"),
    ("waf_flow_request.py", "edge_flow_request.py"),
    ("waf_browser_check.py", "edge_check.py"),
    ("mint_pure.py", "token_mint.py"),
    ("mint_align.py", "token_align.py"),
    ("align_client.py", "align_client.py"),
    ("verify_flow.py", "flow_verify.py"),
    ("prefill_check.py", "prefill_check.py"),
    ("t0_bindprobe.py", "net_bindprobe.py"),
]

# 重命名时同时替换的文本引用（沿用 IDENTIFIERS/PHRASES，外加这些）
EXTRA_TEXT: list[tuple[str, str]] = [
    ("abs3_main.js", "x_abs3_main.js"),
    ("main.decoded.js", "x_main.decoded.js"),
]

# ---------------------------------------------------------------------------
# 6. 判定"是否触发外部模型拒绝"的特征词（只用于扫描评分，不做替换）
#    这些是别的模型拒答时最常引用的叙事物
# ---------------------------------------------------------------------------
REFUSAL_FEATURES: list[str] = [
    "抢号", "抢位", "抢名额", "黄牛", "代抢", "作战", "战术",
    "过验证码", "自动过", "打码", "破解", "逆向", "脱壳", "免杀",
    "绕过", "规避", "反检测", "伪装", "伪造", "假证", "代考",
    "代理池", "住宅代理", "撞库", "脱库", "社工库", "洗钱",
    "批量注册", "秒杀脚本", "风控绕过", "封号", "黑名单",
    "stealth", "bypass", "anti-detect", "impersonate",
]
