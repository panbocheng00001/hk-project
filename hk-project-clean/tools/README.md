# tools/

> 这份文档回答一个问题：**`tools/` 里这几个脚本各自做什么，什么时候跑。**

---

## 1. 一览

| 文件 | 作用 | 什么时候跑 |
|---|---|---|
| `check.ps1` | 一条命令：文案 lint + booking-lab 测试 + captcha-lab 测试，出汇总表 | 每次改完代码、提交前 |
| `wording_lint.py` | 扫全仓文本：block 词表、失效路径、凭据与个人数据；支持 `wording-lint: allow/off/on` 豁免 | 单独定位某一处命中时 |
| `wording_lexicon.json` | 词表数据源（block / warn / 失效路径 / pii 正则） | 只在要增删词条时改 |

`docs/WORDING_LEXICON.md` 由 `wording_lint.py` 生成，不要手改。

---

## 2. 常用命令

```bash
# 全仓检查（0 block 才算过）
python tools/wording_lint.py --root .

# 只看某几个目录
python tools/wording_lint.py --root . --paths docs booking-lab/docs

# 连 warn 一起看（defense/ 那类语境）
python tools/wording_lint.py --root . -v

# 机器可读报告（挂 CI）
python tools/wording_lint.py --root . --json lint.json

# 改了词表后重新生成文档
python tools/wording_lint.py --render-lexicon docs/WORDING_LEXICON.md
```

```powershell
pwsh -File tools/check.ps1                 # 全部
pwsh -File tools/check.ps1 -SkipKernel     # 只跑宿主侧（改 booking-lab 时更快）

# 机器上没有 pwsh（Windows PowerShell 5.1）时用等价写法，脚本两个版本都兼容：
powershell -ExecutionPolicy Bypass -File tools\check.ps1
```

---

## 3. 豁免怎么写

```python
# wording-lint: allow 这是规范正文，词表本身需要展示反例
```

```markdown
<!-- wording-lint: off -->
（这一段是反例展示，不参与检查）
<!-- wording-lint: on -->
```

豁免必须带理由，理由要说明**为什么这个词在这里不是问题**，而不是"先过了再说"。

---

## 4. 词表怎么改

1. 改 `wording_lexicon.json`（`block` 加条目，`warn` 放允许保留的）；
2. `python tools/wording_lint.py --render-lexicon docs/WORDING_LEXICON.md` 重新生成文档；
3. `python tools/wording_lint.py --root .` 看新增命中，逐个处理；
4. 同一个提交里更新 `docs/AI_CODING_SPEC.md` §3 的说明。

新增 block 词条的门槛：它在**任何**语境下都不该出现在本仓库。做不到就放 `warn`。
