"""CLI：doctor / init / run / status / export / analyze / cost / profile / role / serve。

UX 契约见 docs/ux/cli.md：
- 三种输出形态：TTY 实时覆盖、管道逐行、--json NDJSON
- 开跑前必给成本估算与确认
- 错误第一行是人话，且给出可执行的下一步
- 退出码语义化（见 ui.py）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from . import __version__
from .adapters import WRITERS
from .config import (
    AppConfig,
    apply_platform,
    dump_toml,
    keys_path,
    load_config,
    load_project_manifest,
    migrate_project,
    profiles_path,
    resolve_task_platform,
    write_project_config,
)
from .estimate import estimate_cost
from .glossary import Glossary
from .llm.base import FakeProvider
from .llm.usage import UsageLog
from .model import Status
from .pipeline import Progress, make_router, run_pipeline
from .project import init_project, iter_source_files, read_docs
from .prompt.builder import lang_code
from .store import Store
from .ui import (
    EXIT_BUDGET,
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_PARTIAL,
    Term,
    UserError,
    fmt_cost,
    fmt_duration,
    fmt_tokens,
)

TERM = Term()


# ---------------- 工具 ----------------

_PROJECT_MARKERS = ("project.json", "booktran.toml", "state.json", "usage.jsonl")


def _is_project_dir(p: Path) -> bool:
    return any((p / m).exists() for m in _PROJECT_MARKERS)


def _find_project_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not _is_project_dir(p):
            raise UserError(f"不是 booktran 项目目录: {p}",
                            "用 booktran init <文件> 创建，或用 --project 指定")
        return p
    cwd = Path.cwd()
    for cand in (cwd, *cwd.parents):
        if _is_project_dir(cand):
            return cand
    raise UserError("未找到项目目录",
                    "先运行 booktran init <文件>，或用 --project 指定路径")


def _load_project_cfg(project_dir: Path, overrides: dict | None = None) -> AppConfig:
    """项目配置：全局档案 + 项目差异（引用式，不冻结）。"""
    note = migrate_project(project_dir)
    cfg = load_config(project_dir, overrides)
    if note:
        TERM.warn(note)
    return cfg


def _apply_role(cfg: AppConfig, role: str, explicit: str = "") -> str:
    """把角色绑定到档案，并同步到 cfg.llm（供旧代码路径使用）。"""
    pid = explicit or resolve_task_platform(cfg, role)
    if not pid:
        return ""
    if pid in cfg.profiles:
        apply_platform(cfg, pid)
    elif pid in cfg.platforms:
        apply_platform(cfg, pid)
    return pid


def _settlement(term: Term, stats, *, elapsed: float, project_dir: Path,
                pending: int = 0) -> None:
    """结算块：不是一句"完成"，而是一张能对账的单子。"""
    term.rule()
    term.write(term.paint("✓ 完成" if not pending else "! 部分完成", "green" if not pending else "yellow"))
    term.write(f"  请求 {stats.requests} 次 · 失败块 {stats.failed_chunks} · 用时 {fmt_duration(elapsed)}")
    term.write()
    term.kv("token", f"输入 {fmt_tokens(stats.prompt_tokens)}   输出 {fmt_tokens(stats.completion_tokens)}")
    cost_line = fmt_cost(stats.cost)
    if stats.unpriced_requests:
        cost_line += f"   ({stats.unpriced_requests} 次请求的档案未配置价格，未计入)"
    term.kv("费用", cost_line)
    if pending:
        term.kv("待办", f"{pending} 个(段×语言)未完成，可再次运行 booktran run 继续")
    term.write()
    term.kv("预览", f"booktran preview -p {project_dir}")
    term.kv("导出", f"booktran export -p {project_dir}")


# ---------------- doctor ----------------

def cmd_doctor(args) -> None:
    """环境体检：只读，不花钱（除非 --test）。"""
    term = TERM
    term.rule(f"booktran {__version__} · Python {sys.version.split()[0]}")
    term.write()

    # 依赖
    deps = []
    for mod, label, why in (("httpx", "httpx", "核心"), ("bs4", "beautifulsoup4", "EPUB"),
                            ("pymupdf", "pymupdf", "PDF"), ("opencc", "opencc", "简繁"),
                            ("fastapi", "fastapi", "网页端")):
        try:
            __import__(mod)
            deps.append((label, why, True))
        except ImportError:
            deps.append((label, why, False))
    term.rule("依赖")
    for label, why, ok in deps:
        (term.ok if ok else term.warn)(f"{label:<16} {why}")

    cfg = load_config(None)
    # 档案
    term.rule("模型档案")
    if not cfg.profiles:
        term.warn("未定义任何模型档案")
        term.write(f"    {profiles_path()}")
    for pid, p in sorted(cfg.profiles.items()):
        from .config.keyring import key_source
        src = key_source(p)
        ok = src != "未找到"
        line = f"{pid:<14} {p.provider}/{p.model or '—'}  [{p.kind}]  密钥:{src}"
        (term.ok if ok else term.warn)(line)
        if p.price_in is None and p.price_out is None:
            term.write(f"    {'':<14} 未配置价格 → 费用显示 —")

    # 角色
    term.rule("角色路由")
    if not cfg.roles:
        term.warn("未配置角色（翻译将使用默认档案）")
    for name, spec in sorted(cfg.roles.items()):
        term.write(f"  {name:<14} → {spec.target or '—'}"
                   + (f"  (fallback: {spec.fallback})" if spec.fallback else ""))

    # 连通性（可选）
    if args.test:
        term.rule("连通性")
        for pid, p in sorted(cfg.profiles.items()):
            if p.provider == "fake":
                term.skip(f"{pid} 假 provider，无需测试")
                continue
            asyncio.run(_test_profile(cfg, pid))

    # 路径
    term.rule("路径")
    term.kv("全局配置", str(profiles_path().parent))
    term.kv("密钥", str(keys_path()))
    term.write()
    problems = sum(1 for _, _, ok in deps if not ok)
    if problems:
        term.warn(f"{problems} 个可选依赖缺失（相关功能不可用）")
    term.write()


async def _test_profile(cfg: AppConfig, pid: str) -> None:
    from .llm.router import ModelRouter
    router = ModelRouter(cfg)
    p = cfg.profiles.get(pid)
    if p is None:
        TERM.fail(f"{pid} 档案不存在")
        return
    if not p.api_key and p.provider not in ("fake", "ollama", "local", "vllm"):
        TERM.warn(f"{pid} 没有可用密钥，跳过连通性测试")
        return
    t0 = time.monotonic()
    try:
        router.backend(p)
        res, _ = await router.run("translate", "ping", "回复一个字：好")
        dt = (time.monotonic() - t0) * 1000
        TERM.ok(f"{pid} 连通 ({dt:.0f}ms, {res.prompt_tokens}+{res.completion_tokens} tok)")
    except Exception as e:  # noqa: BLE001
        TERM.fail(f"{pid} 连通失败: {str(e)[:120]}")
    finally:
        await router.aclose()


# ---------------- init ----------------

def cmd_init(args) -> None:
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise UserError(f"输入不存在: {source}", "检查路径是否正确")
    project_dir = Path(args.dir).expanduser().resolve() if args.dir \
        else Path.cwd() / f"booktran-{source.stem}"
    if project_dir.exists() and ((project_dir / "project.json").exists()
                                 or (project_dir / "booktran.toml").exists()):
        raise UserError(f"项目已存在: {project_dir}",
                        "直接运行 booktran run 即可续翻；换目录请用 --dir")

    cfg = load_config(project_dir)
    _apply_overrides(cfg, args)
    pid = _apply_role(cfg, "translate", getattr(args, "platform", "") or "")
    if getattr(args, "target_langs", None):
        cfg.tr.target_langs = [x.strip() for x in str(args.target_langs).split(",") if x.strip()]
    elif getattr(args, "target_lang", None):
        cfg.tr.target_langs = [args.target_lang]
    cfg.ensure_langs()

    files = iter_source_files(source)
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp"}
    has_images = any(f.suffix.lower() in IMAGE_EXTS for f in files)
    if getattr(args, "vlm", False) or getattr(args, "ocr", False) or has_images:
        docs = asyncio.run(read_docs(files, source if source.is_dir() else source.parent,
                                     vlm=True, vlm_cfg=cfg,
                                     vlm_dpi=getattr(args, "vlm_dpi", 150),
                                     fake=getattr(args, "fake", False),
                                     ocr=bool(getattr(args, "ocr", False)),
                                     platform=getattr(args, "platform", "") or ""))
    else:
        docs = None

    info = init_project(source, project_dir, cfg, docs=docs, files=files)
    # 项目只写引用与差异；模型配置留在全局档案
    roles = {"translate": pid} if pid else {}
    write_project_config(project_dir, cfg, roles=roles)

    term = TERM
    term.rule("项目已建立")
    term.kv("目录", str(project_dir))
    term.kv("文档", f"{info['docs']} 个 · {info['segments']} 段（待翻 {info['pending']}）")
    term.kv("目标语言", ", ".join(info["target_langs"]))
    term.kv("翻译角色", pid or "（未绑定档案）")
    term.write()
    term.kv("下一步", f"booktran run -p {project_dir}")
    term.write()


# ---------------- run / translate ----------------

def cmd_run(args) -> None:
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        # stdout 只留机器可读事件，人类输出改走 stderr
        TERM.redirect(sys.stderr)
    if getattr(args, "deprecated", False):
        TERM.warn("booktran translate 已更名为 booktran run（命令仍然可用）")
    project_dir = _find_project_dir(args.project)
    cfg = _load_project_cfg(project_dir)
    _apply_overrides(cfg, args)
    _apply_role(cfg, "translate", getattr(args, "platform", "") or "")

    store = Store(project_dir)
    docs, stats = store.load(default_lang=cfg.tr.target_lang)
    if not docs:
        raise UserError("项目状态为空", "先运行 booktran init <文件>")
    for doc in docs:
        for s in doc.segments:
            s.ensure_tracks(cfg.tr.target_langs)

    pending = sum(1 for d in docs for s in d.segments if s.status != Status.EXCLUDED
                  for lang in cfg.tr.target_langs
                  if s.track(lang, create=False).status != Status.TRANSLATED)
    term = TERM
    if pending == 0:
        term.ok("没有待翻译的段落")
        return

    usage_log = UsageLog(project_dir)
    provider = FakeProvider() if getattr(args, "fake", False) else None
    router = make_router(cfg, provider, usage_log=usage_log)

    # ---- 开跑前成本闸 ----
    profile = router.pick("translate")
    est = estimate_cost(docs, profile,
                        chunk_budget_tokens=cfg.tr.chunk_budget_tokens,
                        target_langs=len(cfg.tr.target_langs))
    term.rule("即将开始")
    term.kv("项目", str(project_dir))
    term.kv("规模", f"{est.segments} 段 · {fmt_tokens(est.source_tokens)} 源 token · {est.chunks} 块")
    term.kv("模型", f"{profile.model or profile.provider}  (档案: {profile.id})")
    if est.unknown:
        term.kv("预计费用", "未知（该档案未配置价格表）")
    else:
        term.kv("预计费用", f"{fmt_cost(est.cost_low)} – {fmt_cost(est.cost_high)}  （估算）")
    if cfg.budget.max_cost:
        term.kv("预算上限", fmt_cost(cfg.budget.max_cost))
    if getattr(args, "dry_run", False):
        term.write()
        term.ok("试算结束（未发起任何请求）")
        return
    if not getattr(args, "yes", False) and sys.stdin.isatty():
        term.write()
        answer = input("  [Enter] 开始   [c] 改配置   [q] 退出 ").strip().lower()
        if answer in ("q", "quit", "exit"):
            term.skip("已退出")
            return
        if answer in ("c", "c", "config"):
            raise UserError("请在配置文件中调整后重试",
                            f"档案: {profiles_path()}\n     项目差异: {project_dir / 'booktran.toml'}")

    # ---- 执行 ----
    term.rule()
    progress = Progress(term)
    stop_event = asyncio.Event()
    if json_mode:
        def _emit(event: dict) -> None:
            print(json.dumps(event, ensure_ascii=False), flush=True)
        progress.add_listener(_emit)

    stage_arg = getattr(args, "stage", "translate")
    stages = ["translate", "proofread"] if stage_arg == "all" else [stage_arg]
    proofread_mode = getattr(args, "proofread_mode", "qa")
    glossary = Glossary.load(project_dir / "glossary.json")

    async def _amain():
        try:
            await run_pipeline(docs, None, cfg, store, stats, glossary, progress,
                               stop_event, router=router, usage_log=usage_log,
                               stages=stages, proofread_mode=proofread_mode,
                               project_dir=project_dir)
        finally:
            await router.aclose()
            usage_log.close()

    t0 = time.monotonic()
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt as e:
        term.clear_status()
        store.save_snapshot(docs, stats)
        term.warn("已暂停 · 已保存译文 · 再次运行 booktran run 即可从断点继续")
        raise SystemExit(EXIT_INTERRUPTED) from e
    finally:
        store.close()

    elapsed = time.monotonic() - t0
    remaining = sum(1 for d in docs for s in d.segments if s.status != Status.EXCLUDED
                    for lang in cfg.tr.target_langs
                    if s.track(lang, create=False).status != Status.TRANSLATED)
    _settlement(term, stats, elapsed=elapsed, project_dir=project_dir, pending=remaining)
    if stats.cost and cfg.budget.max_cost and stats.cost >= cfg.budget.max_cost:
        raise SystemExit(EXIT_BUDGET)
    if remaining:
        raise SystemExit(EXIT_PARTIAL)


# ---------------- proofread / report ----------------

def cmd_proofread(args) -> None:
    """只跑校对阶段，产出问题清单与报告。"""
    project_dir = _find_project_dir(args.project)
    cfg = _load_project_cfg(project_dir)
    _apply_overrides(cfg, args)
    store = Store(project_dir)
    docs, stats = store.load(default_lang=cfg.tr.target_lang)
    if not docs:
        raise UserError("项目状态为空")
    usage_log = UsageLog(project_dir)
    router = make_router(cfg, FakeProvider() if args.fake else None, usage_log=usage_log)
    progress = Progress(TERM)
    glossary = Glossary.load(project_dir / "glossary.json")

    async def _amain():
        try:
            await run_pipeline(docs, None, cfg, store, stats, glossary, progress,
                               None, router=router, usage_log=usage_log,
                               stages=["proofread"], proofread_mode=args.mode,
                               project_dir=project_dir)
        finally:
            await router.aclose()
            usage_log.close()

    asyncio.run(_amain())
    from .quality.report import QualityReport
    report = QualityReport.load(project_dir)
    s = report.summary()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
        return
    TERM.rule("质检结果")
    TERM.kv("问题", f"{s['total']} 处"
                    f"（错误 {s['by_severity'].get('error', 0)} · "
                    f"警告 {s['by_severity'].get('warn', 0)} · "
                    f"提示 {s['by_severity'].get('info', 0)}）")
    for kind, n in sorted(s["by_kind"].items(), key=lambda kv: -kv[1]):
        TERM.write(f"  {kind:<14} {n}")
    TERM.kv("报告", str(project_dir / "quality_report.md"))


def cmd_report(args) -> None:
    """查看质检报告。"""
    project_dir = _find_project_dir(args.project)
    from .quality.report import QualityReport
    report = QualityReport.load(project_dir)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
        return
    if not (project_dir / "quality_report.json").exists():
        TERM.write("还没有质检报告")
        TERM.write("  运行 booktran proofread -p <项目> 生成")
        return
    s = report.summary()
    TERM.rule("质检报告")
    TERM.kv("问题", f"{s['total']} 处")
    for issue in report.sorted_issues()[:100]:
        TERM.write(f"  {issue.lang:<20} {issue.sid:<24} {issue.severity:<6} {issue.message}")


# ---------------- status ----------------

def _project_langs(project_dir: Path, cfg: AppConfig, manifest: dict) -> list[str]:
    """目标语言：新项目取 booktran.toml；旧项目回落到清单快照。"""
    if (project_dir / "booktran.toml").exists():
        return cfg.tr.target_langs
    saved = AppConfig.from_dict(manifest.get("config"))
    return saved.tr.target_langs or cfg.tr.target_langs


def cmd_status(args) -> None:
    project_dir = _find_project_dir(args.project)
    cfg = load_config(project_dir)
    manifest = load_project_manifest(project_dir) if (project_dir / "project.json").exists() else {}
    langs = _project_langs(project_dir, cfg, manifest)
    store = Store(project_dir)
    docs, stats = store.load(default_lang=(langs or ["chinese_simplified"])[0])
    if not docs:
        TERM.write("项目为空")
        return
    excluded = sum(1 for d in docs for s in d.segments if s.status == Status.EXCLUDED)
    total = sum(1 for d in docs for s in d.segments if s.status != Status.EXCLUDED)
    if args.json:
        payload = {
            "project": str(project_dir),
            "langs": {lang: {
                "translated": sum(1 for d in docs for s in d.segments if s.is_done(lang)),
                "total": total,
            } for lang in langs},
            "excluded": excluded,
            "stats": stats.to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return

    term = TERM
    term.rule(str(project_dir))
    for lang in langs:
        done = sum(1 for d in docs for s in d.segments if s.is_done(lang))
        pct = done / max(1, total) * 100
        errored = sum(1 for d in docs for s in d.segments
                      if s.status != Status.EXCLUDED and not s.is_done(lang)
                      and s.track(lang, create=False).error)
        term.kv(lang, f"{done}/{total} 段 ({pct:.1f}%)"
                      + (f" · {errored} 段待重试" if errored else ""))
    term.kv("过滤", f"{excluded} 段")
    term.kv("累计", f"请求 {stats.requests} · 失败块 {stats.failed_chunks} · "
                    f"token {fmt_tokens(stats.prompt_tokens)}→{fmt_tokens(stats.completion_tokens)}")
    # 费用真相在 usage 账本（逐次记账）；快照 stats 只是汇总
    usage = UsageLog(project_dir).aggregate()["total"]
    cost_value = usage["cost"] if usage["requests"] else stats.cost
    cost = fmt_cost(cost_value)
    unpriced = usage["unpriced"] + (stats.unpriced_requests if not usage["requests"] else 0)
    if unpriced:
        cost += f"（另有 {unpriced} 次请求未计价）"
    term.kv("费用", cost)
    from .quality.report import QualityReport
    report = QualityReport.load(project_dir)
    if report.issues:
        rs = report.summary()
        term.kv("质检", f"{rs['total']} 个问题"
                        f"（错误 {rs['by_severity'].get('error', 0)}）"
                        f" · booktran report -p {project_dir}")


# ---------------- cost ----------------

def cmd_cost(args) -> None:
    project_dir = _find_project_dir(args.project)
    log = UsageLog(project_dir)
    agg = log.aggregate()
    if args.json:
        print(json.dumps(agg, ensure_ascii=False))
        return
    total = agg["total"]
    if not total["requests"]:
        TERM.write("还没有任何用量记录")
        return
    term = TERM
    term.rule("费用")
    term.kv("合计", f"{fmt_cost(total['cost'])}   请求 {total['requests']} 次   "
                    f"token {fmt_tokens(total['prompt_tokens'])}→{fmt_tokens(total['completion_tokens'])}")
    if total["unpriced"]:
        term.kv("未计价", f"{total['unpriced']} 次请求（档案未配置价格）")
    for dim, label in (("by_stage", "按阶段"), ("by_role", "按角色"),
                       ("by_model", "按模型"), ("by_lang", "按语言")):
        buckets = agg[dim]
        if not buckets:
            continue
        term.rule(label)
        width = max(len(k) for k in buckets)
        for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["cost"]):
            note = f"（{b['unpriced']} 次未计价）" if b["unpriced"] else ""
            term.write(f"  {name.ljust(width)}  {fmt_cost(b['cost'])}{note}")


# ---------------- profile / role ----------------

def cmd_profile(args) -> None:
    cfg = load_config(None)
    if args.action == "list":
        if not cfg.profiles:
            TERM.write(f"未定义模型档案 → {profiles_path()}")
            return
        TERM.rule("模型档案")
        for pid, p in sorted(cfg.profiles.items()):
            TERM.write(f"  {pid:<16} {p.provider}/{p.model or '—'}  [{p.kind}]")
        return
    if args.action == "add":
        path = profiles_path()
        data = {}
        if path.exists():
            import tomllib
            try:
                data = tomllib.loads(path.read_text("utf-8"))
            except Exception as e:  # noqa: BLE001
                raise UserError(f"配置文件解析失败: {path}", str(e)) from e
        pid = args.id
        entry = dict(data.get("profile", {}).get(pid, {}))
        entry.update({
            "provider": args.provider or entry.get("provider", "anthropic"),
            "model": args.model or entry.get("model", ""),
            "kind": args.kind or entry.get("kind", "chat"),
        })
        if args.key_env:
            entry["key_env"] = args.key_env
        if args.price_in is not None:
            entry["price_in"] = args.price_in
        if args.price_out is not None:
            entry["price_out"] = args.price_out
        if args.concurrency:
            entry["concurrency"] = args.concurrency
        data.setdefault("profile", {})[pid] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_toml(data), encoding="utf-8")
        TERM.ok(f"已写入档案 {pid} → {path}")
        TERM.write("  改全局档案会立刻影响所有项目（项目只存引用）")
        return
    if args.action == "remove":
        path = profiles_path()
        if not path.exists():
            raise UserError(f"配置文件不存在: {path}")
        import tomllib
        data = tomllib.loads(path.read_text("utf-8"))
        if args.id not in (data.get("profile") or {}):
            raise UserError(f"档案不存在: {args.id}",
                            f"现有: {', '.join(sorted(data.get('profile') or {})) or '无'}")
        del data["profile"][args.id]
        path.write_text(dump_toml(data), encoding="utf-8")
        TERM.ok(f"已删除档案 {args.id}")
        return
    if args.action == "test":
        if args.id not in cfg.profiles:
            raise UserError(f"档案不存在: {args.id}",
                            f"现有: {', '.join(sorted(cfg.profiles)) or '无'}")
        asyncio.run(_test_profile(cfg, args.id))


def cmd_role(args) -> None:
    cfg = load_config(None)
    if args.action == "list":
        TERM.rule("角色路由")
        if not cfg.roles:
            TERM.write("  （未配置；翻译将使用默认档案）")
        for name, spec in sorted(cfg.roles.items()):
            TERM.write(f"  {name:<14} → {spec.target or '—'}"
                       + (f"   fallback: {spec.fallback}" if spec.fallback else ""))
        return
    if args.action == "set":
        if "=" not in args.assign:
            raise UserError("格式应为 <角色>=<档案>", "例如 booktran role set translate=claude")
        name, pid = args.assign.split("=", 1)
        name, pid = name.strip(), pid.strip()
        path = profiles_path()
        import tomllib
        data = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
        data.setdefault("roles", {})[name] = pid
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_toml(data), encoding="utf-8")
        TERM.ok(f"角色 {name} → {pid}  （已写入 {path}）")
        TERM.write("  项目可单独覆盖：编辑 <项目>/booktran.toml 的 [roles]")
        return


# ---------------- export ----------------

def cmd_export(args) -> None:
    project_dir = _find_project_dir(args.project)
    cfg = load_config(project_dir)
    manifest = load_project_manifest(project_dir)
    langs = _project_langs(project_dir, cfg, manifest)
    store = Store(project_dir)
    docs, stats = store.load(default_lang=(langs or ["chinese_simplified"])[0])
    if not docs:
        raise UserError("项目状态为空")

    langs = cfg.tr.target_langs or ["chinese_simplified"]
    if getattr(args, "lang", None):
        want = [x.strip() for x in str(args.lang).split(",") if x.strip()]
        unknown = [x for x in want if x not in langs]
        if unknown:
            raise UserError(f"未知目标语言: {unknown}", f"项目语言: {langs}")
        langs = want

    source_root = Path(manifest["source"])
    out_root = Path(args.out).expanduser().resolve() if args.out else project_dir / "output"
    for lang in langs:
        suffix = lang_code(lang)
        for doc in docs:
            writer = WRITERS[doc.fmt]()
            src = source_root / doc.relpath if source_root.is_dir() else source_root
            if doc.fmt == "pdf":
                ext = {"epub": ".epub", "pdf": ".pdf"}.get(args.format, ".txt")
                out = out_root / f"{src.stem}_{suffix}{ext}"
            else:
                out = out_root / f"{src.stem}_{suffix}{src.suffix}"
            bilingual_out = out.with_name(f"{src.stem}_{suffix}_bilingual{out.suffix}")

            if doc.fmt == "pdf" and args.format == "pdf":
                from .pdf_layout import PdfLayoutWriter
                PdfLayoutWriter().write(doc, src, out, bilingual=False, lang=lang,
                                        target_lang_code=suffix)
                TERM.ok(f"导出 {out.name}（{lang} 版面保真译文 PDF）")
                if args.bilingual:
                    PdfLayoutWriter().write(doc, src, bilingual_out, bilingual=True, lang=lang,
                                            target_lang_code=suffix)
                    TERM.ok(f"导出 {bilingual_out.name}（{lang} 双语 PDF）")
                continue
            if doc.fmt == "pdf" and args.format == "epub":
                writer.write_epub(doc, src, out, lang=lang)
            else:
                writer.write(doc, src, out, bilingual=False, source_first=args.source_first,
                             lang=lang, target_lang_code=suffix)
            if args.bilingual:
                if doc.fmt == "pdf" and args.format == "epub":
                    TERM.skip("双语 EPUB 暂不支持，已跳过")
                    continue
                writer.write(doc, src, bilingual_out, bilingual=True, source_first=args.source_first,
                             lang=lang, target_lang_code=suffix)
            done = sum(1 for s in doc.segments if s.is_done(lang))
            total = sum(1 for s in doc.segments if s.status != Status.EXCLUDED)
            TERM.ok(f"导出 {out.name}（{lang}，{done}/{total} 段已翻译）")
    TERM.write(f"输出目录: {out_root}")


# ---------------- preview ----------------

def cmd_preview(args) -> None:
    """渲染书页预览（书页 = 排好版的成品，不是段落表格）。"""
    project_dir = _find_project_dir(args.project)
    cfg = load_config(project_dir)
    manifest = load_project_manifest(project_dir)
    langs = _project_langs(project_dir, cfg, manifest)
    if getattr(args, "lang", None):
        want = [x.strip() for x in str(args.lang).split(",") if x.strip()]
        unknown = [x for x in want if x not in langs]
        if unknown:
            raise UserError(f"未知目标语言: {unknown}", f"项目语言: {langs}")
        langs = want
    store = Store(project_dir)
    docs, stats = store.load(default_lang=(langs or ["chinese_simplified"])[0])
    if not docs:
        raise UserError("项目状态为空")

    from .ir import StyleSheet
    from .layout import write_preview
    sheet = None
    style_file = project_dir / "stylesheet.json"
    if style_file.exists():
        import json
        try:
            data = json.loads(style_file.read_text("utf-8"))
        except (OSError, ValueError):
            data = {}
        # stylesheet.json 是 {relpath: 样式表}，预览时按文档取
        if isinstance(data, dict):
            sheet = next(iter(data.values()), None)

    written: list[Path] = []
    for doc in docs:
        doc_sheet = sheet
        if isinstance(sheet, dict) and doc.relpath in sheet:
            from .ir import StyleSheet
            doc_sheet = StyleSheet.from_dict(sheet[doc.relpath])
        for lang in langs:
            path = write_preview(project_dir, doc, lang=lang, sheet=doc_sheet)
            written.append(path)
    if getattr(args, "out", None):
        out = Path(args.out).expanduser()
        # 多份时合并成一个索引
        if len(written) == 1:
            out.write_text(written[0].read_text("utf-8"), encoding="utf-8")
            TERM.ok(f"预览已写出: {out}")
            written = [out]
        else:
            links = "\n".join(f'<li><a href="{p.name}">{p.name}</a></li>' for p in written)
            out.write_text(f"<!doctype html><meta charset=utf-8><title>预览</title>"
                           f"<ul>{links}</ul>", encoding="utf-8")
            TERM.ok(f"预览索引已写出: {out}（{len(written)} 份）")

    TERM.rule("预览")
    for p in written:
        TERM.kv("书页", str(p))
    if getattr(args, "serve", False):
        import http.server
        import socketserver
        TERM.kv("服务", f"http://127.0.0.1:{args.port}/")
        with socketserver.TCPServer(("127.0.0.1", args.port),
                                    http.server.SimpleHTTPRequestHandler) as httpd:
            httpd.serve_forever()


# ---------------- analyze ----------------

def cmd_analyze(args) -> None:
    project_dir = _find_project_dir(args.project)
    cfg = _load_project_cfg(project_dir)
    _apply_overrides(cfg, args)
    _apply_role(cfg, "analyze", getattr(args, "platform", "") or "")
    store = Store(project_dir)
    docs, _ = store.load()
    if not docs:
        raise UserError("项目状态为空")

    from .glossary import analyze_project
    usage_log = UsageLog(project_dir)
    router = make_router(cfg, FakeProvider() if args.fake else None, usage_log=usage_log)

    async def _run():
        try:
            return await analyze_project(docs, router, cfg.llm.concurrency,
                                         llm_merge=not args.no_llm_merge)
        finally:
            await router.aclose()
            usage_log.close()

    glossary = asyncio.run(_run())
    out = project_dir / "glossary.json"
    glossary.save(out)
    TERM.ok(f"术语分析完成 → {out}")
    TERM.write(f"  角色/术语 {len(glossary.terms)} 条（dst 为空，需人工补译名）· 禁翻 {len(glossary.non_translate)} 条")
    TERM.write("  编辑 glossary.json 补全 dst 列后重新运行 booktran run")


# ---------------- serve ----------------

def cmd_serve(args) -> None:
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError as e:
        raise UserError("网页端需要额外依赖",
                        "pip install 'booktran[web]'（fastapi + uvicorn）") from e
    from .web import build_app  # noqa: PLC0415

    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd()
    app = build_app(root)
    TERM.rule("booktran web")
    TERM.kv("地址", f"http://{args.host}:{args.port}")
    TERM.kv("项目根目录", str(root))
    TERM.write()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


# ---------------- 参数 ----------------

def _apply_overrides(cfg: AppConfig, args) -> None:
    for key in ("provider", "model", "base_url", "api_key", "concurrency", "rpm",
                "max_output_tokens", "prompt_preset"):
        val = getattr(args, key, None)
        if val not in (None, "", 0):
            setattr(cfg.llm, key, val)
    for key in ("source_lang", "chunk_budget_tokens", "rounds",
                "system_prompt", "system_prompt_file", "zh_convert", "layout", "domain"):
        val = getattr(args, key, None)
        if val not in (None, "", 0):
            setattr(cfg.tr, key, val)
    if getattr(args, "target_langs", None):
        cfg.tr.target_langs = [x.strip() for x in str(args.target_langs).split(",") if x.strip()]
    elif getattr(args, "target_lang", None):
        cfg.tr.target_langs = [args.target_lang]
    if getattr(args, "no_strict_placeholders", False):
        cfg.tr.strict_placeholders = False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="booktran", description="书籍翻译工具 (EPUB/TXT/PDF/MOBI)")
    p.add_argument("--version", action="version", version=f"booktran {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--project", "-p", help="项目目录（默认自动查找）")

    def llm_opts(sp):
        sp.add_argument("--provider", help="anthropic | openai | fake")
        sp.add_argument("--model")
        sp.add_argument("--base-url")
        sp.add_argument("--api-key")
        sp.add_argument("--concurrency", type=int)
        sp.add_argument("--rpm", type=int)
        sp.add_argument("--max-output-tokens", type=int)
        sp.add_argument("--platform", help="模型档案名（profiles.toml 中定义）")
        sp.add_argument("--prompt-preset", choices=["default", "local"],
                        help="提示词预设：default（JSON 协议）| local（本地小模型，编号纯文本协议）")
        sp.add_argument("--fake", action="store_true", help="使用假 provider（零成本演示/测试）")

    def prompt_opts(sp):
        sp.add_argument("--system-prompt", help="整体自定义提示词（占位符 {source_language}/{target_language}）")
        sp.add_argument("--system-prompt-file", help="从文件读取整体提示词")

    sp = sub.add_parser("doctor", help="环境体检（依赖 / 密钥 / 档案 / 连通性）")
    sp.add_argument("--test", action="store_true", help="同时发最小请求测试连通性（会产生极小费用）")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("init", help="创建翻译项目")
    sp.add_argument("source", help="输入文件或目录")
    sp.add_argument("--dir", "-d", help="项目目录（默认 ./booktran-<名>）")
    sp.add_argument("--vlm", action="store_true", help="PDF 扫描页交给多模态模型识别")
    sp.add_argument("--ocr", action="store_true",
                    help="PDF 扫描页走 OCR 通道（本地 OCR → 置信度不够自动升级多模态模型）")
    sp.add_argument("--vlm-dpi", type=int, default=150, help="VLM 识别时的渲染 DPI")
    llm_opts(sp)
    prompt_opts(sp)
    sp.add_argument("--source-lang")
    sp.add_argument("--target-lang")
    sp.add_argument("--target-langs", help="目标语言，逗号分隔")
    sp.add_argument("--zh-convert", choices=["s2t", "t2s"], help="简繁转换")
    sp.add_argument("--domain", choices=["religion", "law", "medicine", "academic",
                                         "technical", "literary"],
                    help="领域预设：宗教/法律/医学/学术/技术/文学")
    sp.add_argument("--layout", choices=["original", "model", "hybrid"], default="original",
                    help="排版策略：参照原排版 | 模型设计 | 混合")
    sp.set_defaults(func=cmd_init)

    for name, help_text in (("run", "执行/续翻"), ("translate", "（别名）等同 run")):
        sp = sub.add_parser(name, help=help_text)
        if name == "translate":
            sp.add_argument("--deprecated", action="store_true", help=argparse.SUPPRESS)
        common(sp)
        llm_opts(sp)
        prompt_opts(sp)
        sp.add_argument("--source-lang")
        sp.add_argument("--target-lang")
        sp.add_argument("--target-langs", help="目标语言，逗号分隔")
        sp.add_argument("--chunk-budget-tokens", type=int)
        sp.add_argument("--rounds", type=int)
        sp.add_argument("--zh-convert", choices=["s2t", "t2s"])
        sp.add_argument("--domain", choices=["religion", "law", "medicine", "academic",
                                             "technical", "literary"],
                        help="领域预设：宗教/法律/医学/学术/技术/文学")
        sp.add_argument("--no-strict-placeholders", action="store_true")
        sp.add_argument("--yes", "-y", action="store_true", help="跳过开跑前的成本确认")
        sp.add_argument("--dry-run", action="store_true", help="只做成本试算，不发起请求")
        sp.add_argument("--json", action="store_true", help="机器可读输出（NDJSON 事件流）")
        sp.add_argument("--stage", choices=["translate", "proofread", "all"], default="translate",
                        help="跑哪个阶段：translate（默认）| proofread | all")
        sp.add_argument("--proofread-mode", choices=["qa", "llm", "off"], default="qa",
                        help="校对模式：qa（本地规则）| llm（再加模型审校）| off")
        sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("status", help="查看进度与花费")
    common(sp)
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("cost", help="费用明细（按阶段/角色/模型/语言）")
    common(sp)
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.set_defaults(func=cmd_cost)

    sp = sub.add_parser("export", help="导出译文")
    common(sp)
    sp.add_argument("--out", "-o", help="输出目录（默认 <项目>/output）")
    sp.add_argument("--bilingual", action="store_true", help="同时导出双语版")
    sp.add_argument("--source-first", action="store_true", help="双语版原文在前")
    sp.add_argument("--format", choices=["txt", "epub", "pdf"], default="txt",
                    help="PDF 源的导出格式")
    sp.add_argument("--lang", help="只导出指定目标语言，逗号分隔")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("analyze", help="全文术语分析（生成 glossary.json）")
    common(sp)
    llm_opts(sp)
    sp.add_argument("--no-llm-merge", action="store_true", help="跳过第二阶段的 LLM 合并")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("profile", help="模型档案管理")
    sp.add_argument("action", choices=["list", "add", "remove", "test"])
    sp.add_argument("id", nargs="?", help="档案 id")
    sp.add_argument("--provider")
    sp.add_argument("--model")
    sp.add_argument("--kind", choices=["chat", "vision", "ocr", "local"])
    sp.add_argument("--key-env", help="从哪个环境变量读密钥")
    sp.add_argument("--price-in", type=float, help="输入价格 $/Mtok")
    sp.add_argument("--price-out", type=float, help="输出价格 $/Mtok")
    sp.add_argument("--concurrency", type=int)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("role", help="角色路由（哪个角色用哪个档案）")
    sp.add_argument("action", choices=["list", "set"])
    sp.add_argument("assign", nargs="?", help="<角色>=<档案>")
    sp.set_defaults(func=cmd_role)

    sp = sub.add_parser("proofread", help="成品质检（术语/未翻译/占位符/长度/编号/禁翻）")
    common(sp)
    sp.add_argument("--mode", choices=["qa", "llm"], default="qa",
                    help="qa（本地规则）| llm（再加模型审校，会产生费用）")
    sp.add_argument("--fake", action="store_true", help="使用假 provider（测试/演示）")
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.set_defaults(func=cmd_proofread)

    sp = sub.add_parser("report", help="查看质检报告")
    common(sp)
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("preview", help="渲染书页预览（随翻随排版）")
    common(sp)
    sp.add_argument("--lang", help="预览哪个目标语言（默认全部）")
    sp.add_argument("--out", "-o", help="输出 HTML 文件（默认写进 artifacts/preview/）")
    sp.add_argument("--serve", action="store_true", help="起一个本地静态服务看预览")
    sp.add_argument("--port", type=int, default=8790)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("serve", help="启动网页端")
    sp.add_argument("--root", help="projects 根目录（默认当前目录）")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except UserError as e:
        TERM.error(e)
        raise SystemExit(e.exit_code) from e
    except SystemExit as e:
        # 库内部抛的 SystemExit（带人话消息）→ 统一成带 ✗ 与提示的错误
        if isinstance(e.code, int):
            raise                      # 已经是退出码，原样抛出
        TERM.error(UserError(str(e), exit_code=EXIT_ERROR))
        raise SystemExit(EXIT_ERROR) from e
    except KeyboardInterrupt as e:
        TERM.clear_status()
        TERM.warn("已中断")
        raise SystemExit(EXIT_INTERRUPTED) from e
    except Exception as e:  # noqa: BLE001
        TERM.error(UserError(str(e) or e.__class__.__name__,
                             "加 --traceback 查看完整栈" if False else ""))
        raise SystemExit(EXIT_ERROR) from e


if __name__ == "__main__":
    main()
