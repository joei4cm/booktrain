# booktran v2 设计评审与重构方案

> 评审对象：v0.4.0 现状代码（`src/booktran/`，23 个模块 / ~7k 行）
> 评审日期：2026-10-29
> 结论：**核心流水线（轨道状态机 / 事件日志 / 无状态请求 / 硬校验）是好的，但配置模型与"作业模型"是从 v0 玩具阶段直接长出来的，无法承载多模型、many-to-many、随翻随排版、校对排版精修这些需求。建议做一次以「配置层 + 作业层 + 阶段层」为主轴的结构性重构，而不是继续打补丁。**

---

## 零、需求 → 现状覆盖矩阵

| 需求 | 现状 | 覆盖度 | 判定 |
|---|---|---|---|
| WebApp + CLI 双界面 | FastAPI + 单页 HTML / argparse | 80% | ✅ 形态对，但共享层没抽象干净 |
| 配置一个或多个大模型（VLM/OCR/…） | 只能 1 个 provider + 3 个固定任务名路由 | 15% | ❌ **核心缺失** |
| many-to-many：自动检测源语言 + 用户选目标语言 | 目标语言多值 ✅；源语言是字符分布猜测 | 55% | ⚠️ 检测需重做 |
| 输入 TXT/PDF/EPUB/MOBI | TXT/EPUB/PDF ✅；MOBI 无 | 75% | ⚠️ 补 MOBI + 图片 |
| 图片格式需 OCR 或 VLM | 只有 VLM 兜底（扫 PDF 页） | 40% | ⚠️ 无独立 OCR 通道、无图片输入 |
| 默认系统提示词 + override | 有 custom_prompt 整段替换 | 70% | ⚠️ 缺 domain preset 层 |
| 背景知识（专业/宗教）注入 | `glossary.background` 注入 ✅ | 85% | ✅ 够用，位置要挪 |
| 透明：token / 费用 / 进度 / 自动重试 | token ✅、进度 ✅、重试 ✅；**费用 0%** | 60% | ❌ 缺费用 |
| 随翻随排版 | 无。导出是末端批量命令 | 0% | ❌ **完全缺失** |
| 翻译后校对 + 排版精修 | 无校对阶段；只有 chunk 级校验 | 10% | ❌ **完全缺失** |
| 排版：参照原排版 or 模型设计 | 只有"参照原排版"一种 | 30% | ❌ 缺策略抽象 |
| 禁止"一个任务一个配置" | 恰恰就是"一个项目一份冻结配置" | 0% | ❌ **这正是当前最差的地方** |

---

## 一、现状审计（12 条，按严重度）

### P0-1 「一个任务一个配置」的根因：project.json 把配置拍平快照

- `config.py:198-205` `save_project_manifest()` 把整个 `AppConfig` 拍平成 JSON 存进 `project.json`。
- `cli.py:43-57` `_restore_cfg()`：`cfg.llm = saved.llm`、`cfg.tr = saved.tr` —— **整体替换**，项目快照优先级高于全局 `config.toml`。
- `web/app.py:82-91` 同样整体替换；`web/app.py:210-245` `apply_settings()` 又把网页表单写回 `project.json`。

后果：

1. **全局档案改了，既有项目不跟随**。在 `platforms.toml` 里把模型从 `claude-sonnet-4-5` 换成 `claude-opus-4-1`，所有已建项目继续用旧快照 —— 改一个模型要逐个项目重做。
2. **引用身份丢失**。快照只存字段值，不存"用的是哪个 profile"，无法回溯、无法切换、无法做多模型并存。
3. **密钥与配置错位**。快照刻意清空密钥（`config.py:87-88`），密钥改为从环境变量取；但 Web 端又在项目目录写 `secrets.json`（`web/app.py:247-266`）—— 结果是**密钥配置在项目里、模型配置在全局里**，两套生命周期，两处漂移。
4. **四份配置源、两种合并语义**。`config.toml` + `platforms.toml` × (全局 + 项目) = 4 个文件；合并逻辑在 `config.py:112-173` 和 `web/app.py` 手写重复两遍。

> 这条不是"实现细节"，它决定了整个产品形态：**只要配置是"每个任务一份"，就不可能在一个任务里同时用多个模型（翻译用 A、识别用 B、校对用 C），也不可能让一次全局配置变更覆盖全部项目。** 需求里"配置一个或者多个大模型"和"禁止一个任务一个配置"是同一句话的两面。

### P0-2 模型数量被锁死为 1

- `llm/__init__.py:17-40` `make_provider(cfg)` 按 `cfg.llm` 造**一个** provider，整个项目共用。
- 唯一的"多模型"是 `config.py:193-195` 的 `routing`：`translate` / `analyze` / `vlm` 三个**固定任务名**，每个只能指向一个档案，且这些路由本身也被快照进项目。
- 缺：OCR 角色、校对角色、排版角色、级联路由（大模型跑难段/小模型跑易段）、多厂商并行负载均衡、按段模型归属记账。

