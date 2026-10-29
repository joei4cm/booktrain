# booktran：从安装到运行（Build & Run Guide）

Python 项目无需编译，"编译"这一步 = 可编辑安装（pip 安装依赖 + 建入口脚本 + 写元数据）。
所有命令均在 Python 3.14.4 / Linux 上实测通过（90 个测试全绿）。

## 0. 环境要求

- Python ≥ 3.11（`python3 --version` 确认）
- 有网可访问 PyPI
- 无系统级 C 依赖：`pymupdf` / `opencc` / `fastapi` 都有现成 wheel，不需要 gcc

## 1. 安装（= 你的"编译"步骤）

```bash
cd /home/michael/codes/booktran

python3 -m venv .venv                       # 建独立环境
.venv/bin/pip install -e ".[pdf,zh,web,tok,test]"   # 可编辑安装 + 全部功能
```

extras 按需选择，可多选：

| extras | 装什么 | 提供的能力 |
|---|---|---|
| `pdf` | pymupdf | PDF 读取、版面保真双语 PDF 导出 |
| `zh` | opencc | `--zh-convert s2t/t2s` 简繁转换 |
| `web` | fastapi + uvicorn + python-multipart | `booktran serve` 网页端（**跑网页必装**） |
| `tok` | tiktoken | 精确 token 计数（没有则按字符估算） |
| `test` | pytest | 跑测试 |

只有 `httpx` + `beautifulsoup4` 是硬依赖；最小安装 `pip install -e .` 只够 TXT/EPUB。

## 2. 验证安装

```bash
.venv/bin/booktran --version      # 期望: booktran 0.4.0
.venv/bin/python -m pytest tests/ -q   # 期望: 90 passed
```

> 若终端提示符还显示旧版本号（如 `📦 v0.2.0`），那是 shell 主题（starship）读的
> `PKG-INFO` 元数据，不代表实际功能；重跑一次 `.venv/bin/pip install -e ".[web]"`
> 即会刷新到 0.4.0。实际运行的入口以 `.venv/bin/booktran --version` 为准。
>
> 若 `booktran` 不在 PATH，或指向了别的解释器，一律用全路径 `.venv/bin/booktran`。

## 3. 配置模型服务

三种来源，优先级：命令行 > 项目快照（`project.json`）> `config.toml` > 环境变量 > 默认值。

### 3.1 环境变量（最省事）

```bash
# Anthropic 系（含兼容网关；也认 ANTHROPIC_AUTH_TOKEN 作为 Bearer）
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI 兼容端点（DeepSeek / vLLM / Ollama 等）
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.deepseek.com/v1
```

### 3.2 config.toml（可复用）

`~/.config/booktran/config.toml` 或项目目录下 `config.toml`：

```toml
[llm]
provider = "anthropic"            # anthropic | openai | fake
model = "claude-sonnet-4-5"
concurrency = 4
rpm = 0                           # 0 = 不限速
max_retries = 4
prompt_preset = "default"         # default | local（本地小模型用编号纯文本协议）

[translate]
source_lang = "auto"              # 按字符分布自动猜测
target_lang = "chinese_simplified"
chunk_budget_tokens = 4000
rounds = 6
```

### 3.3 platforms.toml（接口档案 + 任务路由）

`~/.config/booktran/platforms.toml`：

```toml
active_platform = "claude"

[platforms.claude]
provider = "anthropic"
model = "claude-sonnet-4-5"
concurrency = 8

[platforms.local]
provider = "openai"
model = "qwen2.5:7b"
base_url = "http://127.0.0.1:11434/v1"
api_key = "local"
prompt_preset = "local"

[routing]
translate = "claude"   # 翻译用 claude
analyze   = "local"    # 术语分析用本地小模型
# vlm = "vision"       # 扫描页识别可单配一档
```

选档案规则：`--platform X`（命令行）> `routing[任务]` > `active_platform`。

**安全约定**：`project.json` 是不含密钥的快照（`api_key` 被掩码）；网页端填的密钥单独写
`<项目>/secrets.json`（权限 0600），设置接口只回传 `has_api_key` 布尔值。

## 4. CLI 运行（零成本 demo，不调 API）

下面一整段可直接粘贴（在 /tmp 建了个 3 段示例书，双目标语言）：

