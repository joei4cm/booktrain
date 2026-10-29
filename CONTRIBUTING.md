# 贡献指南

感谢你愿意动手。booktran 是一个 **CLI 优先、本地运行、不收集任何数据** 的书籍翻译工具，
所有改动都要能被陌生人复现、被机器验证。

本文只写三件事：**怎么开始**、**本地怎么跑起来**、**提交前要满足什么**。

- 仓库：<https://github.com/joei4cm/booktrain>
- 许可证：Apache-2.0（见 [`LICENSE`](LICENSE)）
- 行为准则：Contributor Covenant 2.1（见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)）
- 漏洞报告不走 issue，见 [`SECURITY.md`](SECURITY.md)

---

## 1. 三种贡献路径

### 1.1 报 bug（最省事，也最有用）

开 issue，选 **Bug 报告** 模板（`.github/ISSUE_TEMPLATE/bug_report.md`）。模板要的几样东西：

- `booktran --version` 与 Python 版本
- **能复现的命令**（把 `booktran run ...` 这样的完整命令行抄进去）
- `booktran status --json` 的输出（用 JSON 而不是截图，方便复现）
- 是否涉及 API key —— **只回答"是/否"，不要贴 key 本身**

排错时先看 [`docs/ux/cli.md`](docs/ux/cli.md) 的退出码表与错误文案规范：
退出码 `0` 成功 / `2` 用法错误 / `3` 配置错误 / `4` 鉴权失败 / `5` 限流耗尽 /
`6` 预算超限 / `7` 部分成功 / `8` 用户中断。带上退出码能省一轮问答。

### 1.2 提 issue（想法、UX、提问）

- 想要一个功能 → **功能建议** 模板（`feature_request.md`）
- 界面哪里别扭 → **UX 问题** 模板（`ux_issue.md`）
- 不知道怎么用 → 直接开 issue 提问，标题里写清"怎么 + 期望 + 实际"

写 issue 之前先搜一遍已有 issue。**大改动（改配置模型、改流水线阶段、改存储格式）
请先开 issue 讨论再动手**，PR 里临时改架构会让评审很难进行。

### 1.3 提 PR

