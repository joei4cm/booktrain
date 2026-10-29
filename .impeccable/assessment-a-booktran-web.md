# Assessment A · booktran Web 端设计评审（独立设计评审，未运行探测器/未启动应用）

## 1. Design specificity verdict

**结论：数据模型是 booktran 的，界面不是。**

陌生用户会一眼认出「GitHub 风格的 admin/dashboard」——配色逐字取自 GitHub Primer：`#f6f8fa` 头/侧栏、`#0969da` 强调色、`#1f883d` 主按钮、`#cf222e` 错误色（index.html:9-11, 14, 21-22, 28, 44）。但他不会认出 booktran：全页没有书名、封面、章节或「页」的概念，书名只以目录名形式出现在侧栏（index.html:268 ← app.py:546-554 `unique_dir` 生成 `booktran-<名>-2`）。

真正属于本产品的只有三处，这是唯一的特异性来源：
- 段落表的「每语言一条轨道」：一行内每个目标语言一个编辑框（index.html:410-421），别家产品用不上这张表；
- 术语表的 `dst_<lang>` 覆盖列与「回落默认」占位（index.html:377-382）；
- 翻译背景的 10 个书籍领域字段：书名/题材/时代/命名规则（index.html:210-212, 353-361）。

然而 PRODUCT.md 明说工作台「不是仪表盘，是**一张摊开的书页**」（PRODUCT.md:64），当前 UI 恰恰是它所否弃的那种仪表盘：原文与译文是两个表格单元格里的 12.5px 无衬线编辑框（index.html:43, 414），不是可排版的书页；PRODUCT.md 要求预览正文用宋体/Noto Serif CJK（PRODUCT.md:134-135），界面里没有任何预览面。**像素层面 category-interchangeable，schema 层面 authored。**

## 2. Heuristics scoring table

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | 进度/日志是真的（index.html:296-299, 490），但侧栏百分比永不刷新（`loadProjects` 只在初始化/上传/删除时调用：index.html:258, 463, 508），头部 `#srcinfo` 只在选中项目时写一次（index.html:279），运行中无任何头部位状态；`task.state` 裸露英文枚举且只在概览页（index.html:294） |
| 2 | Match System / Real World | 2 | 中英混排：段落状态显示 `translated/partial/pending/excluded`（index.html:420），任务状态 `idle/running/done/error`（index.html:294），provider 下拉里混着 `fake`（index.html:132），prompt 预设叫 `local`（index.html:143）——全部靠 README 解释 |
| 3 | User Control and Freedom | 1 | 一次可能跑几小时、花用户钱的任务**没有停止按钮**（app.py:597-767 无 cancel 端点），段落编辑无 undo，导出无撤销；唯一确认框是删除项目（index.html:460） |
| 4 | Consistency and Standards | 2 | 一套视觉词汇尚可，但三处不一致：保存设置有 try/catch（index.html:344-349）而保存背景/术语没有（index.html:363-397，失败静默）；`partial` 与 `translated` 共用同一个绿色 tag（index.html:419）；`shortLang` 只映射 10 种语言（index.html:234），其余显示原始码 |
| 5 | Error Prevention | 1 | 目标语言是自由文本、零校验（index.html:86, 155），而代码里明明有一份 19 语言的 `LANGS`（index.html:214-216，死代码）；「开始翻译」花钱无预估、无预算、无确认（index.html:436-439）；导出恒为 `bilingual:true` + `format:"txt"`（index.html:440-443），PDF 源默认产出 txt |
| 6 | Recognition Rather Than Recall | 2 | 标签页/按钮都有中文名，但用户必须凭记忆知道语言码、provider 名、prompt 预设；术语分析按钮远在「概览」页（index.html:122） |
| 7 | Flexibility and Efficiency of Use | 1 | 全站 0 个 `tabindex`、0 个快捷键；术语表不能新增/删除行；每页固定 50 段不可调（index.html:208）；导出不能选语言/格式，而 CLI 有 `--lang/--format/--platform`（README:120-163） |
| 8 | Aesthetic and Minimalist Design | 3 | 视觉噪音低、配色克制、一个主按钮；扣分在层级扁平：概览页 8 行 KV + N 行进度 + 文档表 + 4 个并排按钮权重相同（index.html:113-124） |
| 9 | Error Recovery | 2 | 报错文本具体（"上传失败: 不支持的文件类型 .docx"），但只说问题不给解法；两个保存路径无 catch → 静默失败（index.html:363-397）；toast 2.5s 后消失且无 `role=status`（index.html:68, 228-231） |
| 10 | Help and Documentation | 1 | 界面内无帮助入口、无 tooltip；只有两处 `<small>`（index.html:167, 383），其中术语空状态还把用户赶去手改 `glossary.json`（index.html:383），与「WebApp 是首次使用者的入口」（PRODUCT.md:32）相悖 |
| **Total** | | **17/40** | **Poor（12-19）：核心承诺未被界面兑现** |

