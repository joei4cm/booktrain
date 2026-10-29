# AiNiee 架构研究与 booktran 实现方案

> 源码参考：`reference/AiNiee/`（shallow clone，2026-10-28）
> AiNiee 核心代码（ModuleFolders/）约 2.3 万行 Python，另有 PyQt 桌面 UI。

## 一、AiNiee 是什么

一款"拖入文件夹 → 一键翻译"的桌面 AI 翻译工具，覆盖游戏文本（Mtool/Ren'py/T++/Paratranz）、电子书（EPUB/TXT）、文档（PDF/Word/MD/PPT）、字幕（SRT/ASS/VTT/LRC）。支持 OpenAI 兼容 / Anthropic / Google / Bedrock / Sakura（本地模型）等接口。

## 二、核心架构

### 2.1 分层

```
UserInterface (PyQt) ──事件总线──┐
                                 ▼
ModuleFolders/
├── Base/EventManager        # 事件总线，UI 与核心解耦（TASK_START/TASK_UPDATE/...）
├── Config/                  # 配置 + 表格（术语表/禁翻表/替换表）+ 路径
├── Domain/
│   ├── FileReader/          # 25+ 个 Reader（epub/txt/docx/md/srt/renpy/...）
│   ├── FileOutputer/        # 对应 Writer（译文版 + 双语版）
│   ├── FileAccessor/        # 底层文件访问（epub zip 等）
│   ├── PromptBuilder/       # 提示词组装（986 行，最复杂的模块）
│   ├── TextProcessor/       # 译前译后替换、占位符保护、空白/换行规范化
│   ├── TextFilter/          # 无效文本过滤（纯数字/代码/语言不符）
│   ├── ResponseExtractor/   # 从模型回复中正则提取编号→译文
│   ├── ResponseChecker/     # 回复结构校验
│   └── TranslationResultCheck/  # 全书终检报告
├── Infrastructure/
│   ├── LLMRequester/        # 各平台请求器 + Client 工厂
│   ├── RequestLimiter/      # 全局 RPM + 单请求 token 上限
│   └── Tokener/             # tiktoken 计数
└── Service/
    ├── Cache/               # CacheManager：项目状态持久化（关键！）
    └── TaskExecutor/        # Translator/Polisher/Proofread/Analysis 四种任务
```

### 2.2 数据模型（精髓所在）

一切围绕三层缓存对象：

- **CacheItem**（最小翻译单元 = 一行/一段）：
  `text_index, source_text, translated_text, translation_status, model, lang_code(语言+置信度), extra{}`
  - `translation_status`: UNTRANSLATED / TRANSLATED / POLISHED / EXCLUDED / QUESTIONABLE
  - `extra` 存 reader 专属数据：epub 存 `original_html`（回写时做字符串替换）、txt 存 `line_break`（段间空行数）
- **CacheFile**：一个源文件的 items + encoding + 路径
- **CacheProject**：全部 files + project_id + 统计数据，即整个"翻译工程"

**持久化**：`ProjectCache/<uuid>/AinieeCacheData.json`，msgspec 序列化，临时文件 + `os.replace` 原子写；后台线程每 8s 保存一次 + 完成一块就 `require_save`。另存轻量 `ProjectStatistics.json` 供历史列表快速读取。

这套设计 = **断点续翻 / 换模型重译 / 增量翻译 / 崩溃恢复** 的全部基础。任务随时可停，重启后按 status 过滤出 UNTRANSLATED 继续。

### 2.3 翻译流水线

`TaskExecutor.translation_start_target()`：

1. **TextFilter** 过滤无效文本 → EXCLUDED（不消耗 API）
2. **轮次循环**（默认最多 10 轮）：
   - 取所有 UNTRANSLATED 条目，按 token（默认 1024/块，强模型可以开大）或行数（默认 10 行）切块
   - 每块预构建 `TranslatorTask`：规范化 → 译前替换 → 提取首尾空白/占位符/代码段（保护起来不让模型碰）→ PromptBuilder 组装
   - ThreadPoolExecutor（线程数按 RPM 自动推算）并发执行
   - **第 2 轮起块大小减半**（防单块反复失败死循环）
3. 单任务执行：RequestLimiter 过闸（RPM + 单请求 60w token 上限）→ LLMRequester 分发请求 → ResponseExtractor 正则提取 → ResponseChecker 校验 → **全部通过才写回缓存**（translated_text + TRANSLATED），否则整块丢弃下轮重试
4. 全部完成 → 可选 OpenCC 简繁转换 → Writer 输出（译文版/双语版）→ TranslationResultCheck 终检（禁翻残留/占位符残留/序号残留/示例复读/换行符数），输出错误报告