### P0-3 费用：全仓 grep `cost|price|usd` = 0 命中

- `model.py:157-184` `Stats` 只有 `requests / failed_chunks / prompt_tokens / completion_tokens / retried_requests`。
- 没有价格表、没有按模型/角色/语言的成本分摊、没有预算上限、没有 Web 成本面板。
- 需求明写"消费的 token，**费用**" —— 当前只能给 token 数，用户无法回答"这本书花了多少钱"。

### P0-4 没有校对与排版精修阶段

- 流水线只有 `analyze` + `translate`（`pipeline.py:87` `run_pipeline`）。
- 对照 AiNiee 的四类任务（Translator / Polisher / Proofread / Analysis，见 `docs/research-ainiee.md:36`），本项目只有 Translator。
- `quality/checks.py` 是 chunk 级硬/软校验，不是成品级终检；**且 `glossary_violations()`（`checks.py:99`）从未被 pipeline 调用** —— 术语遵从检查是死代码，术语一致性在生产路径上没有任何强制手段。

### P0-5 没有"随翻随排版"

- 排版是末端批动作：`cli.py:157-222` `cmd_export`。
- 唯一排版实现 `pdf_layout.py`（PDF 按 bbox 重排）；EPUB/TXT 走"原结构回写"（`adapters/epub.py:426` `_rebuild`）。
- Web 端"段落"页是逐条表格（`web/index.html:183-193`），用户看不到"书正在成型"，只看到一堆行。
- 需求"随翻随排版"= 需要**中间产物增量渲染 + 预览服务**，当前 0 实现。

### P0-6 排版策略只有一种

- 需求："参照原排版 or 模型自己设计排版"。
- 现状：只有参照原排版；且 PDF 是"图片+右栏文字按 y 对齐"（`pdf_layout.py:141` `_draw_column`），README 自己承认是"重排而非还原"。
- 缺 `LayoutPolicy` 抽象：`original`（沿用源样式/位置）/ `model`（模型产出样式表）/ `hybrid`（模型定样式、位置沿用源）。

### P1-1 输入格式缺 MOBI / 图片

- `project.py:12` `SUPPORTED_EXTS = {".txt", ".epub", ".pdf"}`。
- 需求明确要 MOBI；且"如果是图片格式的"暗示图片作为输入，目前图片只能作为 PDF 内页出现。

### P1-2 OCR 只是 VLM 的副业

- `vlm.py` 全部走多模态模型；无传统 OCR（PaddleOCR / Tesseract / Surya / 云 OCR）作为"快而省"的通道。
- 需求"需要 OCR **或者** VLM 来识别" = 应该是二选一或级联（先 OCR，置信度低再升级 VLM），现在只有 VLM 一种，且 `vlm.py:101-136` 逐页串行调用，没有并发、没有置信度、没有复核（README 已知限制自认）。

### P1-3 源语言检测是字符分布猜测

- `pipeline.py:229-238` `_guess_source_lang()`：假名→日语、谚文→韩文、汉字→中文、其余→英文。
- 法/德/俄/西语长文本一律判成英文；混合语种书（作者附注、英文书名）错判；且**检测粒度是整本书**（`doc.meta["source_lang"]`），无法处理逐段语言漂移。
- 需求是"智能检测输入语言"，对标 AiNiee 的 `lang_code(语言+置信度)`（`docs/research-ainiee.md:44`）—— 本项目在这一点上比参考实现更弱。

### P1-4 术语表的按语言覆盖是字符串约定

- `glossary.py:71-73` `dst_<lang>` 靠字符串拼键；many-to-many 下每个术语要人工填 N 列。
- 且 `glossary.json` 同时塞了 `terms / non_translate / style / notes / system_prompt / background`（`glossary.py:43-48`）—— **词库、背景、提示词三种东西混在一个文件**，这正是"配置散落"的又一面。

### P1-5 提示词只有一层，没有 domain preset

- `prompt/builder.py:138-177` `build_system()`：`custom_prompt` 非空就**整段替换**内置模板，术语/背景插槽靠追加保留。
- 需求"默认系统提示词和 override" + "专业/宗教类书籍需要背景知识范围" ⇒ 需要分层：`base(default) → domain preset → project background → run override`，且要能锁定某些段不被覆盖（尤其输出协议段）。

### P2 级别（不阻塞重构，但要一并处理）

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | Track 只有 `target/status/error/model`，没有阶段维度，无法表达"已译待校 / 校对待定 / 排版待精修" | `model.py:16-40` |
| P2-2 | Web 端轮询 `/events`（`web/app.py:747-753`）而非 SSE；无成本面板、无实时预览、无多模型配置界面（表单只有单个 provider/model/base_url） | `web/index.html:128-166` |
| P2-3 | EPUB 行内标签用"位置比例套回"（`adapters/epub.py:144` `_apply_original_structure`），语义错位 | README 已知限制 |
| P2-4 | `Segment.meta` 用格式特有 dict 塞 `original_html/bbox/line_break`，加 MOBI/图片后必然爆炸 | `model.py:52-54` |
| P2-5 | Web API 无鉴权（README 自认），密钥存项目目录 | `web/app.py:588` |
| P2-6 | `stats.prompt_tokens == 0` 时用字符估算回填（`pipeline.py:211-212`），与真实用量混在一栏，账目不可信 | `pipeline.py:211` |

