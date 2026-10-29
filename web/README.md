# 网页端开发（Vite + React + TypeScript）

后端仍是 Python（FastAPI），前端是独立的 Vite 工程，构建产物落到
`src/booktran/web/dist/`，由后端直接静态托管。

## 日常开发（前后端分离跑）

```bash
# 终端 1：后端
.venv/bin/booktran serve --root ~/booktran-projects --port 8787

# 终端 2：前端（Vite dev server，/api 自动代理到 8787）
cd web
npm install        # 首次
npm run dev        # http://localhost:5173
```

## 构建（发布用）

```bash
cd web
npm run build      # 产物 → ../src/booktran/web/dist/
```

后端会自动优先服务 `dist/index.html`；没有构建产物时回落到旧的单文件界面
（`src/booktran/web/index.html`），所以**不构建也能跑**。

## 目录

```
web/src/
├── main.tsx          # 入口
├── App.tsx           # 外壳：顶栏 / 导航轨 / 常驻作业条 / SSE 接线
├── api.ts            # 类型化 API 客户端 + SSE 订阅
├── types.ts          # 与后端契约一一对应的类型
├── tokens.css        # 设计令牌（唯一样式来源）
├── styles.css        # 外壳布局
└── panels/
    ├── Library.tsx   # 书架（列表 + 空状态教学 + 上传）
    ├── Workspace.tsx # 工作台三栏：目录/阶段 | 活页预览 | 校对
    ├── Proof.tsx     # 校对面板（问题清单 + 采用建议）
    ├── Models.tsx    # 模型档案 + 角色路由 + 预算
    ├── Cost.tsx      # 成本明细（按阶段/角色/模型/语言）
    └── CommandPalette.tsx  # ⌘K 命令面板
```

## 规矩

- 颜色/间距/字号一律用 `tokens.css` 的 CSS 变量，**不硬编码颜色**。
- 组件不直接 `fetch`，统一走 `api.ts`。
- 类型以 `types.ts` 为准（与后端 `model.py` / `web/app.py` 一一对应）。
- 改完跑 `npm run typecheck`（`tsc --noEmit`）与 `npm run build`。
