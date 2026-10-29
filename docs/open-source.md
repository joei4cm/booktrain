# 开源发布清单（v2 → v1.0）

> 目标：把 booktran 作为**面向公众的**开源项目发布。
> 判断标准：陌生人 clone 之后，**5 分钟内能跑通一次翻译**，**30 分钟内能理解架构**，
> **1 小时内能提交一个被接受的 PR**。
> 依据：`PRODUCT.md`（用户是公众开源用户）、`docs/ux/cli.md`、`docs/ux/web.md`。

---

## 1. 仓库形态

保持**单仓库、单 Python 包 + 一个前端子目录**，不拆多包：

```
booktran/
├── src/booktran/            # Python 包（CLI + FastAPI 后端 + 引擎）
├── web/                     # Vite + TypeScript 前端（v2 新增，从 src/booktran/web 迁出）
├── docs/                    # 面向人的文档（Read 模式）
│   ├── design-v2.md         # 架构评审与目标设计
│   ├── ux/cli.md  ux/web.md # CLI / Web UX 规范
│   ├── open-source.md       # 本文件
│   ├── build-and-run.md     # 开发环境
│   └── architecture.md      # 架构说明（新建）
├── examples/                # 可运行的示例书（新建）
│   └── sample-book/         # 公共领域短文 + 一张扫描页样本
├── prompts/                 # 提示词预设（base / domain presets）
├── tests/                   # pytest
├── web/tests/               # vitest
├── LICENSE  CODE_OF_CONDUCT.md  SECURITY.md  CONTRIBUTING.md
├── CHANGELOG.md
└── README.md
```

**为什么前端从 `src/booktran/web/` 迁到 `web/`**：Python 包里塞 node_modules 会污染
wheel 构建。分离后 wheel 只含后端 + 预构建的 `web/dist`，npm 依赖不进入分发物。

---

## 2. 必须有的仓库文件

| 文件 | 内容要点 | 状态 |
|---|---|---|
| `README.md` | 定位一句话 / 60 秒上手 / 一张架构图 / 文档导航 / 谁适合用 / 许可 | 重写 |
| `LICENSE` | **Apache-2.0（推荐）**：显式专利授权，与所用参考项目（AiNiee、impeccable）一致；改动需 CLA 确认 | 待定 |
| `CONTRIBUTING.md` | 三种贡献路径（报 bug / 提 issue / 提 PR）、本地环境、代码风格、提交规范、评审流程 | 新建 |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1，联系方式用 maintainer GitHub | 新建 |
| `SECURITY.md` | 漏洞报告渠道；**明确：issue 里不要贴 API key**；支持版本表 | 新建 |
| `CHANGELOG.md` | Keep a Changelog 格式 | 新建 |
| `.github/ISSUE_TEMPLATE/` | bug（含复现命令与 `--json` 输出）/ feature / UX / 提问 | 新建 |
| `.github/pull_request_template.md` | 变更类型、关联 issue、测试证据（pytest + detector 输出） | 新建 |
| `.github/workflows/ci.yml` | 见 §4 | 新建 |
| `.gitignore` | 含 impeccable 的忽略片段 | 部分 |

**刻意不做**：Discord/Slack 邀请链接、Twitter 矩阵、贡献者排行榜。
一个长期项目靠 issue 与文档就够了，多余的社交机制是维护负担。

---

## 3. README（Read 模式的信息设计）

README 是这个项目**唯一的第一印象**，必须自己站住：

```
1  一句话：把电子书籍翻译成你能读的语言，翻译过程中看得见进度、花费与排版效果
2  截图（一张！不是六张）：工作台三栏，书页正在长大，费用可见
3  60 秒上手：三行命令 + 一张终端截图
4  它适合谁 / 不适合谁（诚实：不适合 1000 页的整本圣经逐字考据）
5  支持什么：输入格式 / 输出格式 / 语言 / 模型后端
6  一张架构图（config → roles → stages → layout，四个盒子）
7  文档导航：快速上手 / CLI 参考 / Web 指南 / 架构 / 贡献
8  谁在用（真实案例，没有就写"暂无"）
9  许可
```

- 截图是**唯一不可伪造的资产**。开源发布前必须真跑一遍截真实界面。
- 第 4 节"不适合谁"不能省。开源项目最伤信任的是夸大适用范围。
- 不写"一键完成"这类与实际（几小时的大书）不符的词。

---

## 4. CI（质量门禁）

```yaml
jobs:
  python:      # 3.11 / 3.12 / 3.13 矩阵
    - `ruff check .`（用仓库自带的 `pyproject.toml [tool.ruff]` 配置，**硬门禁**）
      与 `ruff format --check .`（格式基线，暂不阻断；跑一次 `ruff format .` 后转门禁）
    - mypy src/booktran        # 至少新增模块必须类型干净
    - pytest -q --cov         # 覆盖率 ≥ 70%
    - pip install -e ".[pdf,zh,web,tok]" 后跑端到端
  web:         # node 20
    - npm ci && npm run build && npm run typecheck && npm test
    - npx impeccable detect web/src --github    # 设计反模式门禁（无 finding 才过）
  smoke:       # 端到端冒烟（假 provider，零成本）
    - booktran init examples/sample-book/sample.txt --dir /tmp/proj --fake
    - booktran run -p /tmp/proj --fake --yes --stage all
    - booktran proofread -p /tmp/proj --fake      # 必须产出 quality_report.json
    - booktran preview -p /tmp/proj               # 必须产出 artifacts/preview/*.html
    - booktran export -p /tmp/proj --bilingual    # 必须产出译文 + 双语版
  docs:
    - markdownlint docs/ README.md
    - 所有 docs/ 内部链接可达
```