### 该保留的（不要重写）

| 机制 | 位置 | 评价 |
|---|---|---|
| 段 × 目标语言的**轨道状态机** | `model.py:16-92` | 优秀，many-to-many 的正确基础，v2 原样保留并加阶段维度 |
| **事件日志 + 原子快照** | `store.py` | 优秀，崩溃最坏丢一条 |
| **无状态请求 + 上文注入** | `chunker.py:50-70` | 正确，天然并发、块级独立重试 |
| **轮次减半兜底** | `pipeline.py:144` | 正确 |
| **硬校验不通过不写回** | `quality/checks.py:43-69` | 正确，"宁可不写，不可写错" |
| **按块命中注入术语** | `glossary.py:55-73` | 正确（token 控制），需搬进新配置层 |
| 请求级指数退避重试 | `llm/ratelimit.py:64-76` | 正确，补上了 AiNiee 的最大短板 |

---

## 二、目标设计

### 2.1 五条原则

1. **配置是引用，不是快照。** 项目只引用 profile id + 写差异；全局档案一改，所有项目立刻生效。
2. **一个作业，一个角色绑定表。** "用哪些模型"由**角色**（role）决定，而不是由项目决定：translate / proofread / typeset / vlm / ocr / analyze 各自绑定 profile，可在全局配默认、项目按需覆盖。
3. **能力抽象优先于厂商抽象。** 后端暴露 `text / vision / ocr / embed` 能力，不是 `anthropic / openai`。
4. **阶段化 + 状态维度化。** 每个 (段 × 目标语言 × 阶段) 有独立状态；"已译"不等于"成品"。
5. **渲染是持续过程，不是末端动作。** 每完成一批就增量渲染，Web 端看的就是最终排版的实时版本。

### 2.2 配置模型（消灭"一个任务一个配置"）

**优先级（后者覆盖前者）：**

```
代码内置默认  →  ~/.config/booktran/config.toml  →  ~/.config/booktran/profiles.toml
              →  <项目>/booktran.toml（只写差异）  →  CLI/UI 一次性 override
```

**`~/.config/booktran/profiles.toml`（全局，任意多个档案并存）：**

```toml
# ---- 模型档案：可同时存在任意多个，密钥一律走环境变量/keyring ----
[profile.claude-sonnet]
kind      = "chat"                 # chat | vision | ocr | local
provider  = "anthropic"
model     = "claude-sonnet-4-5"
base_url  = "https://api.anthropic.com"
key_env   = "ANTHROPIC_API_KEY"
concurrency = 8
rpm         = 60
price_in    = 3.0                  # $/Mtok，用于费用记账
price_out   = 15.0
price_cache_read = 0.30
vision      = true

[profile.gemini-vision]
kind = "vision"; provider = "openai"; model = "gemini-2.5-pro"
base_url = "https://generativelanguage.googleapis.com/v1beta"
key_env = "GEMINI_API_KEY"
price_in = 1.25; price_out = 10.0

[profile.paddle-ocr]
kind = "ocr"; provider = "paddleocr"; model = "pp-ocr-v4"
# 本地引擎，费用恒为 0，但会占用 CPU 配额
price_in = 0.0; price_out = 0.0

# ---- 角色路由：把角色绑到档案；可级联 ----
[roles]
translate = "claude-sonnet"
proofread = "claude-sonnet"
typeset   = "gemini-vision"
vlm       = "gemini-vision"
ocr       = "paddle-ocr"
analyze   = "claude-sonnet"

# 级联：按段特征选模型（需求里的"配置一个或多个大模型"落到段级）
[roles.translate_cascade]
default = "claude-sonnet"
fallback = "qwen-local"
rules = [
  { match = { chars_gt = 400 },                 use = "claude-opus" },
  { match = { glossary_density_gt = 0.5 },      use = "claude-opus" },
  { match = { target_lang = "japanese" },       use = "qwen-ja" },
]

# ---- 预算：透明可见的一部分 ----
[budget]
max_cost   = 5.00      # 超过即暂停并提示
max_tokens = 2_000_000
```

**`<项目>/booktran.toml`（指针 + 差异，**不再持有模型配置**）：**

```toml
[project]
source_lang  = "auto"                      # auto | 显式 | "per-segment"
target_langs = ["chinese_simplified", "english"]
layout       = "original"                 # original | model | hybrid
detect_layout = true                      # 有图/扫描页时自动 OCR/VLM

[roles]                                    # 只覆盖需要改的角色
translate = "deepseek-v3"

[translate]
chunk_budget_tokens = 6000
```

**密钥**：只在 `~/.config/booktran/keys.toml`（0600）或环境变量。**项目目录永不存密钥** —— 现有 `<项目>/secrets.json` 废止。