```bash
BT=/home/michael/codes/booktran/.venv/bin/booktran
cd /tmp && printf '第一章 启程\n\n他推开了那扇门。\n\n"终于到了。"她说道。\n' > demo.txt

$BT init demo.txt --dir projects/booktran-demo --fake \
     --target-langs chinese_simplified,english   # 建项目：假 provider + 多目标语言
$BT analyze  -p projects/booktran-demo --fake    # 全文术语分析 → glossary.json
$BT translate -p projects/booktran-demo --fake   # 翻译（可随时 Ctrl-C，再跑即续翻）
$BT status   -p projects/booktran-demo           # 逐语言进度
$BT export   -p projects/booktran-demo --bilingual
ls projects/booktran-demo/output/                # demo_zh.txt demo_en.txt + 两个双语版
```

去掉 `--fake` 即走真实模型（会读上面的环境变量 / 配置）。

### 常用命令速查

```bash
booktran init 源.epub [--dir 项目目录] [--provider anthropic] [--model 模型名] \
         [--platform 档案名] [--target-langs 中文,英文] [--zh-convert s2t] \
         [--system-prompt-file prompt.txt] [--vlm]   # 扫描件识别
booktran analyze   -p <项目> [--platform 档案] [--no-llm-merge]
booktran translate -p <项目> [--fake] [--system-prompt "…"] [--rounds 6]
booktran status    -p <项目>
booktran export    -p <项目> [--bilingual] [--format txt|epub|pdf] [--lang english]
```

- **断点续翻**：`translate` 中断后重跑，已译（按语言独立）自动跳过
- **多语言**：每段有多条"语言轨道"，进度/重试/续翻按语言互不影响
- **翻译背景**：项目 `glossary.json` 的 `background` 对象（题材/时代/人物/命名规则…）
  会以 `## 作品背景` 节注入每次请求
- **PDF 扫描件**：`init --vlm` 交给多模态模型识别；`export --format pdf --bilingual`
  出左原文右译文的版面保真 PDF

## 5. 网页端运行

```bash
mkdir -p ~/booktran-projects        # 项目根目录（上传的项目都建在这里）
cd ~/booktran-projects
/home/michael/codes/booktran/.venv/bin/booktran serve --root . --port 8787
```

打开 <http://127.0.0.1:8787>：

1. 左侧上传框拖入 PDF/EPUB/TXT（可勾 VLM、填目标语言）→ 自动建项目
2. 「设置」页调模型/base_url/密钥/并发/目标语言/简繁等 → 保存
3. 「翻译背景」「术语表（按语言分列 `dst_<lang>`）」填设定
4. 点翻译 → 进度条轮询；「段落」页逐条改译文
5. 导出 → 「文件」列表直接下载

常用变体：

```bash
booktran serve --root . --port 8787            # 本机访问
booktran serve --root . --host 0.0.0.0         # 局域网访问（API 无鉴权，仅可信内网）
nohup booktran serve --root . --port 8787 > serve.log 2>&1 &   # 后台常驻
```

> ⚠️ 文档里的 `<projects根目录>` 是**占位符**，必须替换成真实路径；照抄尖括号会被
> bash 当成输入重定向（`bash: … No such file or directory`）。用 `--root .` 最省事。

## 6. 更新 / 卸载 / 排错

```bash
# 改了代码：editable 安装无需重编，重载即可（新开 shell / 重启 serve）
# 改了依赖或版本号：重装一次刷新元数据
.venv/bin/pip install -e ".[web]"

# 卸载
.venv/bin/pip uninstall -y booktran

# 排错
.venv/bin/booktran --version          # 确认 0.4.0
.venv/bin/python -m pytest tests/ -q  # 确认 90 passed
```

| 症状 | 原因 / 处理 |
|---|---|
| `bash: …: No such file or directory` 且内容是中文占位符 | 把 `<…>` 换成真实路径 |
| 提示符显示旧版本 | starship 读的元数据，`pip install -e .` 刷新 |
| `booktran: command not found` | 用全路径 `.venv/bin/booktran` |
| 网页 422（上传接口） | 没装 `[web]` extra，或缺 `python-multipart` |
| 翻译 401/404 | 检查环境变量 / `platforms.toml` 的 base_url 与档案是否匹配 |
| PDF 导出报 pymupdf | `pip install -e ".[pdf]"` |
