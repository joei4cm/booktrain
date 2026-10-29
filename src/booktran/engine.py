"""作业编排：把阶段串起来，可单独重跑某一阶段。

    Job = 一次作业（文档 + 配置 + 状态 + 模型路由 + 计划）
    Engine.run_job(job) → 依次执行计划里的阶段
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config.models import AppConfig
from .glossary import Glossary
from .llm.router import ModelRouter
from .llm.usage import UsageLog
from .model import SourceDoc, StageState, Stats
from .progress import Progress
from .quality.report import QualityReport
from .stages import STAGE_ORDER, STAGES, StageContext, StageResult, stage_label


@dataclass
class Plan:
    """一次作业要做什么。"""

    stages: list[str] = field(default_factory=lambda: ["translate"])
    proofread_mode: str = "qa"          # qa | llm | off
    layout: str = "original"
    restart_stage: str = ""             # 重跑某阶段（把它的下游标记为 stale）

    def stage_list(self) -> list[str]:
        return [s for s in self.stages if s in STAGES]


@dataclass
class Job:
    """一次作业的全部上下文。"""

    docs: list[SourceDoc]
    cfg: AppConfig
    store: Any
    stats: Stats
    glossary: Glossary
    router: ModelRouter
    progress: Progress
    project_dir: Path
    usage_log: UsageLog | None = None
    plan: Plan = field(default_factory=Plan)


@dataclass
class JobResult:
    """作业结果。"""

    stages: list[StageResult] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    elapsed: float = 0.0
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages)

    def to_dict(self) -> dict:
        return {
            "stages": [{"stage": s.stage, "ok": s.ok, "done": s.done,
                        "failed": s.failed, "detail": s.detail} for s in self.stages],
            "issues": len(self.issues),
            "elapsed": round(self.elapsed, 2),
            "stopped": self.stopped,
        }


class Engine:
    """按计划执行阶段。"""

    async def run_job(self, job: Job, *, stop_event: asyncio.Event | None = None) -> JobResult:
        result = JobResult()
        ctx = StageContext(job=job, stop_event=stop_event)
        t0 = time.monotonic()
        plan = job.plan.stage_list() or ["translate"]
        for name in plan:
            stage_cls = STAGES.get(name)
            if stage_cls is None:
                continue
            if stop_event is not None and stop_event.is_set():
                result.stopped = True
                break
            stage = stage_cls()
            job.progress.term.rule(stage_label(name))
            try:
                stage_result = await stage.run(ctx)
            except Exception as e:  # noqa: BLE001 — 单阶段失败不吞掉整场作业
                stage_result = StageResult(stage=name, ok=False, detail=str(e))
                job.progress.term.fail(f"{stage_label(name)} 失败：{e}")
            result.stages.append(stage_result)
            await job.progress.stage_end(name, ok=stage_result.ok,
                                         detail=stage_result.detail,
                                         done=stage_result.done, failed=stage_result.failed)
            if not stage_result.ok:
                break
            if stop_event is not None and stop_event.is_set():
                result.stopped = True
                break
        result.elapsed = time.monotonic() - t0
        result.issues = [i.to_dict() for i in QualityReport.load(job.project_dir).issues]
        return result


def mark_stage_state(job: Job, stage: str, state: StageState) -> int:
    """把某阶段的状态写进所有 track（并把下游标记为 stale）。"""
    n = 0
    for doc in job.docs:
        for seg in doc.segments:
            for lang in job.cfg.tr.target_langs:
                tr = seg.track(lang)
                tr.set_stage(stage, state)
                if state == StageState.RUNNING:
                    tr.invalidate_downstream(stage, STAGE_ORDER)
                n += 1
    return n