Heuristics 7 与 10 按 Operate 模式计分，未标 n/a。

## 3. Cognitive load

**清单 8 项中失败 7 项 → 高认知负荷（临界）。**

| 项 | 判定 | 依据 |
|---|---|---|
| Single focus | ✗ | 5 个标签页 + 2 个常驻头部动作 + 侧栏上传，互相争夺注意 |
| Chunking (≤4/组) | ✗ | 「翻译背景」一组 10 个字段（index.html:210-212）；「模型服务」9 个控件（index.html:129-150） |
| Grouping | ✓ | 设置页 3 个 h2 分组 + `.grid2` |
| Visual hierarchy | ✗ | 头部两按钮与标签页同级；概览页四按钮同权重 |
| One thing at a time | ✗ | 「开始翻译」常驻任意标签页顶部；一份 glossary 有三个保存按钮 |
| Minimal choices (≤4) | ✗ | 见下表 |
| Working memory | ✗ | 侧栏语言字段（index.html:86）与设置页语言字段（index.html:155）并存且不同步，用户无法判断哪个生效；改完设置必须记得点「保存设置」才生效，无脏标记 |
| Progressive disclosure | ✗ | 10 字段背景表单恒展开；术语分析按钮不在术语页 |

**可见选项 >4 的决策点：**
1. 「模型服务」：provider / model / base_url / api_key / platform / prompt 预设 / 并发 / RPM / 最大输出 tokens = **9 个同时可见**（index.html:129-150）
2. 「翻译背景」：**10 个**（index.html:210-212, 353-361）
3. 上传目标语言：**19 种语言的自由文本**（index.html:86 vs 214-216）
4. 术语表：每行 `2 + N` 个输入（3 语言即 5 个），且固定 `width:90px`（index.html:380-381），中文译名放不下
5. 概览页动作行：4 个按钮混排，删项目与「术语分析」「VLM 重读」并排（index.html:119-124）

## 4. Emotional journey

**Peak-end 是失败的**：全书最贵的一刻（几小时 API 花费）结束时**零确认**——无完成 toast、无头栏变化、无桌面通知；轮询只更新概览页（index.html:470-483），用户若停在「段落」页会以为还在跑（`loadProjects` 从不轮询，侧栏百分比全程冻结）。

**Valleys（按痛苦排序）：**
1. **花钱前的空洞**：点「开始翻译」前没有估算、没有已花、没有预算；概览只显示 token（index.html:293），且 `Stats` 结构里根本没有金额字段（model.py:157-175），CLI `status` 也只打 token 不打钱（cli.py:250-251，而 README:29 宣称「查看进度/成本」）。
2. **不知道在翻哪本书**：头部两个按钮对选中项目生效，但头部只显示 `provider/model · 源 auto`（index.html:279），各项目完全相同；侧栏显示的是目录名。用户无法在按下花钱按钮前确认对象。
3. **跑完之后按钮卡死**：`disabled` 只在 `select()` 里按状态设置（index.html:280），轮询不复位（index.html:470-483），`disabled` 全部出现处仅 253/261/280/438 —— 一次运行结束后「开始翻译」永久禁用，必须手动点两次项目才恢复。续跑（PRODUCT.md:124）实际被 UI 堵死。
4. **静默吞掉编辑**：术语页改了译名 → 切到设置页点「保存背景」→ 覆盖的是 `glossaryCache`（index.html:364-368），未保存的术语 DOM 编辑被静默丢弃，无任何提示。
5. **无退出的长跑**：模型选错、提示词写坏，只能杀进程；`start_translate` 在任务运行时返回 `{"ok":false}`，而前端忽略 `r.ok`（index.html:436-439），照样提示「翻译已启动」——**假成功反馈**。

**高风险时刻的安抚**：整个应用只有一个 `confirm()`，用在删除项目上（index.html:460）。真正花钱的三个动作（开始翻译 / 术语分析 / VLM 重读，其中 VLM 是全书页图多模态调用）和「导出未完成的书」全部零确认、零预估、零预算。

## 5. What's working