- **impeccable detector 进 CI**：把"反 AI 味的视觉回归"变成机器门禁，
  而不是靠自觉。这是这个项目区别于同类开源项目的地方。
- **端到端冒烟进 CI**：一条假 provider 的全链路必须跑通（init → run → proofread →
  preview → export），并校验产物存在——比单元测试更能守住"用户真的能用"。
- 端到端测试必须包含一条 **fake provider 的完整闭环**（TXT/EPUB/PDF → 翻译 → 导出），
  以及一条 **CLI ↔ Web 数据契约一致性**测试（同一份 state.json，两端读出的阶段/费用一致）。

---

## 5. 文档体系（Read 模式）

> 以下是 **v1.0 发布前必须建成** 的目标文档树；当前仓库只有 `design-v2.md` / `ux/*` / `open-source.md`。

```
README.md            5 分钟上手
docs/quickstart.md   详细上手（含成本与 key 配置）
docs/cli.md          CLI 参考（每个命令 + 退出码 + 环境变量）
docs/web.md          Web 指南（四个界面 + 快捷键）
docs/architecture.md 架构（配置层 / 能力抽象 / 阶段流水线 / 排版 / 存储）
docs/configuration.md 配置参考（profiles / roles / cascade / budget / lexicon）
docs/providers.md    支持的后端与各自的能力矩阵（text/vision/ocr/embed）
docs/prompting.md    提示词五层模型与如何写背景
docs/troubleshooting.md 常见故障（key、无输出、429、OCR 差、乱码）
docs/development.md  开发环境与架构约定
```

- 每个文档**能独立阅读**：读者可能是从 troubleshooting 搜进来的。
- 文档用**任务作标题**（"怎么只重翻第三章"），不用名词堆（"Advanced Usage"）。
- 中英双语：README 与 docs 提供 `README.md`（英文）+ `README.zh-CN.md`（中文），
  或英文为主、中文 README 链接到同一份中文版。**选定一种并坚持**，
  不要两套都半成品。

---

## 6. 首屏体验（决定 star 数量）

- 仓库自带 `examples/sample-book/`：一本 12 段的公共领域短篇（英文）+
  一张扫描页样本（PDF）+ 一本 MOBI 样本（验证 MOBI 链路）。
- `booktran doctor` 之后，`booktran init examples/sample-book/sample.txt --fake`
  能在**零成本、零 key**下跑通全流程。README 的"60 秒上手"就用它。
- Web 端有「用示例书试试」按钮（同一条路径，见 `docs/ux/web.md` §3.1）。

---

## 7. 品牌与资产（当前缺口，诚实记录）

| 资产 | 状态 | 说明 |
|---|---|---|
| 名称 booktran | ✅ | 沿用 |
| Logo | ❌ | 需要设计；建议做一枚"翻页 + 批注"的小字标，单色可用 |
| 应用图标 / favicon | ❌ | 同上，需 512/180 PNG |
| README 主图 | ❌ | 必须真跑截图 |
| 社交预览图 | ❌ | 可由 README 主图 + 标题生成 |
| 品牌色 | ❌ | 当前视觉系统是中性灰 + 靛蓝，**不假装有品牌色**（见 `PRODUCT.md`） |
| 字体授权 | ⚠️ | 界面用系统字体（无授权问题）；书页预览用 Source Han Serif / Noto Serif（OFL，需随包附带许可或走系统回退） |
| 领域 | ❌ | booktran.org 未注册；先占 GitHub 地址与 PyPI 名字 |

---

## 8. 隐私与安全姿态（要写进 README）

- **不收集任何数据**：无遥测、无崩溃上报、无网络回传（除模型 API 调用本身）。
- **密钥不出本机**：只从环境变量或 `~/.config/booktran/keys.toml` 读取；
  项目目录永不写密钥；Web 端不回显密钥。
- **默认只监听 127.0.0.1**，不开放网络端口。
- 上传的书稿留在本地 `projects/` 目录，不外传。
- 若将来引入可选遥测，必须**默认关闭**且在 README 明示开关。

---

## 9. 发布流程

1. 版本号 = `pyproject.toml` 与 `src/booktran/__init__.py` 的 `__version__` 同源
   （现在两处各写一遍，容易漂移 → 改为从包元数据读取）。
2. `CHANGELOG.md` 写好 → tag `vX.Y.Z` → GitHub Release 附产物。
3. PyPI 发布：`python -m build && twine upload`（用 Trusted Publishing，密钥不落 CI）。
4. 打包校验：`pip install booktran && booktran doctor` 在干净环境通过。
5. 前端产物：wheel 内含 `booktran/web/dist`（由 release workflow 构建并校验哈希）。

---

## 10. 发布前必须完成（Definition of Done）

- [ ] `booktran doctor` 在干净机器 + 只有一个 key 的场景下能定位所有阻塞项
- [ ] `booktran init examples/sample-book/sample.txt --fake` 零成本跑通
- [ ] CLI 的每个命令都有 `--help` 与至少一个示例
- [ ] 终端输出在 `NO_COLOR=1`、非 TTY、80 列、120 列四种条件下都可读
- [ ] Web 端四档宽度（1440/1024/768/390）无布局破损
- [ ] 键盘可完成全部主流程（不依赖鼠标）
- [ ] 对比度全部达标；状态不只用颜色
- [ ] CI 全绿：lint / type / test / build / detector
- [ ] README 截图是真实运行截图
- [ ] LICENSE / CONTRIBUTING / CODE_OF_CONDUCT / SECURITY 齐备
- [ ] 无任何密钥、路径、书名等个人信息残留在仓库里（含 git 历史）
- [ ] `impeccable critique` + `impeccable finish review` 通过，DETECTOR 无阻塞项
