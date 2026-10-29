# CLI UX 规范（v2）

> 模式：**Operate**（工具界面）。读者 = 正在完成一件长任务的人。
> 依据：`PRODUCT.md` 的产品事实、`docs/design-v2.md` 的架构约束。
> 本文件是实现契约：命令行表面、输出格式、退出码、错误文案都在此定死。
>
> **实现状态（v0.8）**：已实现——`doctor` / `init` / `run`（`translate` 别名）/ `status` / `cost` /
> `export` / `analyze` / `proofread` / `report` / `preview` / `profile` / `role` / `serve`；
> TTY 实时单行进度、结算块、开跑前成本闸（`--dry-run` 试算、`--yes` 跳过）、
> `--json` 的 NDJSON 事件流（stdout 纯机器可读，人类输出走 stderr）、
> `--stage translate|proofread|all`、退出码 0/2/6/7/8。
> 未实现：退出码 3/4/5 的细分归类、`--json` 事件字段的正式冻结（v1 前稳定）。

---

## 0. 一句话设计论点

**CLI 面对的不是"跑一条命令"，而是"守一场要跑几小时的活"。**
所以终端里常驻三件事：**到哪了、花了多少、能不能停**。
其余一切（彩色、进度条、阶段条）都为这三件事服务。

当前 v0.4 的做法（`src/booktran/pipeline.py:39-48`：每完成一个块就 `print` 一行）
在 3000 段的书上会刷出 3000 行——这是本规范要消灭的第一件事。

---

## 1. 命令面

```
booktran <group> <command> [flags]
```

| 命令 | 作用 | 首次使用者会用到吗 |
|---|---|---|
| `booktran doctor` | 环境体检：依赖、密钥、模型连通性、配置合法性 | ✅ 第一个命令 |
| `booktran init <src>` | 建项目（选源文件 → 选目标语言 → 选排版策略） | ✅ |
| `booktran run [-p P]` | 执行/续跑（可指定阶段） | ✅ |
| `booktran status [-p P]` | 进度 + 花费（只读，随时可跑） | ✅ |
| `booktran preview [-p P]` | 打开/导出当前排版预览 | ✅ |
| `booktran export [-p P]` | 导出成品 | ✅ |
| `booktran cost [-p P]` | 花费明细（按阶段/角色/语言） | 深度用户 |
| `booktran profile ...` | 模型档案增删改查、连通性测试 | 深度用户 |
| `booktran role ...` | 角色绑定与级联规则 | 深度用户 |
| `booktran serve` | 启动本地 WebApp | ✅ |
| `booktran config ...` | 查看/初始化全局配置 | 深度用户 |
| `booktran version` | 版本 + 环境摘要 | 提 issue 时必用 |

**v0.4 的 `translate` 改名为 `run`**，因为它现在跑的是阶段流水线而不只是"翻译"。
保留 `booktran translate` 作为隐藏别名（打印一行 deprecation 提示），避免文档与
外部脚本断裂。

### 全局 flag

```
-p, --project PATH     项目目录（缺省：向上查找 booktran.toml）
    --json             机器可读输出（NDJSON 事件流）
    --no-color         等价 NO_COLOR=1
    --quiet / -q       只输出错误
    --verbose / -v     额外诊断
    --traceback        崩溃时打印完整栈（默认不带）
```

---

## 2. 输出契约：三种形态，永不混用

| 环境 | 形态 | 规则 |
|---|---|---|
| TTY + 人类 | **实时覆盖**（单行原地刷新） | 进度不刷屏；阶段切换时另起一行留痕 |
| 非 TTY（管道 / CI） | **逐行事件** | 每行一个事件，禁止原地刷新（\r） |
| `--json` | **NDJSON** | 每行一个 JSON 对象，字段稳定，机器消费 |

非 TTY 下的输出示例：

```
{"type":"job.started","project":"booktran-银色天空","stages":["translate"],"target_langs":["chinese_simplified"]}
{"type":"stage.started","stage":"translate"}
{"type":"segment.done","sid":"a3f#812","lang":"chinese_simplified","model":"claude-sonnet-4-5","prompt_tokens":3912,"completion_tokens":140,"cost":0.0117,"elapsed_ms":2140}
{"type":"cost.update","prompt_tokens":812340,"completion_tokens":221450,"cost":3.42,"currency":"USD"}
{"type":"stage.finished","stage":"translate","done":2417,"pending":0,"failed":0,"cost":3.42}
{"type":"job.finished","status":"ok","exit_code":0}
```

NDJSON 的字段是**公开契约**：字段名与语义在 v1 之前保持稳定，新增字段向后兼容。

---

## 3. 签名交互：守场

`booktran run` 的 TTY 输出（实时覆盖单行）：

