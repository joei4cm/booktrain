"""阶段协议与结果。

流水线被切成可独立重跑的阶段：

    recognize → translate → proofread → typeset

每个阶段对每个 (段 × 目标语言) 有独立状态（见 `model.StageState`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# 阶段顺序（下游依赖上游；上游改动会把下游标记为 stale）
STAGE_ORDER = ["recognize", "translate", "proofread", "typeset"]

STAGE_LABELS = {
    "recognize": "识别",
    "translate": "翻译",
    "proofread": "校对",
    "typeset": "排版",
}


@dataclass
class StageResult:
    """一个阶段跑完的结果。"""

    stage: str = ""
    ok: bool = True
    done: int = 0
    failed: int = 0
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageContext:
    """阶段执行上下文（阶段之间共享的可变状态）。"""

    job: Any = None
    stop_event: Any = None
    budget_hit: bool = False


class Stage(Protocol):
    """阶段协议。"""

    name: str

    async def run(self, ctx: StageContext) -> StageResult:
        ...
