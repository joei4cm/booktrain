# 更新日志

本文件记录 booktran 的重要变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

约定：

- **0.x 阶段**：`minor` 版本可能包含不兼容变更（配置格式、CLI 参数、存储结构）。
  每个版本的「破坏性变更」小节里会单独列出。
- **发布日期**在打 tag 时补齐（本文件不预填未经确认的日期）。
- 未发布的改动写在「未发布」小节。

---

## [未发布]

（暂无）

---

## [0.9.0] - <发布日期>

阶段化流水线 + 文档 IR + 排版引擎 + 前端重做 + 开源发布。核心变化：
**「已译」不再等于「成品」**，每个 (段 × 目标语言 × 阶段) 有独立状态；
排版从末端动作变成持续过程；前端从单文件界面重做为 Vite + React + TypeScript。

### 新增

- **开源发布**：`LICENSE`（Apache-2.0）、`CONTRIBUTING.md`、
  `CODE_OF_CONDUCT.md`（Contributor Covenant 2.1）、`SECURITY.md`、本文件、
  CI 工作流（`.github/workflows/ci.yml`：Python 矩阵 / 前端 / 端到端冒烟）、
  issue 与 PR 模板、Dependabot 配置（pip / npm / github-actions，weekly）。
- **图片输入**：jpg/png/webp/tiff/bmp 可作为书籍输入，走 OCR 通道
  （tesseract/paddle → 置信度不够升级多模态模型）。
- **领域预设**：宗教/法律/医学/学术/技术/文学六类，`--domain` 或
  `booktran.toml` 的 `[project] domain`。
- **修订回退**：`POST /api/project/{pid}/segment/{sid}/revert`；
  前端「校对」面板的「回退」按钮。

阶段化流水线 + 文档 IR + 排版引擎 + 前端重做。核心变化：
**「已译」不再等于「成品」**，每个 (段 × 目标语言 × 阶段) 有独立状态；
排版从末端动作变成持续过程；前端从单文件界面重做为 Vite + React + TypeScript。

### 新增

- **阶段化流水线**：`Track.stages` + `stages/` 包 + `engine`，
  阶段序列 `recognize → translate → proofread → typeset`；
  每个 (段 × 目标语言 × 阶段) 有独立状态（`pending/running/done/failed/stale`）；
  上游内容改动会把下游阶段标记为 `stale`（数据保留，但不再算数）。
- **PROOFREAD 阶段与质检报告**：`booktran proofread`（`--mode qa|llm`）
  与 `booktran report`，产出 `quality_report.json` / `quality_report.md`；
  检查项覆盖术语不一致、疑似未翻译、占位符丢失、长度异常、编号残留、禁翻表残留。
- **SSE 事件流**：作业事件（阶段 / 进度 / 费用 / 质检问题）实时推送到网页端，
  不再靠轮询。
- **文档 IR**（`ir.py`）：统一的 `Block` / `Span` 结构，
  字段含 `kind` / `level` / `spans` / `anchors` / `style` / `src_lang`；
  各格式适配器回填 IR 字段。
- **段级语言检测**：脚本 + 功能词启发式（含简繁判定）逐段判定源语言；
  切块按语言分组，一个块只讲一种语言。
- **OCR 通道**：本地引擎（tesseract / paddleocr）先跑，
  **置信度不足自动升级到多模态模型**；模型不可用时回退到 OCR 结果而不是留空白。
- **MOBI 读取**（`adapters/mobi.py`）。
- **排版引擎**（`layout/`）：`original` / `model` / `hybrid` 三种策略 + 统一样式表；
  **预览与导出共用同一个渲染器**；随翻随排版（每轮刷新 `artifacts/preview/`）；
  新增 `booktran preview`（`--serve` 起本地服务）。
- **前端重做**：Vite + React + TypeScript，源码在 `web/`。
  一级目的地为 **书架 / 工作台 / 术语 / 模型 / 成本**，外加常驻作业条与
  ⌘K 命令面板；工作台三栏（目录·阶段 ｜ 活页 ｜ 校对问题）；
  设计令牌集中在 `web/src/tokens.css`，与 `docs/ux/web.md` §7 一一对应。

