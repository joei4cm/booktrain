# booktran

书籍翻译 infra —— EPUB / TXT / PDF / MOBI 的 LLM 翻译流水线。断点续翻、术语一致、
自定义提示词、**可见的 token 与费用**、随翻随排版的预览。CLI-first，可嵌入自动化流程；
也提供网页端：拖入文件即建项目，页面内选模型角色、看进度与花费、导出下载。

> **v0.5**：配置从「每个项目一份冻结快照」改为「**引用**」——模型档案与角色路由定义在全局，
> 项目只写引用与差异；改一次全局档案，所有项目立刻生效。

架构研究自 [AiNiee](https://github.com/NEKOparapa/AiNiee)（见 `docs/research-ainiee.md`），
针对其短板做了增强：请求级指数退避重试、JSON 文本协议、JSONL 增量持久化、asyncio 并发、
**按角色路由的多模型**与**费用记账**。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
# PDF 支持（含版面保真输出）：.venv/bin/pip install -e ".[pdf]"
# 简繁转换：.venv/bin/pip install -e ".[zh]"
# 网页端：.venv/bin/pip install -e ".[web]"
# 可选精确 token 计数: pip install -e ".[tok]"

# 0. 第一次用：先体检（依赖 / 密钥 / 档案 / 连通性）
booktran doctor

# 1. 定义一个模型档案（全局，所有项目共用）
booktran profile add claude --provider anthropic --model claude-sonnet-4-5 \
    --price-in 3.0 --price-out 15.0
booktran role set translate=claude

# 2. 建项目（输入可以是单个文件或目录）
booktran init 我的小说.epub --target-langs chinese_simplified,english

# 3. 翻译（开跑前先给你成本估算；Ctrl-C 可优雅暂停，再跑一次即续翻）
booktran run -p booktran-我的小说

# 4. 导出译文 + 双语版
booktran export -p booktran-我的小说 --bilingual

# 5. 看钱花在哪
booktran cost -p booktran-我的小说

# 6. 成品质检（术语/未翻译/占位符/长度/编号/禁翻）
booktran proofread -p booktran-我的小说
booktran report -p booktran-我的小说

# 7. 看排好版的书页（随翻随排版，翻译中就在长）
booktran preview -p booktran-我的小说 --serve
```

零成本演示（不调 API）：`booktran run -p <项目> --fake`

## 命令

| 命令 | 作用 |
|---|---|
| `booktran doctor [--test]` | 环境体检：依赖 / 密钥来源 / 模型档案 / 角色路由 /（可选）连通性 |
| `booktran init <src>` | 建项目（选源文件 → 目标语言 → 排版策略） |
| `booktran run [-p P]` | 执行 / 续翻（`translate` 为旧名别名） |
| `booktran status [-p P]` | 进度 + 花费（`--json` 机器可读） |
| `booktran cost [-p P]` | 费用明细：按阶段 / 角色 / 模型 / 语言（`--json`） |
| `booktran preview [-p P]` | 打开/导出当前排版预览（v0.8） |
| `booktran export [-p P]` | 导出成品（txt / epub / pdf，可双语） |
| `booktran analyze [-p P]` | 全文术语分析 → `glossary.json` |
| `booktran proofread [-p P]` | 成品质检：产出问题清单（`--mode qa\|llm`） |
| `booktran report [-p P]` | 查看质检报告（Markdown / JSON） |
| `booktran preview [-p P]` | 渲染书页预览（`--serve` 起本地服务） |
| `booktran profile list\|add\|remove\|test` | 模型档案管理（全局） |
| `booktran role list\|set` | 角色路由：哪个角色用哪个档案 |
| `booktran serve` | 启动本地网页端 |

### 阶段化流水线

```
recognize → translate → proofread → typeset
```

每个 (段 × 目标语言 × 阶段) 有独立状态（`pending/running/done/failed/stale`）：
"已译"不等于"成品"。用 `--stage` 单独跑某一阶段：

```bash
booktran run -p <项目> --stage translate      # 只翻（默认）
booktran run -p <项目> --stage all --proofread-mode qa   # 翻 + 校
booktran run -p <项目> --stage proofread --proofread-mode llm   # 只校（模型审校）
```

上游改动会把下游阶段标记为 `stale`（数据保留，但不再算数）。

### 输出形态

| 环境 | 形态 |
|---|---|
| 终端 | 实时覆盖单行（进度 / 花费 / 速率 / ETA 同行），不刷屏 |
| 管道 | 逐阶段输出 |
| `--json` | **stdout 只有 NDJSON 事件流**，人类输出走 stderr |

退出码：`0` 成功 · `2` 用法错误 · `3` 配置错误 · `4` 鉴权失败 · `5` 限流耗尽 ·
`6` 预算超限 · `7` 部分成功（可续跑）· `8` 用户中断。

## 配置模型（配置是引用，不是快照）

优先级（后者覆盖前者）：

```
内置默认
  → ~/.config/booktran/config.toml      [llm] [translate]
  → ~/.config/booktran/profiles.toml     [profile.*] [roles] [budget]
  → <项目>/booktran.toml                 [project] [roles] [translate]（只写差异）
  → 命令行 / 网页端的一次性 override
```

### 模型档案与角色路由

```toml
# ~/.config/booktran/profiles.toml
[profile.claude]
kind      = "chat"            # chat | vision | ocr | local
provider  = "anthropic"
model     = "claude-sonnet-4-5"
key_env   = "ANTHROPIC_API_KEY"
concurrency = 8
price_in  = 3.0               # $/Mtok（不写 = 未知，界面显示 —，不猜）
price_out = 15.0

[profile.paddle-ocr]
kind = "ocr"; provider = "paddleocr"; model = "pp-ocr-v4"

[roles]                       # 谁干什么活
translate = "claude"
ocr       = "paddle-ocr"
vlm       = "gemini-vision"

[roles.translate_cascade]     # 级联：按段特征选模
default  = "claude"
fallback = "qwen-local"
rules = [{ match = { chars_gt = 400 }, use = "claude-opus" }]

[budget]
max_cost = 5.00               # 超限即暂停，不静默烧钱
```

**项目文件只写引用与差异**：

```toml
# <项目>/booktran.toml
[project]
source_lang  = "auto"
target_langs = ["chinese_simplified", "english"]
layout       = "original"

[roles]
translate = "deepseek-v3"     # 仅覆盖这一条角色

[translate]
chunk_budget_tokens = 6000
```

改一行全局档案 → **所有项目下次运行即生效**。项目里不存模型字段，也不存密钥。

### 密钥

只从三处读，**项目目录永不存密钥**：

1. 环境变量（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / …）
2. `~/.config/booktran/keys.toml`（建议 0600）：
   ```toml
   [key.anthropic]
   api_key = "sk-ant-..."
   ```
3. 档案里的 `key_env = "..."` 指定的环境变量

网页端填的密钥同样写进**全局** `keys.toml`，不落项目。

### 兼容

v0.4 的 `platforms.toml`（`[platforms.X]` + `[routing]`）仍然可用，
读取后自动转成档案与角色。旧项目（`project.json` 里存着配置快照）在下次运行时
自动迁移为 `booktran.toml`。

## 领域预设（专业/宗教类书籍）

```bash
booktran init 我的书.epub --domain religion   # 宗教与经文
booktran init 我的书.epub --domain law        # 法律与合同
booktran init 我的书.epub --domain medicine   # 医学与药理
booktran init 我的书.epub --domain academic   # 学术与论文
booktran init 我的书.epub --domain technical  # 技术与工程
booktran init 我的书.epub --domain literary   # 文学与小说
```

预设注入基础指令与作品背景之间（L1.5 层），随每次请求发送。也可写进
`booktran.toml` 的 `[project] domain = "religion"`，或 `translate --domain`。

## 整体自定义提示词

替换内置系统模板，作用于整个项目（每次请求）。支持占位符 `{source_language}` /
`{target_language}`；术语表/禁翻表/文风插槽与 JSON 输出协议段仍会追加，保证协议稳定。

```bash
booktran run -p <项目> --system-prompt "你是轻小说翻译专家……"
booktran run -p <项目> --system-prompt-file prompt.txt
```

优先级：命令行 > 项目 `glossary.json` 的 `system_prompt` > 配置文件 > 内置模板。

## 翻译背景

「翻译背景」是一段随每次请求注入提示词的作品说明（世界观、时代、人物关系、命名规则……），
让译文与设定保持一致。写在项目 `glossary.json` 的 `background` 字段，或在网页端「术语」页填写。

```json
{
  "background": {
    "title": "银色天空",
    "genre": "奇幻",
    "era": "蒸汽与魔法的十九世纪",
    "characters": "艾伦：落魄的机械师；缇娅：城防军军官",
    "naming": "人名保留英文音译，爵位统一译「勋爵」",
    "terminology": "魔导器一律译「魔导器」，不译「导魔器」"
  }
}
```

## 网页端

前端是 **Vite + React + TypeScript**（源码在 `web/`，构建产物进 `src/booktran/web/dist/`，
后端直接托管；没构建过也能跑——会回落到旧的单文件界面）。

```bash
# 开发：后端 + 前端分开跑
booktran serve --root ~/booktran-projects --port 8787
cd web && npm install && npm run dev     # http://localhost:5173（/api 自动代理）

# 构建（发布用）
cd web && npm run build
```

打开 http://127.0.0.1:8787 ：项目列表 / 进度与花费 / 模型角色 / 术语与背景 / 段落逐条编辑 /
导出与下载。后端是 FastAPI 薄封装——所有逻辑复用 CLI 的 pipeline 与 store。

- **费用可见**：概览与项目列表都显示已花金额；开跑前 CLI 会先给成本估算并等你确认。
- **可停止**：运行中有「停止」按钮，优雅暂停并落盘；结束后「开始翻译」恢复可用（可续跑）。
- **密钥不落项目**：网页端填的 key 写进全局 `keys.toml`。
- **SSE 推送**：作业事件实时推到前端（阶段/进度/费用/质检问题），不再靠轮询。
- **活页预览**：书页随翻译进度实时长大；未译段落显示原文占位。
- **校对面板**：质检问题就地处理（采用建议即写回译文，并留修订历史）。
- **工作台三栏**：左＝章节与阶段，中＝**活页**（书页 HTML，随翻译实时刷新），右＝校对问题清单。
- **模型与角色**：档案（价格、密钥状态）与角色路由在同一屏看得见；预算闸可设上限。

## 核心机制

| 机制 | 说明 |
|---|---|
| **配置不冻结** | 模型档案与角色路由属全局；项目只存引用与差异，全局一改全部跟随 |
| **多模型角色路由** | translate / proofread / typeset / vlm / ocr / analyze 各绑一个档案，可按段特征级联、可降级 |
| **费用记账** | 每次调用落 `usage.jsonl`（角色/阶段/模型/语言），未配置价格的档案显示「—」而不是 0 |
| **成本闸** | 开跑前给估算区间与预算上限；超限自动暂停并提示如何提额 |
| **断点续翻** | 每段一个状态机（pending/translated/excluded），JSONL 追加日志 + 快照，崩溃最多丢最后一条 |
| **无状态请求** | 每块独立请求 + 上一块原文/已译对照作为 `<上文>` 注入 —— 失败可独立重试，天然并发 |
| **轮次兜底** | 校验失败的块保持 pending，下一轮预算减半重试，最终退化为单段请求 |
| **JSON 协议** | 模型输出 `{"translations":[{"i":1,"t":"…"}]}`；解析失败自动降级编号纯文本协议 |
| **硬校验** | 缺段/空译/占位符丢失 → 整块重试，绝不写回缓存；疑似未翻译只警告不重试 |
| **术语表** | `analyze` 全文提取角色/术语 → LLM 合并去重 → 人工补译名 → 翻译时只注入当前块命中的词条；支持 `dst_<lang>` 按语言给不同译名 |
| **多语言** | 每段一条「语言轨道」，同一项目并行产出多个目标语言；进度/重试/续翻按语言独立 |
| **段级语言检测** | 脚本 + 功能词启发式（含简繁判定），逐段判断源语言；混排书不会整本判错；切块按语言分组，一个块只讲一种语言 |
| **阶段化** | recognize → translate → proofread → typeset，每 (段×语言×阶段) 独立状态；上游改动把下游标记 stale |
| **成品质检** | 术语不一致 / 疑似未翻译 / 占位符丢失 / 长度异常 / 编号残留 / 禁翻表残留 → 问题清单 + Markdown 报告；模型审校可叠加 |
| **排版引擎** | original / model / hybrid 三策略；样式表统一，**预览与导出共用渲染器**；随翻随排版 |
| **结构保真** | EPUB 行内标签（`<i>`/`<ruby>`/`<a>`）随段落文本送给模型，回写时按原文结构套回；nav/`<title>` 一并翻译 |

## 格式支持

| 格式 | 读 | 写（译文） | 写（双语） | 说明 |
|---|---|---|---|---|
| TXT | ✅ | ✅ | ✅ | 按行切段，空行结构无损 |
| EPUB | ✅ | ✅ | ✅ | 块级标签提取 + 片段回写，行内标签/nav/`<title>` 保留并翻译，lang/OPF 元数据更新 |
| PDF | ✅ | ✅（TXT/EPUB/PDF） | ✅（TXT/PDF） | 文本层提取 + 段落重构 + 版面 bbox；扫描页可交给 OCR/VLM |
| MOBI | ✅ | ✅ | ✅ | kindleunpack 优先，回落 Calibre |
| 图片 | ✅ | ✅ | ✅ | jpg/png/webp/tiff/bmp → OCR 通道（tesseract/paddle → 置信度不够升级 VLM） |

PDF 说明：按页提取文本层（同时记录每段 bbox），折行合并启发式（句末标点/对话符号/章节标题断段，
CJK 直连、拉丁补空格）；无文本层的页会提示：

- `--vlm`：扫描页交给多模态模型识别
- `--ocr`：扫描页走 **OCR 通道**——本地引擎（tesseract / paddleocr）先跑，
  置信度低于阈值自动升级到多模态模型；模型不可用时回退 OCR 结果而不是留空白

**排版**：`--layout original|model|hybrid`。
`original` 参照源排版，`model` 由模型产出样式表（失败自动回退 original），
`hybrid` 用模型样式表但保留源位置。翻译过程中书页实时刷新（`booktran preview` / 网页端「预览」）。

## 项目结构

```
booktran-<名>/
├── booktran.toml   # 项目配置：只写引用与差异（不含模型配置、不含密钥）
├── project.json    # 项目标识：源路径 + 创建时间
├── source/         # 源文件（网页端上传时留存）
├── state.json      # 全量状态快照（原子写）
├── events.jsonl    # 增量事件日志（append，定期压缩）
├── usage.jsonl     # 用量与费用流水
├── glossary.json   # 术语表 + 背景（analyze 生成，人工编辑）
├── quality_report.json/.md  # 质检报告
├── stylesheet.json # 排版样式表（排版策略产出）
├── artifacts/preview/  # 书页预览（随翻随更新）
└── output/         # 导出的译文/双语版
```

## 已知限制（v0.5）

- EPUB 行内标签：模型丢掉标签时按原文结构比例套回，强调位置是近似而非语义对齐
- PDF 版面保真是"重排"而非"绝对还原"：原页以图片形式保留，译文栏按纵向位置对齐
- VLM 识别没有强校验（无块数核对/抽样复核），识别错字不会被自动发现
- 简繁转换只作用于译文，不改原文；`analyze` 第二阶段的 LLM 合并失败时退回本地频次聚合
- MOBI 读取需装 `mobi` 包或 Calibre；OCR 通道需装 tesseract 或 paddleocr，否则直接走多模态模型
- 领域预设是通用翻译指南，不是针对单一教派/法域的术语表；配合 `analyze` 生成的术语表使用效果最佳
- "模型设计排版"目前只产出 HTML 预览样式表，EPUB/PDF 导出仍按源结构回写
- 网页端 API 无鉴权，仅适合本机/内网使用

## 测试

```bash
.venv/bin/python -m pytest tests/ -q   # 205 个用例
```

覆盖：单测 + fake 端到端 + 断点续翻 + PDF/EPUB/TXT + 档案/角色路由/级联/key 池 +
VLM/版面 PDF/简繁 + 多语言/背景 + 网页端上传/设置/分析/导出/下载/路径穿越 +
**配置优先级与"不冻结" + 费用记账 + 成本闸 + doctor + 阶段状态维度 + 校对质检 +
SSE 事件流 + 文档 IR + 段级语言检测 + 排版策略 + 书页预览 + OCR 通道**。

## 设计与规划

| 文档 | 内容 |
|---|---|
| [`docs/design-v2.md`](docs/design-v2.md) | 现状审计 + 目标架构：配置层 / 能力抽象 / 阶段流水线 / 排版系统 + 迁移路线 |
| [`docs/ux/cli.md`](docs/ux/cli.md) | CLI UX 规范：命令面、三种输出形态、成本闸、退出码表、错误文案规范 |
| [`docs/ux/web.md`](docs/ux/web.md) | WebApp UX 规范：四个目的地、活页预览工作台、视觉 token、状态清单、无障碍 |
| [`docs/ux/review-webapp.md`](docs/ux/review-webapp.md) | 对现有 WebApp 的设计评审（实测证据 + 优先问题） |
| [`docs/open-source.md`](docs/open-source.md) | 开源发布清单：仓库结构 / CI / 文档体系 / 资产缺口 / 发布流程 |
| [`web/README.md`](web/README.md) | 网页端开发说明（Vite + React + TypeScript） |
| [`PRODUCT.md`](PRODUCT.md) | 产品事实（用户、定位、原则、无障碍要求） |

## 许可

待定（开源筹备阶段确定，见 [`docs/open-source.md`](docs/open-source.md)）。
