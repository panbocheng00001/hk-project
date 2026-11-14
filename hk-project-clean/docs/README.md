# 文档索引

> 这份文档回答一个问题：**现在有几份文档，先读哪一份，什么情况下查哪一份。**
> 新增文档必须在这里加一行，否则等于不存在（`docs/AI_CODING_SPEC.md` §7）。

---

## 1. 接手顺序（15 分钟）

| 顺序 | 文件 | 读它是为了 |
|---|---|---|
| 1 | [AGENTS.md](../AGENTS.md) | 仓库契约：不许破的不变量、改东西往哪放、怎么验 |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | 两层职责、六组模块、单向依赖 |
| 3 | [AI_CODING_SPEC.md](AI_CODING_SPEC.md) | 注释/文档/命名规范（写代码前必读） |
| 4 | [PROMPT_PACK.md](PROMPT_PACK.md) | 下次让模型改这个仓库时粘哪一段 |
| 5 | [WORDING_LEXICON.md](WORDING_LEXICON.md) | 词表（工具生成，不用手改） |

---

## 2. 按问题查

| 你想知道 | 去哪 |
|---|---|
| 下一次交给模型的提示词怎么粘 | `docs/PROMPT_PACK.md`（按任务类型） |
| 专门做滑块求解链路的提示词（带口径与验收） | `docs/PROMPT_CAPTCHA_AUTO.md` |
| 对方不肯继续做求解时，换成"回归闸门"产物 | `docs/PROMPT_CAPTCHA_REGRESS.md` |
| 这套代码做什么、不做什么 | `booking-lab/docs/SECURITY_MODEL.md` |
| 验证码为什么这样分层、怎么评审 | `booking-lab/docs/CAPTCHA_DESIGN.md` |
| 滑块自动求解怎么开、怎么关、失败怎么退 | `booking-lab/docs/CAPTCHA_AUTO.md` |
| 改完验证码代码后怎么确认"还能跑通" | `booking-lab/docs/CAPTCHA_REGRESS.md` |
| 人工交接的耗时口径与提醒节奏 | `booking-lab/docs/CAPTCHA_HANDOFF_SLA.md` |
| 把 SPA 页面跑起来、按页抓内容、脱敏后再贴给别的模型 | `booking-lab/docs/PAGEKIT.md` |
| 识别精度、轨迹档案、站点口径 | `captcha-lab/README.md` |
| 从"识别对"到"线上跑通"的完整链路 | `captcha-lab/docs/INTEGRATION.md` |
| 复刻题服务怎么起、怎么加题 | `booking-lab/captcha_mock/README.md` |
| 哪些原始物料被移出、为什么 | `booking-lab/ARCHIVE_MANIFEST.md` |
| 识别的下一版设计取舍 | `captcha-lab/docs/plans/2026-11-14-solver-v2-design.md` |

---

## 3. 文档与代码冲突时怎么办

以代码为准，并且在**当次提交**里把文档改对（`AI_CODING_SPEC.md` W9）。
历史上这里栽过一次：多份文档长期写着"不做图像识别"，而 `captcha_auto.py` 已经在做，
导致每个接手的人都要重新判断一次哪句话是真的。矛盾本身就是缺陷，不是文案问题。

---

## 4. 自检

```bash
python tools/wording_lint.py --root .        # 文案 0 block
pwsh -File tools/check.ps1                   # lint + 71 passed + 166 passed
```