1. **长任务的反馈骨架是真的，不是装饰**：每语言进度条带 `translated/total（%）`（index.html:296-299）、日志按轮次/块数/待翻数汇报（index.html:490）、后端 `Progress` listener 真实落事件（app.py:358-361）、3s 轮询刷新概览。对于一个跑几小时的作业，这是正确的骨架。
2. **多语言轨道编辑模型是为本产品写的**：一行一段，每目标语言一个编辑框 + 每语言状态（index.html:410-421），术语表用 `dst_<lang>` 覆盖列把「回落默认」规则摆到台面上（index.html:377-382）。这两个界面别家产品确实用不上。
3. **翻译背景表单是唯一说人话的地方**：把 `glossary.json` 的技术字段映射成「书名/题材/时代/命名规则/目标读者」等 10 个书籍领域标签（index.html:210-212, 353-361），并说明「随每次请求注入提示词」——这正是 PRODUCT.md:176 承诺的那个产品概念。

## 6. Priority issues

**[P0] 钱完全不在视野里**
- *为什么重要*：这是产品第一原则（PRODUCT.md:38-40, 120-121）与头号决策场景（PRODUCT.md:65-66）。用户按下按钮时无法回答「这本书花了我多少钱」，事后也永远无法回答。而 `Stats`（model.py:157-175）与 `summary()`（app.py:113-154）都没有金额字段，概览页只显示 token（index.html:293）——需要从引擎层补价格表。
- *具体修法*：① config 增加 provider/model→单价表；② 开跑前算预估（段数 × token × 单价）并在按钮旁显示「预估 $X.Y / 上限 $Z」；③ 引擎侧硬闸，超限自动停；④ 头部常驻「已花 / 剩余」。
- *建议命令*：`/impeccable harden`

**[P0] 头部的两个全局动作既不指明对象也不给退路**
- *为什么重要*：「开始翻译」「导出」常驻右上（index.html:76-77）却不知道作用于哪本书；跑几小时不能停；跑完按钮永禁用（index.html:280, 438）；导出固定 bilingual+txt 且不告诉你文件落在哪（后端其实有 `/files`、`/file/{name}`，app.py:769-792，界面从不调用——README:3 宣称的「下载」在 UI 上不存在）。
- *具体修法*：头部加项目身份（书名 + 章节/来源文件）；运行中出现「停止」并调 `POST /api/project/{pid}/cancel`；任务结束时复位按钮并给完成 toast；导出改成一次显式选择（格式 / 双语 / 语言）并回链下载。
- *建议命令*：`/impeccable harden`（与上一条同批执行）

**[P1] 键盘与读屏用户被完全挡在门外**
- *为什么重要*：全站 `aria-` 0 处、`role=` 0 处、`tabindex` 0 处；16 个 `<label>` 无一有 `for=` 也不包裹控件（index.html:86, 131-162）→ 所有输入框对辅助技术无名称；上传区是 `div#drop` + `hidden` file input（index.html:84-85, 237），项目列表是 `li` 绑定 onclick（index.html:271）→ 键盘用户无法上传也无法选项目；tabs 是普通 button，无 tab 语义/无 `aria-selected`；toast 无 `role=status`（index.html:68, 203）→ 保存成功/失败永远不被播报；进度条是纯 div 无 `role=progressbar`（index.html:298）；标题层级 h1→h3→h2 断裂（index.html:73, 83, 114）；焦点环只给 input/select/textarea（index.html:18），button 用 UA 默认。
- *具体修法*：`for=`/包裹标签；`#drop` 改 `button` 或加 `role=button tabindex=0` + 键盘处理；项目项改 `<button>`；tabs 补 `role=tablist/tab/tabpanel` + `aria-selected` + 方向键；toast 加 `role=status aria-live=polite`；进度条加 `role=progressbar aria-valuenow`；表格补 `scope`/`caption`；`:focus-visible` 统一。
- *建议命令*：`/impeccable audit`

**[P1] 术语工作流是断的**
- *为什么重要*：PRODUCT.md:29 把「译者/编辑/校对者」列为一类核心用户，但术语分析按钮在「概览」页（index.html:122）；表格**不能新增/删除术语行**，备注列只读（index.html:382）——译者唯一能做的就是给机器生成的词补译名；空状态直接叫人去改 `glossary.json`（index.html:383）；「保存背景」与「保存术语」写同一个文件但互不知情（index.html:363-397），切页保存会静默吃掉未保存的术语编辑。
- *具体修法*：把「术语分析」移进术语页并加「新增术语行/删除行」；备注改可编辑；两个保存合并为一个「保存术语与背景」的单一写入口（或保存前做 dirty 检查）；空状态给出「分析 → 补译名 → 保存」三步引导。
- *建议命令*：`/impeccable clarify`

