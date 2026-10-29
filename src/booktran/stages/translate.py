"""翻译阶段：轮次调度 + 并发执行 + 失败减半重试。

（原 `pipeline.py` 的核心逻辑搬到这里；`pipeline.run_pipeline` 保留为兼容入口。）
"""
from __future__ import annotations

import asyncio
import time

from ..llm.router import ModelRouter, RouteContext
from ..llm.usage import price_of
from ..model import Segment, SourceDoc, StageState, Status
from ..prompt.builder import build_system, build_user
from ..quality import check_chunk, parse_translations, warn_chunk
from ..util import convert_zh, count_tokens
from .base import StageContext, StageResult


async def translate_chunk(router: ModelRouter, chunk, cfg, glossary,
                          target_lang: str, source_lang: str):
    """翻译单块。返回 (ok, 消息, {序号:译文}, 原始结果, 实际使用的档案)。"""
    lines = chunk.source_lines()
    chunk_text = "\n".join(lines)
    rows = glossary.matched_rows(chunk_text, lang=target_lang) if not glossary.is_empty() else []
    nt = glossary.matched_non_translate(chunk_text)
    custom_prompt = _resolve_system_prompt(cfg, glossary)
    preset = cfg.llm.prompt_preset or "default"
    system = build_system(
        source_lang, target_lang, rows, nt,
        glossary.style if not glossary.is_empty() else "",
        glossary.notes if not glossary.is_empty() else "",
        custom_prompt,
        preset=preset,
        background=glossary.background,
        domain=cfg.tr.domain,
    )
    user = build_user(lines, chunk.context_src, chunk.context_tgt, preset=preset)

    ctx = RouteContext(
        chars=len(chunk_text),
        lang=target_lang,
        source_lang=source_lang,
        glossary_density=(len(rows) / max(1, len(lines))),
        stage="translate",
    )
    result, profile = await router.run("translate", system, user, ctx=ctx,
                                       stage="translate", lang=target_lang)
    translations = parse_translations(result.text, expect_count=len(lines))
    if translations is None:
        return False, "回复无法解析为 JSON/编号结构", None, result, profile
    errors = check_chunk(lines, translations, target_lang, cfg.tr.strict_placeholders)
    if errors:
        return False, "; ".join(errors[:4]), None, result, profile
    return True, "", translations, result, profile