**解析器骨架：**

```python
# booktran/config/resolver.py
@dataclass
class Resolved:
    profiles: dict[str, Profile]
    roles:    dict[str, RoleSpec]
    tr:       TranslateConfig
    llm:      LLMConfig          # 默认档案展开后的兜底值
    budget:   Budget

def resolve(project_dir: Path | None = None, overrides: dict | None = None) -> Resolved:
    base = defaults()                                   # 1 内置
    base = merge_toml(base, GLOBAL / "config.toml")     # 2 全局
    base = merge_toml(base, GLOBAL / "profiles.toml")   # 3 档案 + 角色
    if project_dir:
        base = merge_toml(base, project_dir / "booktran.toml")  # 4 项目差异
    return apply_overrides(base, overrides or {})       # 5 CLI/UI
```

> 关键性质：`merge_toml` 只写**出现过的字段**，项目文件里没写的模型字段一律从全局档案现取。改一行全局配置 → 全部项目立刻跟随；项目想特殊化时只写一行 `roles.translate = "..."`。

### 2.3 能力抽象与模型路由

```python
# booktran/models/base.py
class Capability(str, Enum):
    TEXT   = "text"      # 翻译 / 校对 / 术语分析
    VISION = "vision"    # 图文：识页、版面理解、样式生成
    OCR    = "ocr"       # 文字识别：本地引擎或 OCR 服务
    EMBED  = "embed"     # 向量：风格/术语一致性检索

@dataclass
class Request:
    system: str
    user: str
    images: list[bytes] = field(default_factory=list)
    json_schema: dict | None = None      # 能用结构化输出就用
    max_output_tokens: int = 8192
    temperature: float | None = None

@dataclass
class Response:
    text: str
    usage: Usage
    raw: dict | None = None

class Backend(Protocol):
    id: str
    model: str
    caps: frozenset[Capability]
    async def complete(self, req: Request) -> Response: ...
```

后端实现：`anthropic` / `openai-compat`（DeepSeek、vLLM、Ollama、OpenRouter…）/ `paddleocr` / `tesseract` / `http-ocr` / `fake`。

```python
# booktran/models/router.py
class ModelRouter:
    """角色 + 段特征 → 后端；负责降级、记账归属、缓存复用。"""
    def pick(self, role: str, seg: Segment, ctx: Context) -> Backend: ...
    async def run(self, role: str, req: Request, *, seg=None, ctx=None) -> Response:
        # 失败按 cascade.fallback 降级；每次调用产出一条 Usage
```

> 这样"配置一个或多个大模型"变成可执行的语义：**角色表 + 级联规则**。翻译用 claude、识别用 gemini-vision、OCR 用本地 paddle、校对用便宜模型，全局配一次，所有项目复用。

### 2.4 文档中间表示（IR）—— 解锁 MOBI / 图片 / 排版

现在的 `Segment.meta` 是格式特有的 dict（`model.py:52-54`），加两种格式就会爆炸。改成统一 Block IR：

```python
# booktran/ir.py
@dataclass
class Span:            # 行内标记
    text: str
    bold: bool = False; italic: bool = False
    href: str = ""; ruby_base: str = ""; ruby_text: str = ""

@dataclass
class Block:
    bid: str
    kind: str          # heading | para | list | quote | code | caption | footnote | pagebreak | image
    level: int = 0     # 标题层级
    spans: list[Span] = field(default_factory=list)
    style: dict = field(default_factory=dict)   # 对齐/缩进/字号权重
    anchors: list[dict] = field(default_factory=list)  # 源版面锚点：{page,bbox,xpath}
    src_lang: str = ""  # 逐段语言（支持混排）
    text: str = ""      # 纯文本（送模型）
    targets: dict[str, str] = field(default_factory=dict)  # lang -> 译文
    stages: dict[str, str] = field(default_factory=dict)   # 阶段状态
```

- **TXT** → Block(para)；**EPUB** → Block（保留 span + 结构树）；**PDF** → Block（bbox → anchors）；**MOBI** → 解包 HTML → 复用 EPUB 管线；**图片** → OCR/VLM → Block（anchors 来自 bbox）。
- 导出统一由 **LayoutEngine** 消费 Block，不再由各 Writer 直接改原始文件。

### 2.5 流水线：阶段化 + 状态维度化

```
INGEST → RECOGNIZE → STRUCTURE → ANALYZE → TRANSLATE → PROOFREAD → TYPESET → EXPORT
```

