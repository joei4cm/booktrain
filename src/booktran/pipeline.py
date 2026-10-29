"""翻译流水线（兼容入口）。

v0.6：真正的编排在 `engine.Engine`（阶段化：translate / proofread），
本模块保留旧 API（`run_pipeline`），内部转成一次只跑 translate 阶段的作业。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .chunker import Chunk, make_chunks
from .config.models import AppConfig
from .engine import Engine, Job, Plan
from .glossary import Glossary
from .llm.router import ModelRouter
from .llm.usage import UsageLog
from .model import SourceDoc, Stats
from .progress import Progress
from .store import Store
from .util import count_tokens

__all__ = ["Progress", "run_pipeline", "make_router", "translate_chunk",
           "Chunk", "make_chunks", "Stats", "guess_source_lang"]


def guess_source_lang(docs: list[SourceDoc]) -> str:
    """按字符分布猜源语言（实现见 stages.translate）。"""
    from .stages.translate import guess_source_lang as _impl
    return _impl(docs)


# 兼容别名（v0.4 内部名）
_guess_source_lang = guess_source_lang


def _resolve_system_prompt(cfg: AppConfig, glossary: Glossary) -> str:
    """兼容别名（旧名）→ stages.translate._resolve_system_prompt。"""
    from .stages.translate import _resolve_system_prompt as _impl
    return _impl(cfg, glossary)


def _profile_from_llm(cfg: AppConfig):
    """没有定义任何档案时，用当前 llm 配置造一个临时档案（兼容旧路径）。"""
    from .config.models import Profile
    llm = cfg.llm
    return Profile(
        id=cfg.active_platform or "default",
        provider=llm.provider, model=llm.model, base_url=llm.base_url,
        api_key=llm.api_key, api_keys=list(llm.api_keys), key_env=llm.api_key_env,
        concurrency=llm.concurrency, rpm=llm.rpm,
        max_output_tokens=llm.max_output_tokens, temperature=llm.temperature,
        timeout=llm.timeout, max_retries=llm.max_retries,
        prompt_preset=llm.prompt_preset,
    )


def make_router(cfg: AppConfig, provider=None, *, usage_log: UsageLog | None = None,
                router: ModelRouter | None = None) -> ModelRouter:
    """拿到一个可用的 router：优先用传入的，否则按 cfg 里的档案现场造。"""
    if router is not None:
        return router
    if not cfg.profiles:
        cfg.profiles["default"] = _profile_from_llm(cfg)
        cfg.sync_legacy_views()
    if provider is not None:
        return ModelRouter(cfg, usage_log=usage_log, factory=lambda _p: provider)
    return ModelRouter(cfg, usage_log=usage_log)


async def translate_chunk(router: ModelRouter, chunk: Chunk, cfg: AppConfig,
                          glossary: Glossary, target_lang: str, source_lang: str):
    """兼容入口（实现见 stages.translate）。"""
    from .stages.translate import translate_chunk as _impl
    return await _impl(router, chunk, cfg, glossary, target_lang, source_lang)


async def run_pipeline(
    docs: list[SourceDoc],
    provider=None,
    cfg: AppConfig | None = None,
    store: Store | None = None,
    stats: Stats | None = None,
    glossary: Glossary | None = None,
    progress: Progress | None = None,
    stop_event: asyncio.Event | None = None,
    *,
    router: ModelRouter | None = None,
    usage_log: UsageLog | None = None,
    budget=None,
    stages: list[str] | None = None,
    proofread_mode: str = "qa",
    project_dir: Path | None = None,
) -> Stats:
    """主入口（many-to-many）。docs 将被就地更新；每完成一段即落事件日志。

    stages=None 时只跑 translate（与 v0.4 行为一致）。
    """
    cfg = cfg or AppConfig()
    store = store or Store(Path.cwd())
    stats = stats or Stats()
    progress = progress or Progress()
    glossary = glossary or Glossary()
    router = make_router(cfg, provider, usage_log=usage_log, router=router)
    if budget is not None:
        cfg.budget = budget

    job = Job(
        docs=docs, cfg=cfg, store=store, stats=stats, glossary=glossary,
        router=router, progress=progress,
        project_dir=project_dir or store.dir,
        usage_log=usage_log,
        plan=Plan(stages=stages or ["translate"], proofread_mode=proofread_mode),
    )
    engine = Engine()
    await engine.run_job(job, stop_event=stop_event)
    if stats.prompt_tokens == 0:
        stats.prompt_tokens = sum(count_tokens(s.source) for doc in docs for s in doc.segments)
    store.save_snapshot(docs, stats)
    await progress.finished()
    return stats
