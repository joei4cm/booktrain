"""校对阶段：产出问题清单（不擅自改写——改写由人在校对面板确认）。

两种模式：
- ``qa``（默认）：本地规则检查（术语 / 未翻译 / 占位符 / 长度 / 编号 / 禁翻）
- ``llm``：在 qa 之上再让模型过一遍，产出"建议"（severity=info）
- ``off``：跳过
"""
from __future__ import annotations

import json

from ..model import StageState
from ..quality.qa import check_document
from ..quality.report import Issue, QualityReport
from .base import StageContext, StageResult

LLM_SYSTEM = """你是一名翻译审校。下面给出若干「原文 → 译文」对照，请找出译文里的问题。

只报**确凿的问题**，不要因为文风偏好而报告。常见问题类型：
- terminology：专名/术语译法前后不一致或明显不当
- mistranslation：误译、漏译、增译
- grammar：语法/语病
- omitted：漏掉的句子成分

只输出 JSON：{"issues":[{"index":<对应编号>,"type":"terminology|mistranslation|grammar|omitted","note":"一句话说明","suggestion":"建议改法"}]}
没有问题的条目不要出现。只输出 JSON。"""

LLM_BATCH = 20


class ProofreadStage:
    """质检 + 生成问题清单。"""

    name = "proofread"

    async def run(self, ctx: StageContext) -> StageResult:
        job = ctx.job
        cfg = job.cfg
        docs = job.docs
        glossary = job.glossary
        progress = job.progress
        mode = (job.plan.proofread_mode or "qa").lower()

        if mode == "off":
            for doc in docs:
                for seg in doc.segments:
                    for lang in cfg.tr.target_langs:
                        seg.track(lang).set_stage(self.name, StageState.SKIPPED)
            return StageResult(stage=self.name, ok=True, detail="已跳过")

        report = QualityReport()
        for lang in cfg.tr.target_langs:
            issues = check_document(docs, lang, glossary,
                                    strict_placeholders=cfg.tr.strict_placeholders)
            report.extend(issues)

        if mode == "llm":
            report.extend(await self._llm_review(ctx))

        for doc in docs:
            for seg in doc.segments:
                for lang in cfg.tr.target_langs:
                    seg.track(lang).set_stage(self.name, StageState.DONE)

        j, m = report.save(job.project_dir)
        s = report.summary()
        progress.term.kv("问题", f"{s['total']} 处"
                                f"（错误 {s['by_severity'].get('error', 0)} · "
                                f"警告 {s['by_severity'].get('warn', 0)} · "
                                f"提示 {s['by_severity'].get('info', 0)}）")
        progress.term.kv("报告", f"{m.name} / {j.name}")
        for issue in report.sorted_issues()[:50]:
            await progress.issue(issue.to_dict())
        return StageResult(stage=self.name, ok=True, detail=f"{s['total']} 个问题",
                           done=s["total"], data={"report": str(j)})

    # ---------------- 模型审校 ----------------
    async def _llm_review(self, ctx: StageContext) -> list[Issue]:
        job = ctx.job
        cfg = job.cfg
        docs = job.docs
        issues: list[Issue] = []
        try:
            from ..llm.router import RouteContext
        except Exception:  # noqa: BLE001
            return issues

        for lang in cfg.tr.target_langs:
            pairs: list[tuple] = []
            for doc in docs:
                for seg in doc.segments:
                    t = seg.track(lang, create=False)
                    if t.status.value == "translated" and t.target:
                        pairs.append((seg, t.target))
            for start in range(0, len(pairs), LLM_BATCH):
                batch = pairs[start:start + LLM_BATCH]
                lines = []
                for i, (seg, tgt) in enumerate(batch, 1):
                    lines.append(f"{i}. 原文：{seg.source[:400]}")
                    lines.append(f"   译文：{tgt[:400]}")
                user = "### 对照\n" + "\n".join(lines) + "\n\n只输出 JSON。"
                try:
                    res, _profile = await job.router.run(
                        "proofread", LLM_SYSTEM, user,
                        ctx=RouteContext(chars=len(user), lang=lang, stage="proofread"),
                        stage="proofread", lang=lang)
                except Exception:  # noqa: BLE001 — 模型审校失败不影响 qa 结果
                    continue
                issues.extend(self._parse_llm(res.text, batch, lang))
        return issues

    def _parse_llm(self, text: str, batch: list[tuple], lang: str) -> list[Issue]:
        out: list[Issue] = []
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return out
        try:
            obj = json.loads(raw[start:end + 1])
        except ValueError:
            return out
        for item in obj.get("issues") or []:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", 0)) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(batch)):
                continue
            seg, tgt = batch[idx]
            out.append(Issue(
                sid=seg.sid, lang=lang, kind="llm",
                severity="info",
                message=str(item.get("note") or item.get("type") or "模型建议"),
                suggestion=str(item.get("suggestion") or ""),
                source=seg.source[:120], target=tgt[:120],
                extra={"type": item.get("type", "")}))
        return out