1. fork → 新建分支（建议 `feat/xxx`、`fix/xxx`、`docs/xxx`）
2. 按 [§3](#3-本地开发环境) 跑起来，按 [§5](#5-提-pr-前必须跑过) 跑过门禁
3. 提交 PR，把**测试证据**贴进 PR 描述（模板见 `.github/PULL_REQUEST_TEMPLATE.md`）

---

## 2. 先读哪些文档

| 文档 | 什么时候读 |
|---|---|
| [`docs/design-v2.md`](docs/design-v2.md) | 改架构前：配置层、阶段流水线、文档 IR、排版引擎的现状与约束 |
| [`docs/ux/cli.md`](docs/ux/cli.md) | 改 CLI 前：三种输出形态、成本闸、退出码、错误文案规范（**这是契约**） |
| [`docs/ux/web.md`](docs/ux/web.md) | 改网页端前：四个目的地、活页预览、视觉 token、无障碍要求 |
| [`docs/open-source.md`](docs/open-source.md) | 想知道发布标准与缺口 |
| [`PRODUCT.md`](PRODUCT.md) | 想知道产品在解决什么问题、以及明确不做的事 |
| [`README.md`](README.md) | 快速上手与能力边界 |

---

## 3. 本地开发环境

### 3.1 Python 侧

要求 **Python ≥ 3.11**（`pyproject.toml` 的 `requires-python`）。

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[pdf,zh,web,tok,mobi,test]"
```

extras 说明：

| extra | 装什么 | 什么时候需要 |
|---|---|---|
| `pdf` | pymupdf | 读 PDF / 导出带版面保真的 PDF |
| `zh` | opencc | 简繁转换 |
| `web` | fastapi / uvicorn / python-multipart | `booktran serve` 网页端 |
| `tok` | tiktoken | 精确 token 计数（不装则用估算） |
| `mobi` | mobi | 读 MOBI |
| `test` | pytest | 跑测试 |

装完后 `booktran --version` 应能输出版本号；`booktran doctor` 会体检依赖、密钥来源、
模型档案与角色路由。**`doctor` 只读不写，也不产生费用。**

### 3.2 前端侧

前端是 **Vite + React + TypeScript**，源码在 `web/`，构建产物落到
`src/booktran/web/dist/`（该目录不入库，由构建生成）。

```bash
cd web
npm install        # 第一次；CI 用 npm ci
npm run dev        # http://localhost:5173，/api 自动代理到 127.0.0.1:8787
npm run build      # 产出到 ../src/booktran/web/dist
```

前后端分开跑时，先起后端：

```bash
booktran serve --root ~/booktran-projects --port 8787
```

> 网页端 API **没有鉴权**，只监听 `127.0.0.1`，仅限本机使用。不要把它暴露到公网。

### 3.3 配置文件在哪

```
~/.config/booktran/config.toml      # [llm] [translate] 等通用配置
~/.config/booktran/profiles.toml    # [profile.*] [roles] [budget]（全局档案与角色路由）
~/.config/booktran/keys.toml        # 密钥（建议 0600 权限）
<项目>/booktran.toml                # 项目配置：只写引用与差异
```

**项目目录永不写密钥。** 测试时请用假 provider 或本地假 key。

---

## 4. 跑测试

```bash
# Python：全量用例
.venv/bin/python -m pytest tests/ -q

# 只跑一个文件 / 一个用例
.venv/bin/python -m pytest tests/test_cli.py -q
.venv/bin/python -m pytest -q -k "cost"

# 前端：类型检查 / 单测 / 构建
cd web
npx tsc --noEmit
npx vitest run
npm run build
```

**改代码前先跑一遍全量测试**，确认基线是绿的；改完再跑一遍，红了就是你的问题。
不要为了让 CI 变绿而删测试或放宽断言——需要放宽时，在 PR 里写清理由。

---

## 5. 提 PR 前必须跑过

按改动范围选择：

```bash
# ① Python 改动
.venv/bin/python -m pytest tests/ -q

# ② 前端改动
cd web && npx tsc --noEmit && npx vitest run && npm run build

# ③ 界面改动：设计反模式检测（当前 web/src 无 finding）
cd web && npx --yes impeccable detect web/src

# ④ 端到端冒烟：假 provider，零成本、不需要 API key
booktran init examples/sample-book/sample.txt \
    --dir /tmp/booktran-smoke --fake --target-langs chinese_simplified
booktran run      -p /tmp/booktran-smoke --fake --yes
booktran proofread -p /tmp/booktran-smoke --fake
booktran preview  -p /tmp/booktran-smoke
booktran export   -p /tmp/booktran-smoke --bilingual
```

④ 的产物应出现在项目目录里：`quality_report.json` / `quality_report.md`、
`artifacts/preview/*.html`、`output/*.txt`（含 `_bilingual` 版）。

**PR 描述里要贴出这些命令的实际输出**（pytest 末尾的 `N passed`、
tsc/vitest 的结果、detector 的 finding 数）。没有证据的 PR 不会被合并。

---

## 6. 代码风格

### 6.1 Python

- **注释与 docstring 用中文**；标识符、类型名、报错里的技术名词用英文。
- **新增/修改的函数要带类型标注**（参数与返回值）。仓库不是 mypy 全绿，
  但新代码不要增加新的类型错误。
- 错误文案遵守 `docs/ux/cli.md` §5：**第一行是人话**，第二段给一条可粘贴的修复命令，
  最后一行"细节"放原始异常（截断到 200 字符）。不要把 HTTP 状态码或裸栈甩给用户。
- 不要引入新的运行时依赖而不说明理由；可选能力一律放进 extras。
- 状态、路径、JSONL 记录都走已有的 store / 事件日志机制，不要另起一套写入路径。

### 6.2 TypeScript / React

- `strict` 模式已开启，**禁止 `any`**；不确定类型用 `unknown` 再收窄。
- 组件 props 必须显式标注类型；不要用类型断言绕过检查。
- 状态放在组件树里合适的位置，别塞进全局可变单例。
- 文案走中文优先，但**文案层要可替换**（预留 i18n 出口），不要把文案和逻辑焊死。

### 6.3 CSS

- **颜色、间距、字号一律走 `web/src/tokens.css` 里的 CSS 变量**，
  不要在组件里硬编码色值。设计令牌与 `docs/ux/web.md` §7 一一对应。
- 状态不能只用颜色表达，必须有文字或图形标记（无障碍要求）。
- 正文对比度 ≥ 4.5:1，大字 ≥ 3:1；支持 `prefers-reduced-motion`。
- 键盘可达、焦点可见；新增交互要能用键盘走完。

### 6.4 通用

- 一条 PR 只做一件事。跨多个主题的 PR 很难评审。
- 新增行为要配测试：能用假 provider 测的**不要**依赖真实 API。
- 改了配置格式、存储格式或 CLI 契约 → 在 PR 里写明**破坏性变更**，
  并同步 `README.md`、`docs/ux/cli.md` 与 `CHANGELOG.md`。
- 提交信息用祈使句写清做了什么（`fix: 修正导出时双语段落的换行`），正文写原因。

---

## 7. AI 生成代码的声明要求

本仓库**允许**使用 AI 辅助写代码，但要求如下（写进 PR 描述的对应小节）：

1. **声明范围**：写清哪些文件、哪些段落由 AI 生成或由 AI 改写。
2. **必须能解释**：作者要对每一处 AI 产出的改动负责 —— 能讲清楚它为什么这么写、
   有什么替代方案、为什么选了这个。**看不懂的代码不要提交**，
   "模型说没问题"不构成理由。
3. **必须能测试**：AI 产出的行为要有对应的测试或可复现的验证步骤，
   并在 PR 里贴出实际输出。没验证过的 AI 代码视为未完成。
4. **遵守本仓库规范**：中文注释、类型标注、无 `any`、颜色走 CSS 变量、
   CLI 输出契约与退出码语义照旧。AI 生成的代码不豁免任何一条。
5. **来源与许可**：不要整段照搬来源不明的代码或提示词；引入第三方代码要说明来源与许可。
   **不要把密钥、真实书稿、个人路径、模型对话记录提交进仓库**（含 AI 生成内容里的这些东西）。
6. **不虚构事实**：AI 不得替你编造用户数、star 数、发布日期、翻译质量评分或"真实用户评价"。
   文档里涉及事实的地方，请用真实可核对的信息。
7. **文档与文案同样适用**：README、文档、UI 文案若由 AI 生成，作者同样要逐句负责。

违反以上要求的 PR 会被要求补充说明；反复提交无法解释的 AI 代码，评审会直接关闭。

---

## 8. 评审流程

- 维护者会先看**测试证据**，再看实现；没有证据的 PR 会先被打回补证据。
- 涉及配置模型、流水线阶段、存储格式、CLI 契约的改动，需要先有 issue 讨论。
- 评审意见用中文沟通；代码、标识符、日志、报错保持英文或原样。
- 讨论达成一致后由维护者合并。**Squash 合并**，PR 标题即提交信息。

---

## 9. 其他约定

- **不要在 issue / PR / 聊天里贴 API key 或含密钥的日志**（见 [`SECURITY.md`](SECURITY.md)）。
- 贡献即表示你同意你的贡献按 **Apache-2.0** 授权（见 [`LICENSE`](LICENSE)）。
- 项目不收集任何数据、不做用户画像；不要在仓库里加入遥测、崩溃上报或后台上报。
- 贡献者不要求提供真实邮箱作为署名；如需署名请自行在 PR 里说明。

有问题先问：开 issue，或在 `docs/open-source.md` 里写清楚缺口都可以。