```
⠿ 翻译  简体中文  1,284/2,417 段  ▕██████████▏ 53%   $2.14   41.2k tok/min   剩余 ~8m32s
```

同一时刻，**进度、token、美元、速率、ETA 在同一行**，不用上下找。
阶段切换时把旧行留下（不擦除），形成一条时间线：

```
  识别  扫描页 12/340  ▕██▏ 4%   $0.09   paddle-ocr
  翻译  简体中文 1,284/2,417  ▕██████████▏ 53%   $2.14   41.2k tok/min   剩余 ~8m32s
```

结束时打印**结算块**（不是一句"完成"）：

```
✓ 翻译完成 · 2,417 段 · 失败 0 · 用时 18m04s

  token    输入 812,340   输出 221,450
  费用     $3.42   (claude-sonnet-4-5 $3.31 · claude-haiku $0.11)
  预算     $5.00 上限内 · 本次占 68%

  预览     booktran preview -p booktran-银色天空
  导出     booktran export -p booktran-银色天空 --format epub
```

**ETA 置信度**：样本不足 20 段时显示 `剩余 ~?`（而不是编一个数字）。
速率用最近 60 段的滑动窗口，不是全局平均。

### 中断语义

`Ctrl-C` = **优雅暂停**，不是杀死：

```
^C
已暂停 · 已保存 1,284 段译文 · 花费 $2.14
再次运行 booktran run -p booktran-银色天空 即可从断点继续
```

第二次 `Ctrl-C` 才立即退出（先给一次提示："再按一次 Ctrl-C 强制退出"）。

### 预算闸

超过 `[budget] max_cost` 时，**不是静默停止**：

```
⛔ 预算已达上限 $5.00（已用 $5.00 / 本次 $2.14）
   翻译已暂停在 1,284 段。要继续请提高上限：
     booktran config set budget.max_cost 10
   或改用更便宜的档案：booktran role set translate=qwen-local
```

---

## 4. 开跑前的成本闸（公开用户的第一道信任门）

任何会调用 API 的命令，**在第一次请求之前**必须先给出估算并等待确认：

```
即将开始翻译《银色天空》

  源       日语（日文）      目标   简体中文
  规模     2,417 段 · 约 412k 字符
  模型     claude-sonnet-4-5  (profile: claude)
  预计     $2.40 – $4.80   （块预算 4k token，按 3.1× 输入/输出比估算）
  预算     $5.00 上限

  [Enter] 开始    [c] 改配置    [q] 退出
```

- 估算区间来自实际 token 统计（不是拍脑袋），并且**在标题里标明这是估算**。
- 有实测数据时（例如已翻过一部分），用实测替换估算区间。
- `--yes` 跳过确认（CI 用），`--dry-run` 只跑估算。
- 估算不可信时（模型未配置价格），显示 `费用未知（该档案未配置价格表）`，
  **不猜**。

---

## 5. 错误文案规范

**规则：说清问题 + 给出下一步。绝不裸奔 HTTP 状态码，绝不给用户看栈。**

| 场景 | 反例（v0.4 现状） | 正例 |
|---|---|---|
| 没读到 key | `HTTP 401: {"type":"error",...}` | `API key 未找到（profile: claude）` + `设置 ANTHROPIC_API_KEY，或运行 booktran profile add claude --key-env ANTHROPIC_API_KEY` |
| 档案不存在 | `接口档案 'claude' 不存在（platforms.toml 中定义了的: [...]）` | `模型档案 claude 未定义` + `可用档案：qwen-local, deepseek-v3` + `booktran profile list` |
| 源格式不支持 | `不支持的文件类型 .mobi（支持 .txt/.epub/.pdf）` | `不支持 .mobi` + `当前支持 .txt .epub .pdf .mobi（v0.8 起）` + 明确的下一步 |
| 翻译块反复失败 | `缺少 3 段译文: [12, 13, 14]` | `第 2 轮重试仍失败（3 段缺译）` + `已跳过这 3 段，其余 2,414 段已保存` + `booktran status -p … --failed` 看详情 |

格式规范：

```
<一句话说清发生了什么>

  <怎么修：一条可粘贴的命令，或一个明确的操作>

  细节：<原始错误，单行、截断到 200 字符>
```

- 最后一行"细节"可省略，但错误**第一行必须是人话**。
- 退出码说明原因（见下表），不用错误文案重复解释。

---

## 6. 退出码

| 码 | 含义 | 典型触发 |
|---|---|---|
| 0 | 成功 | |
| 1 | 通用失败 | 未归类异常 |
| 2 | 用法错误 | 参数不合法、未知子命令 |
| 3 | 配置错误 | 配置文件无法解析、档案未定义 |
| 4 | 鉴权失败 | 401 / 403 |
| 5 | 限流耗尽 | 429 重试到上限 |
| 6 | 预算超限 | 触发 `[budget]` |
| 7 | 部分成功 | 有段落失败但整体可交付 |
| 8 | 用户中断 | Ctrl-C 优雅暂停 |