- 每个阶段是 `Stage` 协议对象（`name` + `run(job, ctx)`），由 `engine.run_job()` 按计划编排，可单独重跑某一阶段（`booktran run --stage proofread`）。
- 状态维度：`Block.stages[stage] ∈ {pending, running, done, failed, skipped}`。于是"已译但未校对"是可表达的，Web 端可以按阶段过滤、可以只重跑失败块。
- **可取消**：作业接受一个 cancel token（CLI 的 `Ctrl-C` 与 Web 的"停止"按钮共用同一套语义）。取消时优雅落盘、把进行中的阶段置为 `paused`，**不写入半截译文**；续跑从 `paused` 处继续。
- **单一写入口**：所有用户可编辑的内容（术语、背景、提示词覆盖）走一个 `SettingsStore.write()`，带 dirty 标记，避免多路径互相覆盖（v0.4 的"保存背景"会静默吃掉未保存的术语编辑，见 `docs/ux/review-webapp.md` §1.2 B1）。
- **校对（PROOFREAD）**：两种模式——
  - `qa`（默认）：本地规则（占位符残留/序号残留/原文复读/术语遵从/长度比异常/禁翻表残留）产出问题清单 + 建议，人工确认；
  - `llm`（可选）：以原文+译文为输入让模型做润色/一致性校对，输出 diff，写回 `target` 并记录 `revision`（可回退到上一版）。
- **术语遵从检查接线**：`glossary_violations()`（现为死代码）接入 PROOFREAD 阶段。

### 2.6 语言检测（many-to-many 的前提）

```
脚本检测 (unicode ranges) → 统计/CLD3 (可选 py3langid) → 段级语言标记 → LLM 抽样复核(可选)
```

- 粒度：**段级** `Block.src_lang`，书内混排也能正确处理；书级汇总 = 出现最多的段语言。
- 目标语言：用户选 1..N 个；prompt 的 `{source_language}` 按段填入（现在整本一个值）。
- 提示词中显式给出目标语言集合，禁止模型自行混合输出。

### 2.7 提示词分层

| 层 | 载体 | 可否被 override |
|---|---|---|
| L0 协议段（输出格式、编号规则） | 内置 | **锁定**（需显式 `--unlock-protocol` 才可改） |
| L1 基础翻译指令 | `prompts/base/*.md`（内置多语言/通用/文学/技术） | 可 |
| L2 领域 preset | `prompts/domain/{宗教,法律,医学,法律合同,学术,游戏}.md` | 可 |
| L3 项目背景 | `<项目>/booktran.toml [background]`（题材/时代/人物/命名规则） | 可 |
| L4 术语 / 禁翻 / 文风 | `<项目>/lexicon.toml` | 可 |
| L5 本次运行 override | CLI `--system-prompt-file` / UI 文本框 | 可（最高） |

合并顺序 L0→L5，后者覆盖前者；L0 永远最后追加，保证输出可解析（延续现在 `prompt/builder.py:162-165` 的正确做法，但要分层可组合，而不是整段替换）。

### 2.8 成本与用量记账

```python
# booktran/models/usage.py
@dataclass
class Usage:
    role: str; profile: str; model: str; stage: str; lang: str
    prompt_tokens: int = 0; completion_tokens: int = 0
    cached_read_tokens: int = 0; cache_write_tokens: int = 0
    requests: int = 1; retries: int = 0; latency_ms: int = 0
    cost: float = 0.0            # 按 profile 价格表计算；未定价标 None
```

- 落 `usage.jsonl`（append，事件式），聚合视图：按 阶段 / 角色 / 模型 / 目标语言 / 时间段。
- 价格表来自 profile（`price_in/price_out`）；未配置价格的模型显示 `—` 而不是猜（当前 `pipeline.py:211` 用字符估算回填 token，会让账目不可信 —— v2 里估算与实测分列两栏）。
- 预算：`[budget] max_cost/max_tokens`，超限自动暂停并提示，不静默烧钱。
- CLI `booktran cost -p <proj>`；Web 端成本面板（累计/按角色/按语言/实时速率）。

### 2.9 排版系统：策略抽象 + 随翻随排版

**策略：**

```python
class LayoutPolicy(Protocol):
    name: str
    async def plan(self, doc: DocIR, ctx) -> StyleSheet: ...   # 样式表
    def render(self, doc: DocIR, sheet: StyleSheet) -> Artifact: ...
```

| 策略 | 行为 |
|---|---|
| `original` | 沿用源样式与位置（现有 pdf_layout + EPUB 回写升级到 IR） |
| `model` | 模型产出 **StyleSheet**（标题层级/字号/段距/对齐/页边距/字体/注音/图注样式），再由渲染器落盘 —— 这就是"模型自己设计排版" |
| `hybrid` | 模型定样式表，位置/分页沿用源 |

`model` 策略的实现要点：把**排版意图**（不是整本书内容）交给 VLM/LLM，输出结构化 StyleSheet JSON，再由 HTML/CSS → EPUB3 / PDF 渲染器落盘。渲染器建议：EPUB3（zip 直写，保留源资源）+ HTML/CSS 预览 + PDF（WeasyPrint/Prince 或 paged.js 排版后打印）。

**随翻随排版：**

- 翻译完成一个批次 → 立即写回 `Block.targets` → **增量渲染**：EPUB 重打包、PDF 增量页、HTML 直接出。
- Web 端 `/preview` 路由渲染**当前进度**的成品（未译段落显示原文占位），SSE 推送增量。
- 预览与导出**共用同一个渲染器**，避免"预览好看导出走样"。
- 中间产物目录：`artifacts/preview/{lang}.{html,epub,pdf}`，`artifacts/final/…`。