### 变更

- CLI 新增：`--stage translate|proofread|all`、`--proofread-mode qa|llm|off`、
  `booktran proofread`、`booktran report`、`booktran preview`。
- **输出契约**：TTY 下实时覆盖单行（进度 / 花费 / 速率 / ETA 同行）、非 TTY 下逐行事件、
  `--json` 下 stdout 只有 NDJSON 事件流（人类输出走 stderr）；
  退出码语义化（`0` 成功 / `2` 用法错误 / `3` 配置错误 / `4` 鉴权失败 /
  `5` 限流耗尽 / `6` 预算超限 / `7` 部分成功 / `8` 用户中断）。
- 排版策略参数 `--layout original|model|hybrid`（默认 `original`）。

### 破坏性变更

- 旧的单文件网页界面（`src/booktran/web/index.html`）已删除；
  未构建前端时后端给出可操作的提示页，而不是回落到旧界面。

---

## [0.5.0] - <发布日期>

**配置从「每个项目一份冻结快照」改为「引用」**：
模型档案与角色路由定义在全局，项目只写引用与差异；改一次全局档案，所有项目立刻生效。

### 新增

- **配置引用化**：
  - 全局 `~/.config/booktran/profiles.toml` 定义模型档案（`[profile.*]`）、
    角色路由（`[roles]`）与预算（`[budget]`）；
  - 项目 `booktran.toml` **只写引用与差异**（例如只写一行 `roles.translate`）；
  - 配置优先级：`内置默认 → 全局 config.toml → 全局 profiles.toml →
    项目 booktran.toml → 命令行 / 网页端一次性 override`；
  - **密钥收敛到全局**：只从环境变量、`~/.config/booktran/keys.toml`
    或档案的 `key_env` 读取，**项目目录永不存密钥**。
- **ModelRouter**：按角色（`translate` / `proofread` / `typeset` / `vlm` / `ocr` / `analyze`）
  路由到不同模型档案；支持**按段特征级联**（`[roles.translate_cascade]`）与
  **失败降级**（`fallback`）。
- **费用记账**：每次模型调用落 `usage.jsonl`（角色 / 阶段 / 模型 / 目标语言 / token / 费用），
  新增 `booktran cost` 查看明细；**未配置价格的档案显示「—」，不猜价**。
- **新命令**：`booktran doctor`（依赖 / 密钥来源 / 档案 / 角色路由体检，只读不写）、
  `booktran cost`、`booktran profile`（档案增删改查与连通性测试）、`booktran role`（角色绑定）。
- **开跑前成本闸**：调用 API 之前先给估算区间与预算上限，超限自动暂停并提示如何提额；
  `--dry-run` 只试算，`--yes` 跳过确认（CI 用）。
- **NDJSON 输出**：`--json` 时 stdout 只有机器可读的事件流，人类输出改走 stderr。

### 变更

- 网页端同步切到引用式配置，并补齐：**停止**（优雅暂停并落盘）、**续跑**、
  **导出下载**、**费用可见**（概览与项目列表都显示已花金额）、
  **单一写入口**（术语 / 背景 / 提示词覆盖走同一个写入路径，避免互相覆盖）、
  以及一轮 **a11y 修复**。

### 破坏性变更

- 项目不再保存配置快照：`project.json` 里旧有的配置字段在下次运行时自动迁移为
  `booktran.toml`。
- v0.4 的 `platforms.toml`（`[platforms.X]` + `[routing]`）**仍然可用**，
  读取后自动转成模型档案与角色路由。
- 项目目录内的密钥配置不再被读取：密钥一律来自环境变量或全局 `keys.toml`。

---

### 关于 0.6 / 0.7 / 0.8

这几个版本的能力变更在 [`docs/design-v2.md`](docs/design-v2.md) 的迁移表里有逐条记录
（阶段化与 SSE、文档 IR 与 OCR 通道、排版引擎、前端重做）。
本文件目前只列 0.5.0 与 0.9.0 两个条目；若需要按 minor 拆分发布记录，
维护者可按迁移表补齐 —— 拆分时请把上面的条目按版本归属正确下移。
