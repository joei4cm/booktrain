"""阶段注册表。"""
from __future__ import annotations

from .base import STAGE_LABELS, STAGE_ORDER, Stage, StageContext, StageResult
from .proofread import ProofreadStage
from .translate import TranslateStage
from .typeset import TypesetStage

__all__ = ["STAGE_ORDER", "STAGE_LABELS", "Stage", "StageContext", "StageResult",
           "TranslateStage", "ProofreadStage", "TypesetStage", "STAGES", "stage_label"]

#: 阶段名 → 阶段实现
STAGES: dict[str, type] = {
    "translate": TranslateStage,
    "proofread": ProofreadStage,
    "typeset": TypesetStage,
}


def stage_label(name: str) -> str:
    return STAGE_LABELS.get(name, name)
