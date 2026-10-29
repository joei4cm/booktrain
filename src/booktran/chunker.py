"""切块：token 预算内聚合连续段，不跨文档；附带上一块的上下文窗口。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Segment
from .util import count_tokens


@dataclass
class Chunk:
    segments: list[Segment]
    prompt_tokens: int = 0            # 预估输入 token（原文部分）
    context_src: list[str] = field(default_factory=list)   # 上文原文（含已译对照）
    context_tgt: list[str] = field(default_factory=list)   # 上文对应译文（可空 = 未译）

    def source_lines(self) -> list[str]:
        return [s.source for s in self.segments]


def build_chunk(segments: list[Segment]) -> Chunk:
    return Chunk(segments=segments, prompt_tokens=sum(count_tokens(s.source) for s in segments))


def make_chunks(
    docs_segments: list[list[Segment]],
    budget_tokens: int,
    pre_context_chars: int = 1200,
    lang: str = "",
) -> list[Chunk]:
    """docs_segments: 每个文档的待翻译段列表（按文档分组，块不跨文档）。

    lang: 目标语言，用于从 track 取上文译文（可空 = 用兼容 final_text）。
    上文取本块之前（同文档、顺序在前）的最近若干段，总量截断到 pre_context_chars。
    """
    chunks: list[Chunk] = []
    for segs in docs_segments:
        current: list[Segment] = []
        current_tokens = 0
        for seg in segs:
            t = count_tokens(seg.source)
            if current and current_tokens + t > budget_tokens:
                chunks.append(build_chunk(current))
                current, current_tokens = [], 0
            current.append(seg)
            current_tokens += t
        if current:
            chunks.append(build_chunk(current))

    # 附加上下文：在原始全量序列里找每块前面的段
    for doc_segs in docs_segments:
        index = {id(s): i for i, s in enumerate(doc_segs)}
        for chunk in chunks:
            if not chunk.segments:
                continue
            start = index.get(id(chunk.segments[0]))
            if start is None or start == 0:
                continue
            ctx: list[Segment] = []
            used = 0
            for seg in reversed(doc_segs[:start]):
                if used + len(seg.source) > pre_context_chars:
                    break
                ctx.insert(0, seg)
                used += len(seg.source)
            chunk.context_src = [s.source for s in ctx]
            if lang:
                chunk.context_tgt = [s.translated_text(lang) for s in ctx]
            else:
                chunk.context_tgt = [s.final_text for s in ctx]
    return chunks