### 2.10 存储与状态

```
<项目>/
├── booktran.toml     # 引用式配置（指针 + 差异）—— 替代 project.json 的配置快照
├── source/           # 源文件（原样留存）
├── state.json        # 全量状态快照（原子写）
├── events.jsonl      # 增量事件日志
├── usage.jsonl       # 用量/费用流水
├── lexicon.toml      # 术语 + 禁翻 + 文风 + 背景（从 glossary.json 迁出并分层）
├── prompts/          # 项目级提示词覆盖文件
└── artifacts/        # preview/ + final/
```

`Track` 升级为带阶段维度：

```python
@dataclass
class Track:
    lang: str
    status: Status                       # pending | translated | excluded
    stages: dict[str, StageState]        # translate/proofread/typeset 各阶段
    revisions: list[dict]                # 校对改写历史（可回退）
    model: str = ""; error: str | None = None
```

### 2.11 WebApp 形态

| 面板 | 内容 |
|---|---|
| **概览** | 阶段进度、吞吐（段/分钟）、token/费用实时、失败块 |
| **模型** | 多 profile 管理（增删改、连通性测试、价格表）+ 角色绑定 + 级联规则可视化 |
| **术语/背景** | 术语表（按语言分列，支持批量导入）、背景知识、提示词分层预览与 override |
| **校对** | 双栏 diff：原文 / 译文 / 问题标记（术语不符、复读、占位符丢失），支持逐条改写与"接受建议" |
| **排版预览** | 实时成品预览（HTML/PDF 翻页），`original / model / hybrid` 切换与重排 |
| **日志** | 请求级日志（模型、重试、耗时、token、费用） |

技术要点：
- **SSE** 替代轮询：`GET /api/project/{pid}/stream`，事件类型 `stage / segment / usage / log / artifact`。
- 预览与导出共用渲染器；预览 URL 可直接分享。
- 本机默认绑定 127.0.0.1；可选 token 鉴权（当前无鉴权是已知风险）。

### 2.12 CLI 形态（与 Web 同核）

```
booktran profile add|list|test          # 管理模型档案（全局）
booktran role set translate=claude-sonnet proofread=qwen
booktran init <src> --target-langs zh,en --layout original|model|hybrid
booktran run    -p <proj> [--stage translate|proofread|typeset|all] [--resume]
booktran status -p <proj> [--cost]
booktran preview -p <proj> [--serve --port]
booktran export -p <proj> --format epub|pdf|txt --layout original|model
booktran cost  -p <proj>
```

CLI 与 Web 共用 `engine.run_job()`；两者只是不同的 driver（现在是 `cli.py` 与 `web/app.py` 各自拼装流程，`web/app.py:317-356` 与 `cli.py:132-145` 有重复逻辑）。

### 2.13 输入格式支持矩阵（v2 目标）

| 格式 | 读 | 识别通道 | 写 | 备注 |
|---|---|---|---|---|
| TXT | ✅ | — | ✅ | |
| EPUB | ✅ | — | ✅ | 升级到 Block IR，标签不再按位置套回 |
| PDF | ✅ | 文本层 / OCR / VLM 三通道 | ✅ | 通道可配，级联升级 |
| **MOBI/AZW3** | 新增 | — | ✅ | `kindleunpack` 优先，回落 Calibre `ebook-convert`（可选依赖） |
| **图片**(jpg/png/webp) | 新增 | OCR / VLM | ✅ | 输出 PDF/EPUB |
| HTML/MD | 可选 | — | ✅ | |

**OCR 通道设计**：

```
页面无文本层 → OCR 引擎(paddle/tesseract) → 置信度评估
             → 置信度低 或 结构化版面需要 → 升级 VLM
             → 两者都失败 → 该页标记 needs_review，进人工队列
```
（现在 `vlm.py:101-136` 是逐页串行调用 VLM，无并发、无置信度、无降级。）

### 2.14 CLI / Web UX 契约

UX 不是收尾润色，是需求的一部分（"透明可见""随翻随排版""校对与排版精修"都是界面行为）。
两份规范独立成文，作为实现的验收契约：

- **[docs/ux/cli.md](ux/cli.md)** —— 命令面、三种输出形态（TTY 实时 / 管道逐行 / `--json` NDJSON）、
  开跑前成本闸、签名交互"守场"、错误文案规范、退出码表、终端视觉系统、首次运行体检。
- **[docs/ux/web.md](ux/web.md)** —— 四个目的地（书架 / 工作台 / 模型 / 成本）、
  常驻作业条、签名交互"活页"（随翻随排版 + 蓝笔批注直接落在书页上）、
  视觉 token、状态清单、响应式、键盘与无障碍。
- **[docs/ux/review-webapp.md](ux/review-webapp.md)** —— 对 v0.4 现有 WebApp 的设计评审（实测证据）。
- **[docs/open-source.md](open-source.md)** —— 开源发布清单（README/CI/文档/资产缺口/发布流程）。

