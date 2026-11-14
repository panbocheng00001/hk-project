# -*- coding: utf-8 -*-
"""把 build_doc.py 的第八章改成「照着做」的任务导向版（含速查卡）。"""
from pathlib import Path

P = Path("build_doc.py")

NEW = r'''    # ---------------- 运行说明（照着做）
    h1(doc, "八、脚本怎么运行（照着做）")
    body(doc, "这一章按「你要做的事」组织，不按文件组织。每条命令都写清：在哪敲、敲什么、"
              "应该看到什么、不对怎么办。全部文件都在 E:\\googleProduct\\hKdriver。")

    h2(doc, "8.0 先打开一个窗口（每次都要）")
    body(doc, "按 Win + X → 选「终端」或「Windows PowerShell」，然后进到项目目录：", size=10)
    code_block(doc, ["cd E:\\googleProduct\\hKdriver"])
    body(doc, "确认三样东西在位，看到这些文件名就说明对了：", size=10)
    code_block(
        doc,
        [
            "check.py      grab.py      human.py      launch_chrome.ps1",
            "config.json   files\\港澳通行证.jpg   files\\内地驾驶证.jpg",
            "runs\\         （运行记录，出问题看这里）",
        ],
    )

    h2(doc, "8.1 场景 A：现在就想验证工具能不能用（任何时间都能做）")
    body(doc, "目的不是抢号，是确认「浏览器→官方系统」这条路通不通。三步，共约 1 分钟。", size=10)

    table(
        doc,
        ["步骤", "敲这条命令", "应该看到", "不对怎么办"],
        [
            ["A1　看站点通不通",
             "python quick_probe.py",
             "entry_status = 200\\nhas_checkbox = True",
             "若是 502：官方故障，只能等\\n若卡住：网络问题，重试"],
            ["A2　看有没有名额",
             "python check.py",
             "[10] 无名额（39s）\\n或 [0] 有名额",
             "[30] = 官方 502，等\\n[20] = 没探到，重试一次"],
            ["A3　看脚本能不能走完全程",
             "python grab.py --probe --workers 1",
             "第2步字段数=… ⚠ 该服务现时没有可预约时段",
             "若报「文件不存在」：检查 files\\ 下两张图"],
        ],
        widths=[3.6, 4.8, 4.6, 3.0],
        size=8.5,
    )

    body(doc, "A3 成功时会打印这一串（时间不同属正常）：", size=10)
    code_block(
        doc,
        [
            "打开入口页（第 1 次）",
            "…2s https://abs3.td.gov.hk/?lang=sc",
            "已同意条款",
            "点「预约」",
            "选择：不使用智方便",
            "国家/地方选项: ['中国内地', '中国内地以外的国家或地方']",
            "选国家/地方：中国内地",
            "第2步字段数=72 ⚠ 系统提示：该服务现时没有可预约时段",
        ],
        size=8.5,
    )

    h2(doc, "8.2 场景 B：工作日抢号（16:20 — 17:20）")
    body(doc, "这是唯一需要卡时间的场景。下表按时刻排，照着敲。", size=10)

    table(
        doc,
        ["时刻", "敲这条命令", "说明"],
        [
            ["16:20",
             "powershell -ExecutionPolicy Bypass -File .\\launch_chrome.ps1",
             "起真 Chrome（独立 profile）。看到「CDP 就绪：Chrome/156」即可，"
             "浏览器窗口会弹出来，之后别关它"],
            ["16:25",
             "python check.py",
             "确认站点可用。若返回 30（502）→ 当天不具备抢号条件，别浪费排队机会"],
            ["16:30　准时",
             "python grab.py --workers 3",
             "三条队列同时排队。三个窗口各自跑，日志前缀 w1/w2/w3 区分"],
            ["16:30—16:59",
             "（什么都不敲）",
             "挂着等。此时页面是排队页，别刷新、别关窗口；刷新会掉位置"],
            ["17:00",
             "（盯着日志）",
             "随机分配位置。看到「国家/地方选项:」说明有窗口放行了"],
            ["放行后",
             "手动接管那个窗口，或等脚本自动填",
             "脚本会自己填资料、上传图片、挑最早日期；20 分钟内必须点完递交"],
            ["OTP 到达时",
             "把验证码写进 otp.txt（一行数字）",
             "脚本每 2 秒读一次，读到就自动填；也可以直接在窗口里手敲"],
            ["成功后",
             "（等响铃）",
             "看到「★★★ 拿到预约」+ 电邮收到 tdabs@td.gov.hk 确认信，才算完"],
        ],
        widths=[2.4, 6.2, 7.4],
        size=8.5,
    )

    body(doc, "OTP 那一步的具体做法（这是唯一需要你动手的地方）：", size=10, bold=True)
    code_block(
        doc,
        [
            "① 邮件来自 tdabs@td.gov.hk，里面有一串数字",
            "② 在项目目录新建文本文件 otp.txt",
            "③ 里面只写那串数字，一行，保存",
            "④ 脚本 2 秒内读到并填入，然后自动点继续",
            "⑤ 若脚本没抢到填的机会（窗口在你手上），直接在窗口里手敲更快",
        ],
    )

    h2(doc, "8.3 场景 C：17:20 没抢到，挂着等「取消回流」")
    body(doc, "官方明确：经更改或取消的预约时段，名额会重新开放。这条通道竞争小得多。", size=10)
    code_block(
        doc,
        [
            "# 每 5 分钟探一次；有号就响铃，并保持浏览器窗口给你接手",
            "python check.py --loop 300 --ring --show --handoff",
            "",
            "# 想安静地挂着（不弹窗口，只响铃）",
            "python check.py --loop 300 --ring",
            "",
            "# 停掉：按 Ctrl + C",
        ],
    )
    body(doc, "注意：探测间隔不要低于 60 秒。官方 FAQ 明确会对异常活动做限制，"
              "而且取消回流本来就分散在全天，快没有意义。", size=10)

    h2(doc, "8.4 三条命令各自到底做了什么")

    table(
        doc,
        ["命令", "做了什么", "会不会动你的预约"],
        [
            ["python quick_probe.py",
             "打开入口页，确认能跳转到 abs3、勾选框能渲染（约 20 秒）",
             "不会。只看不动"],
            ["python check.py",
             "走一遍：勾同意 → 预约 → 不用智方便 → 选内地 → 看系统回什么（约 40 秒）",
             "不会。停在「有没有名额」这个判断上"],
            ["python grab.py --probe",
             "同 check.py，但会导出页面结构到 runs\\，用于改脚本（约 2 分钟）",
             "不会。明确不提交"],
            ["python grab.py --workers 3",
             "三条队列排队；任一条放行 → 填资料 → 上传 → 挑最早日期时段 → 递交",
             "会。这是唯一会真正提交的动作"],
            ["python fingerprint_check.py",
             "打开 about:blank 读浏览器指纹，对比自动化痕迹",
             "不碰官方系统"],
        ],
        widths=[4.6, 8.0, 4.4],
        size=8.5,
    )

    h2(doc, "8.5 一页速查卡（打印出来贴屏幕）")
    code_block(
        doc,
        [
            "cd E:\\googleProduct\\hKdriver          ← 每次先做这个",
            "",
            "python quick_probe.py                ← 站点通不通（20 秒）",
            "python check.py                      ← 有没有号（40 秒）",
            "python check.py --loop 300 --ring --show --handoff   ← 挂着等回流",
            "",
            "powershell -ExecutionPolicy Bypass -File .\\launch_chrome.ps1   ← 起真 Chrome",
            "python grab.py --workers 3           ← 正式抢号（3 条队列）",
            "python grab.py --workers 2           ← 站点不稳时用 2 条",
            "python grab.py --probe --workers 1   ← 只演练，不提交",
            "",
            "python fingerprint_check.py --attach http://127.0.0.1:9222    ← 指纹自检",
            "python build_doc.py                  ← 重新生成本文档",
            "",
            "退出码：0=有号  10=没号  20=没探到  30=官方502",
            "OTP：把验证码写进 otp.txt（一行数字）",
        ],
        size=8.5,
    )

    h2(doc, "8.6 全部参数")
    table(
        doc,
        ["参数", "作用", "默认"],
        [
            ["check.py --loop N", "每 N 秒探一次（最低 60）", "只探一次"],
            ["check.py --ring", "有号时响铃", "关闭"],
            ["check.py --show", "显示浏览器窗口", "无头"],
            ["check.py --handoff", "有号时保持窗口打开等你接手", "关闭"],
            ["grab.py --workers N", "同时排几条独立队列", "读 config（3）"],
            ["grab.py --probe", "只侦察到第 2 步，不提交", "关闭"],
            ["grab.py --attach URL", "接管调试端口的真 Chrome", "读 config"],
            ["grab.py --channel chrome", "用本机 Chrome 当内核", "读 config"],
            ["grab.py --headless", "无头模式（仅演练）", "关闭"],
            ["fingerprint_check.py --chrome", "用真 Chrome 测指纹", "chromium"],
            ["fingerprint_check.py --attach URL", "测接管模式的指纹", "—"],
            ["launch_chrome.ps1", "起真 Chrome 并开 9222 端口", "端口 9222"],
        ],
        widths=[5.6, 7.0, 3.4],
        size=8.5,
    )

    h2(doc, "8.7 三种典型输出（对照着看）")
    body(doc, "① 有名额（该立刻接手）：", size=10, bold=True)
    code_block(
        doc,
        [
            "17:00:41 [w2] …41s https://abs3.td.gov.hk/SelectCountry?lang=sc",
            "17:00:43 [w2] 国家/地方选项: ['中国内地', '中国内地以外的国家或地方']",
            "17:00:44 [w2] 选国家/地方：中国内地",
            "17:00:52 [w2] 第2步字段数=14 按钮=[]",
            "17:01:07 [w2] 上传 港澳通行证.jpg -> file#0",
            "17:01:15 [w2] 选日期 2026-11-17",
            "17:01:22 [w2] ★★★ 拿到预约，请立刻检查电邮 ★★★",
        ],
        size=8.5,
    )
    body(doc, "② 没名额（绝大多数时候）：", size=10, bold=True)
    code_block(doc, ["20:18:10 [10] 无名额（39s）  (时段=weekend)"], size=8.5)
    body(doc, "③ 官方 502：", size=10, bold=True)
    code_block(
        doc,
        [
            "20:18:10 [30] 官方返回错误页（502/维护），不是名额问题 url=https://abs3.td.gov.hk/?lang=sc",
            "  站点本身故障（502/维护），300s 后再看",
        ],
        size=8.5,
    )

    h2(doc, "8.8 出问题怎么查")
    table(
        doc,
        ["你看到的现象", "原因", "怎么处理"],
        [
            ["连不上 http://127.0.0.1:9222",
             "Chrome 没开或被关掉",
             "重跑 launch_chrome.ps1；或改用 python grab.py --channel chrome"],
            ["入口页等不到勾选框",
             "维护 / 502 / 链接已被更换",
             "先跑 check.py：返回 30 就是官方故障，等；正常就重跑"],
            ["国家/地方选项为空 []",
             "页面还没渲染完",
             "已修。若仍出现，重跑一次"],
            ["卡在排队页很久",
             "17:00 高峰，位置靠后",
             "正常。别刷新（会掉位置），等别的队列先出"],
            ["上传失败 / 文件不存在",
             "config 里的路径与文件不符",
             "确认 files\\ 下两张图存在；config 里路径写成 files/xxx.jpg"],
            ["OTP 收不到",
             "163 拦截、进垃圾箱、账号风控",
             "抢号前把 tdabs@td.gov.hk 加白名单；电脑和手机同时开邮箱"],
            ["提示「没有可供预约的时段」",
             "真的没号（周末 / 已抢光）",
             "不是脚本问题。工作日 17:00 再来"],
            ["提示「重复预约」",
             "同一身份已有有效预约",
             "立刻关掉其余队列，只保留一条"],
            ["脚本好像不动了",
             "在等页面元素（排队 / 慢加载）",
             "看 runs\\grab.log 最后一行；按 Ctrl+C 可停，重跑即可"],
        ],
        widths=[4.6, 4.8, 6.6],
        size=8.5,
    )

    h2(doc, "8.9 运行纪律（违反会白抢）")
    for t in [
        "一个身份只能有一个有效预约：多开队列只为提高中签率，中了一条立刻关掉其余。",
        "三条队列填的资料必须完全一致，否则交叉核对会判可疑。",
        "别用 VPN / 机房 IP / iCloud 私密转送——官方 FAQ Q20 明确会判异常。",
        "别在 16:00—16:30 维护时段跑，会话会被清、链接会换。",
        "别高频重跑 grab.py；502 期间跑一百次也没用，只会留下更多请求记录。",
        "别把真实证件号发给任何「代办」，也别贴到群里。",
    ]:
        bullet(doc, t, size=10)

    h2(doc, "8.10 文件清单：哪个能删，哪个别动")
    table(
        doc,
        ["文件 / 目录", "作用", "能删吗"],
        [
            ["check.py", "名额雷达", "别删"],
            ["grab.py", "抢位主程序", "别删"],
            ["human.py", "真人节奏模块（被上面两个引用）", "别删"],
            ["launch_chrome.ps1", "起真 Chrome", "别删"],
            ["fingerprint_check.py", "指纹自检", "可删"],
            ["quick_probe.py", "20 秒站点体检", "可删"],
            ["build_doc.py", "重新生成本文档", "可删"],
            ["config.json", "你的资料与策略", "别删（删了要重填）"],
            ["files\\", "两张证件图", "别删"],
            ["runs\\", "每次运行的截图与快照", "可清空"],
            ["profiles\\", "各队列的浏览器 profile（存排队会话）", "抢号期间别删"],
            ["chrome_profile\\", "接管模式的 Chrome 用户数据", "抢号期间别删"],
            ["probe\\、patch_ch8.py", "开发时的临时产物", "可删"],
            ["tdabs_monitor.py", "早期版本，已废弃", "可删（留作参考）"],
        ],
        widths=[4.6, 8.4, 3.2],
        size=8.5,
    )

'''

lines = P.read_text(encoding="utf-8").split("\n")
start = next(i for i, l in enumerate(lines) if 'h1(doc, "八、脚本怎么运行")' in l)
end = next(i for i, l in enumerate(lines) if "# ---------------- 反检测" in l)
lines[start:end] = NEW.split("\n")
P.write_text("\n".join(lines), encoding="utf-8")
print(f"已替换第 {start+1} ~ {end} 行，新章节 {len(NEW.splitlines())} 行")