**[P2] 暗码与英文枚举外泄**
- *为什么重要*：19 种语言用自由文本输入（index.html:86, 155），代码里的 `LANGS`（index.html:214-216）是死代码；`fake` provider、`local` 预设、prompt 协议、`块预算 tokens`、`VLM`（index.html:88, 142-143, 150, 158）全无解释；`shortLang` 只映射 10 种（index.html:234），其余显示原始码。
- *具体修法*：语言改多选 select（数据源直接用 LANGS）；provider/预设各加一行说明；`块预算 tokens`→「每块输入上限（token）」；状态枚举映射成中文。
- *建议命令*：`/impeccable distill`

## 7. Persona red flags

**首次使用的公众用户**：落地只有「上传文件或选择左侧项目」（index.html:101），没有「先配 API key」的引导；必须先知道 `chinese_simplified` 这个码；`fake` 与 `openai` 并列无从判断；点「开始翻译」前不知道会花多少钱；术语空状态把人推向 `glossary.json`；书名在侧栏只是目录名。

**做术语工作的译者**：术语分析按钮在「概览」；不能新增/删除术语行；备注不可编辑；输入框固定 90px 宽放不下中文译名（index.html:380-381）；「默认译名」与各语言覆盖列的关系只能从占位符「回落默认」猜；切到设置页保存背景会静默丢弃未保存的术语编辑；`dst_<lang>` 这套覆盖规则（README:171）界面完全没有说明。

**CI 里的 power user**：无任何快捷键；导出不能选语言/格式；后端已有 `/files`、`/file/{name}`（app.py:769-792）却无 UI 入口；没有深链到某一本书（`offset` 只是内存变量，刷新即丢，index.html:208）；3s 全量轮询 summary，页面刷新后所有浏览位置归零。

**读屏用户**：前述全部 a11y 缺陷；额外：段落表的编辑框 placeholder 是原始语言码（index.html:414），读屏读出 `chinese_simplified`；进度/费用无 `aria-live` 播报（PRODUCT.md:130 明确要求），任务结束也不会被播报。

## 8. Minor observations

- 侧栏百分比永不刷新 —— 一行修复（轮询里调 `loadProjects`，index.html:470-483）。
- 翻译按钮忽略 `r.ok`（index.html:436-439）→「任务已在运行」也提示「翻译已启动」。
- `partial` 与 `translated` 同为绿色（index.html:419）——用同一 tag 区分不了「译了一半」。
- `main` 高度硬编码 `calc(100vh - 45px)`（index.html:27）而头部实际约 33px → 双滚动条。
- `#content{max-width:960px}`（index.html:36）在宽屏左侧留白，右侧大片空。
- 日志每 2s 用 `textContent` 整体重写（index.html:489）→ 滚动位置被重置。
- 概览每 3s 整块重渲染（index.html:287-301）→ 选中文字与滚动位置被抹掉。
- 无 `prefers-reduced-motion`（PRODUCT.md:132），唯一动效是进度条宽度过渡（index.html:52）。
- 控件不同族：`select/input` 与 `button` 的 padding/border-radius 不一致（index.html:17-19）；按钮只有 hover 与 disabled 态，没有 focus-visible/loading 态。
- 导出成功只给文件名，没有完整路径或「打开输出目录」。
- `<html lang="zh">` 应为 `zh-CN`（PRODUCT.md:88）。
- 没有深色模式——长时阅读场景可选，但不是主战场。

## 9. Provocative questions

1. 产品承诺是「看得见花的钱 / 随时能停 / 能续跑」，这三条恰好是界面最缺的三条。这个 Web 端到底是给**第一次花真钱的公众用户**用的，还是给开发者看状态的后台？两者需要的界面不是同一个东西。
2. 一个「书」的产品里，为什么**没有一处是书页**？把左右对照段落做成真正的双栏排版（宋体、行宽 65-75ch、可滚动书页）——这比再调十遍设置表单的样式，是否更接近差异化？
3. 「术语分析」是一次全书级 LLM 调用（花钱），现在和「删除项目」并排躺在「概览」页最不显眼的位置。一个花钱动作凭什么是最不需要思考的那个？
4. 19 种语言码自由文本输入，而代码里已有一份 `LANGS`（index.html:214-216）——这个界面是为「配置文件」设计的，还是为「要读一本书的人」设计的？
5. 如果 CLI 才是深度用户的归宿，Web 端是否应当明确只做三件事（选书 → 选语言 → 看进度），把模型/并发/RPM/块预算全部折进「高级」？