**与 v2 架构的对应关系：**

| UX 要求 | 依赖的架构能力 | 落在哪个版本 |
|---|---|---|
| 成本闸 / 费用常驻 | `usage.jsonl` + `budget` + 角色价格表 | v0.5 |
| 实时进度与费用推送 | 阶段化状态机 + SSE | v0.6 |
| 活页预览 | 文档 IR + `LayoutPolicy` + 增量渲染 | v0.8 |
| 蓝笔批注 | PROOFREAD 阶段的问题清单 + 回写 | v0.6 |
| 模型页（档案 + 角色 + 级联） | `profiles.toml` + `roles` + `ModelRouter` | v0.5 |
| `booktran doctor` | 配置解析器 + 后端连通性测试 | v0.5 |

---

## 三、目标代码结构

```
src/booktran/
├── config/
│   ├── defaults.py      # 内置默认
│   ├── profiles.py      # Profile / RoleSpec / 价格表
│   ├── resolver.py      # 五级优先级合并（引用式，不快照）
│   └── keyring.py       # 密钥解析（env / keys.toml / keyring）
├── models/
│   ├── base.py          # Capability / Request / Response / Backend 协议
│   ├── anthropic.py  openai_compat.py  ollama.py
│   ├── ocr_local.py   ocr_http.py
│   ├── router.py        # ModelRouter：角色 + 级联 + 降级
│   └── usage.py         # Usage / 价格计算 / 预算闸
├── ir.py                 # Block / Span / DocIR / StyleSheet
├── ingest/
│   ├── txt.py  epub.py  pdf.py  mobi.py  image.py
│   └── recognize.py     # OCR / VLM 通道 + 置信度 + 降级
├── stages/
│   ├── base.py          # Stage 协议 + StageState
│   ├── translate.py  proofread.py  typeset.py  analyze.py
├── engine.py            # run_job：阶段编排、并发、断点续跑
├── store.py             # state.json + events.jsonl + usage.jsonl
├── layout/
│   ├── policy_original.py  policy_model.py  policy_hybrid.py
│   ├── stylesheet.py       # StyleSheet 模型
│   └── render.py           # EPUB/HTML/PDF 渲染器（预览与导出共用）
├── quality/             # 硬校验 / 软校验 / 终检报告（接线）
├── cli.py
└── web/                 # FastAPI + SSE + 面板化前端
```

---

## 四、迁移路线（务实排序，每步都可独立发布）

> **进度（198 个测试全绿）**：
> - **v0.5 ✅**：`config/` 包 + `profiles.toml` 档案与角色 + 项目 `booktran.toml` 引用式配置 +
>   `ModelRouter`（角色/级联/降级）+ `usage.jsonl` 费用记账 + `doctor`/`cost`/`profile`/`role`/`run`
>   （开跑前成本闸 + NDJSON）。网页端同步切到引用式配置，补齐「停止 / 续跑 / 导出下载 / 费用可见 /
>   单一写入口 / a11y」。
> - **v0.6 ✅**：阶段化（`Track.stages` + `stages/` + `engine.run_job`）、PROOFREAD 阶段
>   （术语/未翻译/占位符/长度/编号/禁翻 → 问题清单 + 报告）、SSE 事件流、
>   CLI `--stage` / `proofread` / `report`、网页端「校对」面板。
> - **v0.7 ✅**：文档 IR（`ir.py`：kind/level/spans/anchors/style/src_lang）、adapters 回填 IR 字段、
>   **段级语言检测**（`langdetect.py`，含简繁判定；切块按语言分组，一个块只讲一种语言）、
>   **OCR 通道**（`ocr.py`：本地引擎 → 置信度不足自动升级 VLM）、**MOBI 读取**（`adapters/mobi.py`）。
> - **v0.8 ✅**：排版引擎（`layout/`：original / model / hybrid 三策略 + 样式表 + 书页 HTML 渲染器）、
>   随翻随排版（每轮刷新 `artifacts/preview/`）、CLI `preview`、网页端「预览」活页。
> - **v0.9 ✅**：前端重做为 **Vite + React + TypeScript**（`web/`）：四个一级目的地
>   （书架 / 工作台 / 术语 / 模型 / 成本）+ 常驻作业条 + ⌘K 命令面板 + SSE 事件流 +
>   工作台三栏（目录·阶段 | 活页 | 校对）。设计令牌集中在 `web/src/tokens.css`，
>   与 `docs/ux/web.md` §7 一一对应；旧的单文件界面已删除（未构建时给出可操作的提示页）。
>
> - **v1.0 ✅**（收口）：开源发布（LICENSE/CONTRIBUTING/COC/SECURITY/CHANGELOG/CI/issue模板），
>   补齐 3 个真实缺口：**图片输入**（jpg/png/webp/tiff/bmp → OCR 通道）、
>   **领域预设**（宗教/法律/医学/学术/技术/文学，`--domain`）、**修订回退**（revert API + 前端按钮）。
>   ruff 硬门禁清零；wheel 打包含前端 dist；仓库地址更新为 `github.com/joei4cm/booktrain`。
>
> 详见 `README.md` 与 `docs/ux/cli.md`、`docs/ux/web.md`。