class TranslateStage:
    """把待翻段落翻成目标语言（按段 × 语言独立进度与续跑）。"""

    name = "translate"

    async def run(self, ctx: StageContext) -> StageResult:
        job = ctx.job
        cfg = job.cfg
        docs: list[SourceDoc] = job.docs
        store = job.store
        stats = job.stats
        glossary = job.glossary
        router: ModelRouter = job.router
        progress = job.progress

        target_langs = list(cfg.tr.target_langs) or ["chinese_simplified"]
        for doc in docs:
            for seg in doc.segments:
                seg.ensure_tracks(target_langs)

        # 源语言：配置固定值 → 文档级检测 → **段级检测**（混排书必须逐段判断）
        configured_source = cfg.tr.source_lang if cfg.tr.source_lang != "auto" else ""
        doc_source_lang: dict[str, str] = {}
        for doc in docs:
            lang = configured_source or doc.meta.get("source_lang") or guess_source_lang([doc])
            doc.meta["source_lang"] = lang
            doc_source_lang[doc.relpath] = lang
        seg_source_lang: dict[str, str] = {}
        if not configured_source:
            from ..langdetect import detect_segment_languages
            seg_source_lang = detect_segment_languages(docs, default="english")
            for doc in docs:
                for seg in doc.segments:
                    lang = seg_source_lang.get(seg.sid)
                    if lang:
                        seg.src_lang = lang
        else:
            for doc in docs:
                for seg in doc.segments:
                    seg.src_lang = configured_source

        # 已经译好的段落：translate 阶段天然是 done（续跑时状态要如实）
        for doc in docs:
            for seg in doc.segments:
                for lang in target_langs:
                    tr = seg.track(lang)
                    if tr.status == Status.TRANSLATED:
                        tr.set_stage(self.name, StageState.DONE)

        def pending_count() -> int:
            return sum(
                1 for doc in docs for seg in doc.segments
                if seg.status != Status.EXCLUDED
                for lang in target_langs
                if seg.track(lang, create=False).status != Status.TRANSLATED
            )

        rounds = cfg.tr.rounds
        budget = cfg.budget
        failed = 0
        for round_i in range(1, rounds + 1):
            if ctx.stop_event is not None and ctx.stop_event.is_set():
                break
            jobs: list[tuple[str, list[list[Segment]]]] = []
            for lang in target_langs:
                docs_pending = [
                    [s for s in doc.segments
                     if s.status != Status.EXCLUDED
                     and s.track(lang, create=False).status != Status.TRANSLATED]
                    for doc in docs
                ]
                if any(docs_pending):
                    jobs.append((lang, docs_pending))
            pending = sum(len(p) for _lang, dp in jobs for p in dp)
            if pending == 0:
                break

            budget_tokens = max(cfg.tr.min_budget_tokens,
                                cfg.tr.chunk_budget_tokens // (2 ** (round_i - 1)))
            work: list[tuple[str, str, object]] = []
            for lang, docs_pending in jobs:
                from ..chunker import make_chunks
                # 按段级源语言分组：一个块只讲一种语言（提示词里的 source_language 才准）
                groups: list[list[Segment]] = []
                group_lang: list[str] = []
                for doc_idx, doc_segs in enumerate(docs_pending):
                    rel = docs[doc_idx].relpath if doc_idx < len(docs) else ""
                    buckets: dict[str, list[Segment]] = {}
                    order: list[str] = []
                    for seg in doc_segs:
                        sl = seg.src_lang or doc_source_lang.get(rel, "") or "auto"
                        if sl not in buckets:
                            buckets[sl] = []
                            order.append(sl)
                        buckets[sl].append(seg)
                    for sl in order:
                        groups.append(buckets[sl])
                        group_lang.append(sl)
                for gl, segs_in_group in zip(group_lang, groups, strict=False):
                    for c in make_chunks([segs_in_group], budget_tokens,
                                         cfg.tr.pre_context_chars, lang=lang):
                        work.append((lang, gl, c))
            await progress.stage_start(self.name, total=pending,
                                       lang=jobs[0][0] if jobs else "",
                                       round=round_i, rounds=rounds)
            if progress.term.is_tty:
                progress.term.kv("待翻", f"{pending} 段 · {len(work)} 块 · 预算 {budget_tokens} tok")

            doc_of = {id(s): doc.relpath for doc in docs for s in doc.segments}
            sem = asyncio.Semaphore(cfg.llm.concurrency)
            done_lock = asyncio.Lock()

            # 把本轮的循环变量绑成默认参数：闭包不会跨轮存活（B023 的标准解法）
            async def worker(idx: int, lang: str, src_lang: str, chunk,
                            sem=sem, doc_of=doc_of, work=work, done_lock=done_lock) -> None:
                nonlocal failed
                if ctx.stop_event is not None and ctx.stop_event.is_set():
                    return
                async with sem:
                    doc_rel = doc_of.get(id(chunk.segments[0]), "")
                    if src_lang in ("", "auto"):
                        src_lang = doc_source_lang.get(doc_rel, "auto")
                    label = f"{idx}/{len(work)} {doc_rel} →{lang}"
                    t0 = time.monotonic()
                    try:
                        ok, msg, trans, llm_res, profile = await translate_chunk(
                            router, chunk, cfg, glossary, lang, src_lang)
                    except Exception as e:  # noqa: BLE001 — 请求级重试已耗尽
                        ok, msg, trans, llm_res, profile = False, f"请求失败: {e}", None, None, None
                    dt = time.monotonic() - t0
                    if ok and trans is not None:
                        if cfg.tr.zh_convert and lang.startswith("chinese"):
                            trans = {i: convert_zh(t, cfg.tr.zh_convert)
                                     for i, t in trans.items()}
                        for i, seg in enumerate(chunk.segments, 1):
                            t = seg.track(lang)
                            t.target = trans.get(i, seg.source)
                            t.status = Status.TRANSLATED
                            t.error = None
                            t.model = getattr(profile, "model", "")
                            t.set_stage(self.name, StageState.DONE)
                            store.append_event(seg, lang)
                        async with done_lock:
                            stats.requests += 1
                            if llm_res is not None:
                                stats.add_usage(llm_res.prompt_tokens,
                                                llm_res.completion_tokens)
                                cost = price_of(profile, llm_res.prompt_tokens,
                                                llm_res.completion_tokens)
                                stats.add_cost(cost)
                                progress.add_cost(cost)
                        warns = warn_chunk(chunk.source_lines(), trans, lang)
                        detail = f"{len(chunk.segments)} 段 · {chunk.prompt_tokens} tok · {dt:.1f}s"
                        if warns:
                            detail += " · ⚠ " + "; ".join(warns[:2])
                        await progress.chunk_done(label, True, detail)
                        if store.pending_compaction:
                            store.save_snapshot(docs, stats)
                            store.pending_compaction = False
                        if budget and budget.max_cost and stats.cost >= budget.max_cost:
                            if ctx.stop_event is not None:
                                ctx.stop_event.set()
                            ctx.budget_hit = True
                            progress.term.warn(
                                f"预算已达上限 ${budget.max_cost:g}，作业已暂停"
                                f"（booktran config set budget.max_cost {budget.max_cost * 2:g}）")
                            return
                    else:
                        failed += 1
                        stats.failed_chunks += 1
                        for seg in chunk.segments:
                            tr = seg.track(lang)
                            tr.error = msg[:300]
                            tr.set_stage(self.name, StageState.FAILED)
                        await progress.chunk_done(label, False, msg[:120])

            await asyncio.gather(*(worker(i, lang, gl, c)
                                   for i, (lang, gl, c) in enumerate(work, 1)))
            store.save_snapshot(docs, stats)
            # 随翻随排版：每轮结束刷新一次书页预览
            await _refresh_previews(job)

            remaining = pending_count()
            if remaining and round_i == rounds:
                progress.term.warn(
                    f"达到最大轮次，仍有 {remaining} 个(段×语言)未翻译（保留 pending，可再次运行继续）")

        if stats.prompt_tokens == 0:
            stats.prompt_tokens = sum(count_tokens(s.source) for doc in docs for s in doc.segments)
        store.save_snapshot(docs, stats)
        return StageResult(stage=self.name, done=progress.done, failed=failed)


async def _refresh_previews(job) -> None:
    """随翻随排版：把当前进度渲染成书页预览（未译段落显示原文占位）。"""
    try:
        from ..ir import StyleSheet
        from ..layout import write_preview
        project_dir = getattr(job, "project_dir", None)
        if not project_dir:
            return
        sheet = None
        style_file = project_dir / "stylesheet.json"
        if style_file.exists():
            import json
            try:
                data = json.loads(style_file.read_text("utf-8"))
            except (OSError, ValueError):
                data = {}
            if isinstance(data, dict):
                sheet = next(iter(data.values()), None)
        for doc in job.docs:
            doc_sheet = StyleSheet.from_dict(sheet) if isinstance(sheet, dict) else None
            for lang in job.cfg.tr.target_langs:
                write_preview(project_dir, doc, lang=lang, sheet=doc_sheet)
    except Exception:  # noqa: BLE001 — 预览失败不该影响翻译
        pass


def _resolve_system_prompt(cfg, glossary) -> str:
    """整体提示词：命令行/配置覆盖 > glossary.json 的 system_prompt > 内置模板。"""
    from pathlib import Path
    if cfg.tr.system_prompt_file:
        try:
            return Path(cfg.tr.system_prompt_file).expanduser().read_text("utf-8")
        except OSError as e:
            raise SystemExit(f"读取 system_prompt_file 失败: {e}") from e
    if cfg.tr.system_prompt:
        return cfg.tr.system_prompt
    return glossary.system_prompt


def guess_source_lang(docs: list[SourceDoc]) -> str:
    """按字符分布猜源语言（出现假名即日语）。显式配置 source_lang 总是更可靠。"""
    import re
    text = "".join(seg.source for doc in docs[:3] for seg in doc.segments[:200])
    if re.search(r"[぀-ヿ]", text):
        return "japanese"
    if re.search(r"[가-힯]", text):
        return "korean"
    if re.search(r"[一-鿿]", text):
        return "chinese_simplified"
    return "english"
