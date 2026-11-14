# 移出的旧提示词文档（2026-11-15）

这四份文档从 `hk-project-clean` 代码树里移出来，原因是它们本身就是"读起来会跑偏"的文本：

- 通篇在讲"怎么让模型别把任务改写掉"，属于关于二次解释的元叙事，不描述任何代码行为；
- 里面写死的路径已失效（旧目录名、盘符绝对路径），照抄会读到空文件；
- 词表用的是对抗性说法，与仓库内的 `tools/wording_lexicon.json` 直接冲突。

替代品：`hk-project-clean/docs/PROMPT_PACK.md`（同样按任务类型给出提示词，但只写产物、路径、验收命令）。

| 文件 | 原位置 | 内容 |
|---|---|---|
| `PROMPT_OPS5.5.md` | `captcha-lab/docs/` | 最早的通用提示词（改造评测型项目为识别工具包） |
| `PROMPT_OPS5.5_HK.md` | `captcha-lab/docs/` | 第二版：把内核接进宿主的详细口径 |
| `PROMPT_OPS5.5_V3.md` | `captcha-lab/docs/` | 第三版：旁路求解 CLI + 离线回归 |
| `PROMPT_OPS5.5_HANDOFF_V4.md` | `booking-lab/docs/` | 第四版：只做人工交接提速（零识别） |

保留原因：里面的实测数字与踩坑记录（代理接管回环导致 503、口径常量 400/34/366、容差 3px）
仍有价值，已并入 `docs/PROMPT_PACK.md` 第 7 节与各模块的 docstring。