| 版本 | 内容 | 解决的痛点 |
|---|---|---|
| **v0.5** ✅ | **配置层重构**：`profiles.toml` + 角色路由 + 引用式 `booktran.toml`；删掉 `project.json` 配置快照；密钥收敛到全局；`make_provider` → `make_backend` + `ModelRouter`；`usage.jsonl` 费用记账；`booktran cost`；**`booktran doctor`** | P0-1、P0-2、P0-3（**最关键的一步**） |
| v0.6 ✅ | 阶段化：`Track.stages`；PROOFREAD 阶段（qa/llm）+ 终检报告；SSE 推送；CLI `--stage`/`proofread`/`report`；网页端校对面板 | P0-4、P2-1 |
| v0.7 ✅ | 文档 IR + MOBI 读取 + OCR 通道与级联 + 段级语言检测；CLI 输出契约（TTY/管道/`--json`）+ 退出码 + 成本闸 | P1-1、P1-2、P1-3、P2-4 |
| v0.8 ✅ | 排版引擎：`LayoutPolicy{original,model,hybrid}` + StyleSheet + 随翻随排版 + 活页预览 | P0-5、P0-6 |
| v0.9 ✅ | 前端重做（Vite + React + TypeScript）：五区工作台、视觉 token、命令面板、SSE、无障碍与响应式 | P2-2、P2-5 |
| v1.0 | 级联路由（段级选模）、提示词五层化、修订历史与回退、并行多厂商；**开源发布（README/CI/文档/资产）** | P1-4、P1-5 |

> v0.5 是**必须先做**的一步：只要"一个任务一份配置"还在，多模型、费用、级联、角色路由全部无从谈起 —— 后面几步都会被迫在错误的抽象上打补丁。

---

## 五、验收清单（逐条对照需求）

| 需求 | v2 落点 | 验收方式 |
|---|---|---|
| 配置一个或多个大模型（VLM/OCR/LLM） | `profiles.toml` 任意多个 + `roles` 绑定 + 级联规则 | `booktran profile list` 显示多档案；一次作业内 translate/vlm/ocr 走不同模型且日志可见 |
| many-to-many：智能检测源语言 | 段级 `Block.src_lang`（脚本 + CLD3 + 可选 LLM 复核） | 混排测试书逐段语言正确 |
| 用户选输出语言 | `target_langs` 列表，轨道并行，per-lang 进度/重试/导出 | 现有 many-to-many 测试 + 成本按语言分摊 |
| TXT/PDF/EPUB/MOBI | MOBI reader（kindleunpack/Calibre）+ 图片输入 | 4 格式端到端用例 |
| 图片需 OCR 或 VLM | OCR 引擎 + VLM 双通道，置信度级联升级 | 扫描页样本，OCR 置信度低自动升级 VLM |
| 默认提示词 + override | 五层提示词（base/domain/background/lexicon/run），协议段锁定 | 覆盖任一层不影响输出协议 |
| 背景知识范围 | `[background]` 独立段，支持 preset + 自定义 | 宗教/法律样本提示词快照比对 |
| token / 费用 / 进度 / 自动重试 | `usage.jsonl` + 预算闸 + 实时面板；请求级退避重试（已有） | `booktran cost` 与面板数字一致 |
| 随翻随排版 | 批次完成即增量渲染；`/preview` SSE 推送 | 翻译中预览持续增长 |
| 翻译后校对 + 排版精修 | PROOFREAD 阶段（qa / llm）+ TYPESET 精修 | 校对产出 diff 与问题清单 |
| 参照原排版 or 模型设计排版 | `LayoutPolicy` 三策略 | 同一本书三种策略出三份产物对比 |
| **禁止"一个任务一个配置"** | 项目只存引用 + 差异；全局档案变更即时生效 | 改全局模型 → 所有项目下次运行即生效（自动化测试） |

---

## 六、风险与未决

1. **MOBI 解析质量**：MOBI 有 PalmDOC/MOBI6/压缩流多种变体，纯 Python 解包对部分加密/DRM 文件无解 —— 需明确"支持未加密 MOBI"，DRM 情况给出清晰错误而不是静默失败。
2. **模型设计排版**的效果上限取决于 VLM 的版面理解能力；StyleSheet 必须有 schema + 校验 + 失败回退到 `original`，不能让模型输出把书排坏。
3. **增量渲染的一致性**：预览与导出共用渲染器可消除不一致，但 EPUB 重打包在超大书上仍有性能压力 —— 需要按章增量写 zip。
4. **费用准确性**：价格表会变，需支持价格表热更新与"按请求时间取价"，避免历史账单被新价格改写。
5. **阶段状态与断点续翻的交互**：多阶段后"重跑 translate"是否作废下游 proofread/typeset 状态，需明确定义（建议：上游变更则下游标记 `stale`）。
