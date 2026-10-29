"""排版阶段：产出样式表 + 渲染书页预览（随翻随排版的落点）。

- 样式表写进项目（``stylesheet.json``），导出与预览共用
- 预览产物写进 ``artifacts/preview/``，翻译过程中可反复刷新
"""
from __future__ import annotations

from ..ir import StyleSheet
from ..layout import get_policy, write_preview
from ..model import StageState
from .base import StageContext, StageResult


class TypesetStage:
    """排版策略 + 预览渲染。"""

    name = "typeset"

    async def run(self, ctx: StageContext) -> StageResult:
        job = ctx.job
        cfg = job.cfg
        docs = job.docs
        progress = job.progress

        policy = get_policy(cfg.tr.layout, job.router)
        written: list[str] = []
        sheets: dict[str, StyleSheet] = {}
        for doc in docs:
            sheet = await policy.plan(doc, lang=cfg.tr.target_lang[0] if cfg.tr.target_langs else "",
                                      ctx=ctx)
            sheets[doc.relpath] = sheet
            for lang in cfg.tr.target_langs:
                path = write_preview(job.project_dir, doc, lang=lang, sheet=sheet,
                                     policy=cfg.tr.layout)
                written.append(path.name)

        # 样式表落盘（导出时可复用）
        import json
        style_path = job.project_dir / "stylesheet.json"
        style_path.write_text(
            json.dumps({rel: s.to_dict() for rel, s in sheets.items()},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        for doc in docs:
            for seg in doc.segments:
                for lang in cfg.tr.target_langs:
                    seg.track(lang).set_stage(self.name, StageState.DONE)

        progress.term.kv("排版", f"{cfg.tr.layout} · 预览 {len(written)} 份")
        return StageResult(stage=self.name, ok=True, detail=f"{len(written)} 份预览",
                           done=len(written), data={"stylesheets": str(style_path)})
