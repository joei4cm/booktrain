"""成品质检规则（qa 模式）：把译文里的可疑之处变成可操作的问题清单。

规则：
- 术语不一致：原文命中术语表，译文里没按表译
- 疑似未翻译：译文与原文相同（且不像专名）
- 占位符丢失：原文的占位符没出现在译文里
- 长度异常：译文长度相对原文异常（过短或过长）
- 编号残留：译文里留着模型输出的编号
- 禁翻表残留：禁翻词条没原样保留
- 空译文：已标记为已译但内容为空
"""
from __future__ import annotations

import re

from ..model import Segment, Status
from .checks import extract_placeholders
from .report import Issue

# 译文里不该出现的编号（模型把协议编号带进来了）
_NUMBERING = re.compile(r"^\s*\d{1,3}[.、)]\s*", re.MULTILINE)
# 疑似复读：译文里整段包含原文长片段
_REPEAT_MIN = 12


def check_segment(seg: Segment, lang: str, glossary, *,
                  strict_placeholders: bool = True,
                  length_ratio_low: float = 0.35,
                  length_ratio_high: float = 3.0) -> list[Issue]:
    """检查一个段落在一个目标语言上的译文，返回问题清单。"""
    issues: list[Issue] = []
    src = seg.source or ""
    tgt = (seg.tracks.get(lang).target if lang in seg.tracks else "") or ""
    if seg.status == Status.EXCLUDED:
        return issues
    if not tgt.strip():
        issues.append(Issue(sid=seg.sid, lang=lang, kind="empty", severity="error",
                            message="已标记为已译但内容为空", source=src[:120]))
        return issues

    # 术语遵从
    if glossary is not None and not glossary.is_empty():
        for row in glossary.matched_rows(src, lang=lang):
            dst = row.get("dst") or ""
            if dst and dst not in tgt:
                issues.append(Issue(
                    sid=seg.sid, lang=lang, kind="glossary", severity="warn",
                    message=f"术语「{row.get('src', '')}」未按表译为「{dst}」",
                    suggestion=dst, source=src[:120], target=tgt[:120]))

    # 占位符
    src_ph = extract_placeholders(src)
    if src_ph:
        tgt_ph = extract_placeholders(tgt)
        lost = src_ph - tgt_ph
        if lost and strict_placeholders:
            issues.append(Issue(
                sid=seg.sid, lang=lang, kind="placeholder", severity="error",
                message=f"占位符丢失：{', '.join(sorted(lost)[:5])}",
                source=src[:120], target=tgt[:120]))

    # 疑似未翻译
    if tgt.strip() == src.strip() and len(src.strip()) >= _REPEAT_MIN:
        issues.append(Issue(
            sid=seg.sid, lang=lang, kind="untranslated", severity="error",
            message="译文与原文完全相同，疑似未翻译",
            source=src[:120], target=tgt[:120]))

    # 长度异常
    if src.strip():
        ratio = len(tgt.strip()) / len(src.strip())
        if ratio < length_ratio_low or ratio > length_ratio_high:
            issues.append(Issue(
                sid=seg.sid, lang=lang, kind="length", severity="info",
                message=f"译文长度是原文的 {ratio:.2f} 倍（正常 0.35–3.0）",
                source=src[:120], target=tgt[:120]))

    # 编号残留
    if _NUMBERING.search(tgt):
        issues.append(Issue(
            sid=seg.sid, lang=lang, kind="numbering", severity="warn",
            message="译文里残留了段落编号", source=src[:120], target=tgt[:120]))

    # 禁翻表残留
    if glossary is not None:
        for token in glossary.matched_non_translate(src):
            if token not in tgt:
                issues.append(Issue(
                    sid=seg.sid, lang=lang, kind="non_translate", severity="warn",
                    message=f"禁翻项「{token}」未原样保留",
                    suggestion=token, source=src[:120], target=tgt[:120]))

    return issues


def check_document(docs, lang: str, glossary, *, strict_placeholders: bool = True) -> list[Issue]:
    """检查整份文档（一个目标语言）。"""
    out: list[Issue] = []
    for doc in docs:
        for seg in doc.segments:
            if seg.status == Status.EXCLUDED:
                continue
            if not seg.track(lang, create=False).status == Status.TRANSLATED:
                continue
            out.extend(check_segment(seg, lang, glossary,
                                      strict_placeholders=strict_placeholders))
    return out