CI 里 `7` 与 `8` 是"值得续跑"的状态，脚本应该重试而不是当失败丢弃。

---

## 7. 视觉系统（终端）

**符号**：统一一套，不混用 emoji。

| 语义 | 符号 | 颜色 |
|---|---|---|
| 成功 | `✓` | green |
| 失败 | `✗` | red |
| 警告 | `!` | yellow |
| 跳过 | `–` | dim |
| 运行中 | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`（braille spinner） | accent |

颜色遵守 `NO_COLOR`、`TERM=dumb`、非 TTY 三重降级；`--no-color` 强制关闭。
颜色仅用于**状态与强调**，正文保持终端默认前景色。

```
✓ 2,417 段已翻译        ! 3 段待复核        ✗ 连接失败        – 已排除
```

不用 `⚠`/`📦`/`✅` 这类 emoji 当图标（终端 emoji 宽度不可控，跨平台渲染不一致）。

**宽度自适应**：按 `COLUMNS`（默认 80）折行；窄于 60 列时进度行退化为两行：

```
⠿ 翻译  简体中文  1,284/2,417 段  53%
  $2.14   41.2k tok/min   剩余 ~8m32s
```

---

## 8. 首次运行（`booktran doctor`）

公开用户的第一条命令必须是**一次就能定位问题**的体检，而不是让用户读 README 猜。

```
$ booktran doctor

  booktran 0.5.0 · Python 3.12.4

  ✓ 依赖      httpx, beautifulsoup4, pymupdf, fastapi
  ! 密钥      ANTHROPIC_API_KEY 未设置（档案 claude 将无法使用）
  ✓ 模型档案  claude (anthropic/claude-sonnet-4-5) · qwen-local (openai/qwen2.5:7b)
  ✗ 连通性    claude: HTTP 401 — key 无效或已过期
  ✓ 配置      ~/.config/booktran/profiles.toml
  ✓ 工作区    /home/you/booktran-projects（可写）

  2 个问题。修复后重跑 booktran doctor。

  快速开始
    booktran init 我的书.epub --target-langs chinese_simplified
    booktran run -p booktran-我的书
```

`doctor` **只读不写**，且不调用会产生费用的模型（连通性测试用 1 token 的最小请求，
并且只对"用户指定了 `--test`"时才发）。

---

## 9. 断点续跑的语义

- 状态按 **(段 × 目标语言 × 阶段)** 记录，`run` 默认跳过已完成的组合。
- 重跑只补缺失：`booktran run` 输出第一行即说明"续跑：1,284 段已完成，本次需补 1,133 段"。
- `--restart-stage translate` 强制重跑某阶段，并把它下游的阶段标记为过期（不是删数据）。
- 改配置（换模型、改提示词）**不会**作废已有译文，但会在 `status` 里标注
  "配置已变更，已有译文的模型与当前档案不一致"。

---

## 10. 键盘与自动化

- 全命令支持 `--json`，输出 NDJSON，字段见 §2。
- `booktran status --json` 输出单个 JSON 对象（便于 `jq`）。
- 退出码语义化（§6），便于 CI 分支处理。
- 支持 `BOOKTRAN_CONFIG` 环境变量指定配置文件路径。
- 支持 `BOOKTRAN_NO_PROGRESS=1` 关闭进度条（CI）。
- 不使用交互式 TUI：所有交互（确认、选择）都有等价的非交互 flag
  （`--yes` / `--target-langs` / `--layout`），保证管道可用。

---

## 11. 验收清单

- [ ] 3000 段的书，`run` 的终端输出不超过屏幕高度的 2 倍（不刷屏）。
- [ ] 任意时刻肉眼可见：进度 %、已花金额、token、ETA、剩余时间。
- [ ] 第一次调用 API 前必然出现成本估算与确认，`--yes` 可跳过。
- [ ] 任意错误的**第一行**是人话，且给出可执行的下一步。
- [ ] Ctrl-C 一次是优雅暂停，二次是强制退出，二者都不丢数据。
- [ ] `--json` 输出的字段名在 v1 前稳定。
- [ ] `NO_COLOR=1` 与非 TTY 环境下输出依然可读、无残留控制字符。
- [ ] `booktran doctor` 在"全新机器 + 只有一个 key"的场景下能一次定位所有阻塞项。
- [ ] 退出码覆盖 §6 全部取值，且与文案一致。
- [ ] 所有交互都有非交互等价物。