### 2.4 Prompt 协议（重点学习）

**请求是无状态的**——每个块独立请求，不做多轮对话。上下文靠把**上一个块的原文**以 `<previous>` 附在 user message 里。这是并发安全 + 容错的根基：任何块失败都可独立重试。

消息结构：
```
system: 基础指令（模板文件，{source_language}/{target_language} 占位）
        + ###术语表（仅注入当前块命中的词条）
        + ###禁翻表
        + ###角色表 / ###术语表(项目级) / ###禁翻表(项目级)
        + ###角色介绍 / ###背景设定 / ###翻译风格 / ###翻译示例
user:   [few-shot：从当前块文本动态生成的原文示例 → 译文示例]  ← 很巧的设计
        + <previous>上一块原文</previous>
        + ###这是你接下来的翻译任务 <textarea>
          1.第一段
          2.第二段
          2.[                 ← 多行文本用嵌套编号方案
          "2.3.,第二段第三行",
          "2.2.,第二段第二行",
          ]
          </textarea>
assistant(预输入): "我完全理解了翻译要求，以下是翻译:"
```

- 提示词模板存 `Resource/Prompt/Translate/*.txt`，三种预设：通用 / CoT 三步走（直译→校正→润色）/ 本地小模型专用
- **动态 few-shot**：把当前块的真实文本按语言正则替换成 `原文テキストA-1` 这类占位样本，教模型输出格式——不消耗真实翻译内容
- 提取端：`<textarea>` 内按 `\n(?=\d+\.)` 分块 + 多行嵌套编号正则解析，容忍各家模型怪癖（多余引号、deepseek 不支持预输入、思考标签）

### 2.5 术语一致性（两阶段全文分析）

`AnalysisTask`（翻译前跑一遍）：
- Stage 1：按 10k token 切块，并发让 LLM 提取 角色/术语/禁翻 候选（JSON 输出）
- Stage 2：按 source 聚合去重、短词挂靠长词，LLM 裁决合并成规范表
- 结果写入项目表（characters/terms/non_translate），翻译时**按块匹配注入**（只注入本块出现的词条，控制 token）

另有 NameExtractor 从游戏工程文件（.trans/.rpy）提取人名、ProofreadTask 表格校对。

### 2.6 EPUB 处理

- Reader：正则按块级标签（h1-7/li/p/section/blockquote/td/div 兜底）提取，BeautifulSoup 清洗；处理同名嵌套标签（平衡括号算法）；NCX 目录文本也提取
- Writer：翻译后 `str.replace(original_html, new_html, 1)` 回写，重建标签保留属性；双语版用带样式的原文标签（灰色斜体小字）；替换 `<html lang>` 与 OPF `<dc:language>`

### 2.7 可靠性设计清单（值得照抄）

- 状态机 + 原子缓存 = 幂等、可恢复
- 失败块不污染缓存（宁可不写，不可写错）
- 轮次 + 减半切分兜底
- 整任务级 request_timeout 防死循环
- 定时 + 按需混合保存策略

### 2.8 弱点（我们的机会）

1. **无请求级重试/退避**：429/网络错误直接算失败，靠下一轮兜底，浪费轮次和时间
2. **单文件大 JSON 持久化**：大部头全量序列化，书越大越慢；无增量
3. **正则文本协议脆弱**：ResponseExtractor 329 行正则对抗模型输出怪癖；用强模型 + JSON/structured output 可省掉一半
4. **线程池 + 全局可变状态**（Base.work_status 类变量做停止信号），难以测试
5. 上文只带原文不带已译译文，长篇译名漂移仍靠术语表硬扛
6. UI 与核心耦合在事件总线上，但核心仍假设单进程单项目

## 三、booktran 实现方案

### 3.1 定位

**书籍翻译 infra**：CLI-first、headless、可嵌入流水线、面向服务器长跑。聚焦 EPUB / TXT（后续 MD/PDF）。不追格式全能，不追 GUI。

参考同类：AiNiee-Next（社区 CLI 化重构）、ainiee-translate-skill（Agent 原生小说翻译）——验证了"核心 CLI + 状态缓存"这个方向。

### 3.2 架构

