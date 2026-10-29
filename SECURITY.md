# 安全策略（Security Policy）

本文件说明 booktran 的**支持范围**、**漏洞报告方式**、**响应时限**，
以及**本项目对数据与密钥的处置方式**。

- 仓库：<https://github.com/joei4cm/booktrain>
- 许可证：Apache-2.0

---

## 1. 支持版本

项目当前处于 **0.x** 阶段（开发中，接口仍可能变动）。按"只有最新 minor 收补丁"的策略：

| 版本 | 状态 | 是否接收漏洞修复 |
|---|---|---|
| 0.9.x | 当前最新 | ✅ 是 |
| 0.8.x 及更早 | 历史版本 | ❌ 否（请升级到 0.9.x） |

升级方式：

```bash
pip install -U booktran
booktran --version
```

> 0.x 阶段的版本策略：**每个 minor 都可能带来不兼容变更**（配置格式、CLI 参数、
> 存储结构）。请在自动化脚本里锁定具体版本号（`booktran==0.9.x`），
> 升级前先看 `CHANGELOG.md` 的"破坏性变更"小节。

---

## 2. 报告漏洞

> **请不要用公开 issue、公开 PR 或公开讨论报告安全漏洞。**
> 公开报告会让漏洞细节在修复前被所有人看到。

**首选渠道：GitHub Security Advisory 私密报告**

1. 打开仓库的 **Security** 标签页
2. 点击 **Report a vulnerability**（或直接访问
   <https://github.com/joei4cm/booktrain/security/advisories/new>）
3. 填写标题与描述，提交后你与维护者之间进入私密对话

这条通道只有你与维护者可见，直到维护者发布 advisory。

**备用渠道**：如果你的 GitHub 账号无法提交私密报告（例如被限制），可以邮件联系
`<维护者邮箱>`，标题写明"安全漏洞报告"。同样**不要**同时开公开 issue。

### 报告里请写清

- 受影响的版本（`booktran --version` 的输出）与 Python 版本
- 复现步骤或最小复现命令（优先用 `--fake` 假 provider 复现，不要用真实 key）
- 影响面：你判断它会造成什么（本地信息泄露、越权写入、命令注入、密钥落盘等）
- 是否可稳定复现、是否需要特定配置
- 你已知的缓解办法（如果有）

不需要提供真实密钥、真实书稿或含个人信息的日志。

### 响应时限（预期，非 SLA）

| 阶段 | 预期时间 |
|---|---|
| 确认收到 | 2 个工作日内 |
| 初步评估（是否受理、严重程度、修复方向） | 7 个工作日内 |
| 修复与发布 | 视严重程度而定；高危问题优先安排补丁版本 |
| 公开披露 | 修复发布后再公开 advisory（除正在被利用的紧急情况） |

修复会在 `CHANGELOG.md` 中记录。除非你要求匿名，否则会在 advisory 里致谢。

---

## 3. 永远不要在 issue / PR / 聊天里贴密钥

**这一条没有例外**：不要在 issue、PR、discussion、聊天里贴 API key，
也不要贴**含密钥的日志、截图或配置片段**。

需要提供日志时，先脱敏：

| 要贴的内容 | 脱敏做法 |
|---|---|
| `sk-…`、`sk-ant-…` 等 key | 整段替换为 `<REDACTED>`，保留前 3 位和后 2 位即可 |
| `~/.config/booktran/keys.toml` | **不要贴整份文件**，只贴键名（`[key.anthropic]`） |
| 环境变量导出 | `export ANTHROPIC_API_KEY=<REDACTED>` |
| 请求头 | `Authorization: Bearer <REDACTED>`、`x-api-key: <REDACTED>` |
| 带 token 的 URL | 去掉 query 里的 `key=` / `token=` / `api_key=` |
| 报错栈 | 检查参数里有没有内联 key（部分 SDK 会把 key 写进异常消息） |

贴之前请自己搜一遍：`sk-`、`api_key`、`Bearer`、`token`、`secret`、`password`。

如果**已经**贴出去了：立刻到对应厂商后台吊销（revoke）这个 key 并重新生成，
然后告诉维护者（`<维护者邮箱>`）以便清理历史。**已泄露的 key 视为已失效。**

---

## 4. 本项目如何处理数据与密钥

booktran 的设计前提是"**数据与密钥只在本机**"。具体地：

- **无遥测、无崩溃上报、无网络回传**。除了你主动调用的模型 API 之外，
  booktran 不向任何服务器发送数据。
- **项目数据全在本机**：书稿、`state.json`、`events.jsonl`、`usage.jsonl`、
  `glossary.json`、导出产物都写在你自己的项目目录里。
  `usage.jsonl` 只记录角色、模型、阶段、目标语言、token 数、费用与延迟，
  **不包含书稿正文**。
- **密钥只在本机**，来源只有三处：
  1. 环境变量（如 `ANTHROPIC_API_KEY`）
  2. `~/.config/booktran/keys.toml`（建议权限 `0600`）
  3. 模型档案里 `key_env = "…"` 指定的环境变量
  **项目目录永不写密钥**；网页端里填写的 key 也只写进全局 `keys.toml`。
- **上传的书稿留在本地**：`booktran serve` 的网页端会把上传的文件留在项目目录，
  不会转发到别处。
- **默认只监听 `127.0.0.1`**：`booktran serve` 不开放网络端口。

### 已知的安全边界（请自行评估）

- **网页端 API 没有鉴权**。它只监听 `127.0.0.1`，**仅限本机或可信内网使用**；
  不要通过端口转发、反向代理或公网 IP 暴露它。
- **翻译内容会发给你配置的模型厂商**。用 `booktran run` 时，段落（及命中的术语、
  翻译背景）会作为请求内容发送给对应的 API。这是产品的核心功能，
  但如果你处理的稿件敏感，请选择你信任的厂商或本地模型档案。
- **OCR / VLM 通道**在处理扫描页时同样会把图片发给模型厂商。
- 依赖库（httpx、beautifulsoup4、pymupdf、fastapi 等）的漏洞按常规流程处理；
  Dependabot 会持续提 PR 更新依赖，但**自动合并前请看 changelog**。

---

## 5. 什么不算漏洞

- 使用 `--fake` 假 provider 或本地假 key 触发的预期行为。
- 未经说明就认为"网页端没有鉴权"是漏洞 —— 这属于已知限制（见 §4），改进建议请开 issue。
- 配置文件里的明文密钥 —— 那是用户自己的选择；项目不做密钥加密托管。
- **把你自己的书稿、密钥或个人信息提交进仓库**（含误提交）：
  这属于操作失误而非代码缺陷，但仍请立即按 §3 处理（吊销 key、清理历史），
  并告知维护者。

---

## 6. 联系方式

- 漏洞私密报告：<https://github.com/joei4cm/booktrain/security/advisories/new>
- 备用邮箱：`<维护者邮箱>`
- 行为准则相关举报：见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
