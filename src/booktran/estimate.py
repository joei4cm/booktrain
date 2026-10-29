"""开跑前的成本估算。

估算口径（写在界面上，用户看得见）：
- 输入 token = 各块正文 token + 系统提示词 + 上文窗口
- 输出 token ≈ 输入正文 token × 1.15（译文略长于原文）
- 区间：低 = 估算值，高 = 估算值 × 1.35（术语表与上文带来的浮动）
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config.models import Profile
from .llm.usage import price_of
from .model import SourceDoc
from .util import count_tokens

SYSTEM_TOKENS = 320        # 系统提示词（基础指令 + 背景 + 术语表）粗估
CONTEXT_TOKENS = 400       # 上文窗口（pre_context_chars）粗估
OUTPUT_RATIO = 1.15        # 译文相对原文的膨胀比
UNCERTAINTY = 1.35         # 高位系数


@dataclass
class Estimate:
    segments: int = 0
    chunks: int = 0
    source_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_low: float | None = None
    cost_high: float | None = None
    priced: bool = True

    @property
    def unknown(self) -> bool:
        return self.cost_low is None


def estimate_cost(docs: list[SourceDoc], profile: Profile | None, *,
                  chunk_budget_tokens: int = 4000,
                  target_langs: int = 1) -> Estimate:
    """估算一次完整翻译的 token 与费用。"""
    active = [s for d in docs for s in d.segments if s.status.value != "excluded"]
    if not active:
        return Estimate(segments=0, chunks=0)
    src_tokens = sum(count_tokens(s.source) for s in active)
    per_chunk = max(1, chunk_budget_tokens)
    chunks = max(1, math.ceil(src_tokens / per_chunk))
    prompt_tokens = chunks * (SYSTEM_TOKENS + CONTEXT_TOKENS) + src_tokens
    completion_tokens = int(src_tokens * OUTPUT_RATIO)
    est = Estimate(
        segments=len(active),
        chunks=chunks,
        source_tokens=src_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    if profile is not None:
        low = price_of(profile, prompt_tokens, completion_tokens)
        if low is None:
            est.priced = False
            est.cost_low = est.cost_high = None
        else:
            est.cost_low = round(low, 4)
            est.cost_high = round(low * UNCERTAINTY, 4)
    else:
        est.priced = False
    return est