```
booktran/
├── pyproject.toml
├── src/booktran/
│   ├── model.py          # Segment / SourceDoc / TranslationProject + 状态机
│   ├── store.py          # 持久化：JSONL 追加式日志 + 快照（比单 JSON 抗损坏、可 git diff）
│   ├── chunker.py        # token/段落切块，不跨章节；附 previous context
│   ├── pipeline.py       # 轮次调度器（asyncio，而非线程池）
│   ├── llm/
│   │   ├── provider.py   # 协议：translate(batch) -> list[Segment]
│   │   ├── anthropic.py / openai.py   # 官方 SDK，JSON/structured output 优先
│   │   └── ratelimit.py  # 令牌桶 RPM + 并发信号量 + 指数退避重试(429/5xx/网络)
│   ├── prompt/
│   │   ├── templates/    # 系统 prompt 模板（沿用 AiNiee 三预设思想）
│   │   └── builder.py    # 术语表/禁翻表/风格/上文 组装
│   ├── quality/
│   │   ├── extract.py    # JSON 解析 → 失败降级到编号协议
│   │   ├── checks.py     # 行数/空译/残留原文/占位符/术语遵从
│   │   └── report.py     # 终检 HTML/MD 报告
│   ├── adapters/
│   │   ├── txt.py        # Reader+Writer（译文/双语）
│   │   ├── epub.py       # original_html 片段策略 + lang/OPF 回写
│   │   └── base.py       # Reader/Writer 协议
│   ├── glossary.py       # 两阶段全文分析（提取→聚合裁决）
│   ├── cli.py            # typer：init/translate/resume/analyze/report/export
│   └── cost.py           # token/费用统计
└── tests/
```

### 3.3 关键设计决策

| 决策 | 选择 | 理由（对照 AiNiee） |
|---|---|---|
| 并发模型 | asyncio + 信号量 | AiNiee 用线程池是历史包袱；IO 密集天然适合 async |
| 文本协议 | JSON（id→译文）优先，编号纯文本兜底 | 强模型下省掉大半 ResponseExtractor；兜底保本地小模型兼容 |
| 上下文 | previous chunk 原文 + 最近 N 条已定稿译文 + 术语表 | 无状态请求保并发；补译文上文减缓译名漂移 |
| 切块 | 按段落聚合到 token 预算（默认 4k），epub 以 spine 章节为硬边界 | AiNiee 默认 1024 太小（为本地模型设计） |
| 持久化 | JSONL 事件日志（append）+ 定期快照 | 大书增量写；崩溃最坏丢一条而非整档 |
| 重试 | 请求级指数退避（429/5xx/超时）×3，再交给轮次机制 | 补上 AiNiee 最大短板 |
| 状态机 | pending → translated / failed / excluded；failed 记录错误原因 | 便于报告与人工介入 |
| 术语 | 沿用两阶段分析 + 按块注入 + 命中检查 | 这是 AiNiee 质量的核心，直接复刻 |

### 3.4 数据模型草案

```python
@dataclass
class Segment:
    sid: str                  # 稳定 ID（文件路径+序号 hash）
    source: str
    target: str | None
    status: Status            # pending/translated/failed/excluded
    lang: str | None
    meta: dict                # epub: {"original_html":..., "item_id":...}; txt: {"line_break":2}
    error: str | None
    model: str | None

@dataclass
class SourceDoc:
    path: Path
    segments: list[Segment]

@dataclass
class Project:
    pid: str
    docs: list[SourceDoc]
    glossary: Glossary
    stats: Stats               # tokens、成本、进度
```

### 3.5 里程碑

- **M1 核心闭环**：TXT in → 切块 → LLM（JSON 协议）→ 断点缓存 → TXT out；CLI 四命令（init/translate/resume/export）；含退避重试与基本校验
- **M2 书籍格式**：EPUB reader/writer、双语输出、简繁转换
- **M3 质量**：两阶段术语分析、术语/禁翻/风格注入、术语遵从检查、终检报告（HTML）
- **M4 运营**：多 key 轮询并发、成本面板、章节预览、MD/PDF

### 3.6 风险与未决问题

- EPUB 破损输出：AiNiee 用字符串替换回写，同名片段可能错位——我们给 original_html 加序号校验，替换失败要报警
- JSON 模式下模型仍可能漏行：校验器兜底 + 单行重译（而非整块重试）可以省成本，值得做
- 译文上文引入的偏差（前一块被重译后上文过期）：采用"已定稿才进上文"策略
