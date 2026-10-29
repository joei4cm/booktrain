"""译文校验链：不通过则整块失败重试，绝不写回缓存（AiNiee 同款原则）。"""
from __future__ import annotations

import re

# 需要在译文中原样保留的占位符类别（可按格式扩展）
PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{?[^{}\n]{1,40}\}?\}"),   # {var} {{var}}
    re.compile(r"%[sdift]"),                  # printf
    re.compile(r"<br\s*/?>", re.IGNORECASE),  # 换行标记
    re.compile(r"【\d+】"),                    # 对话编号
    re.compile(r"&[a-zA-Z]+;"),               # HTML 实体
    re.compile(r"\\n"),                       # 转义换行
]

# 目标语言脚本（用于"译文未翻译"检查）
_TARGET_SCRIPT = {
    "chinese_simplified": re.compile(r"[一-鿿]"),
    "chinese_traditional": re.compile(r"[一-鿿]"),
    "english": re.compile(r"[A-Za-z]"),
    "japanese": re.compile(r"[぀-ヿ一-鿿]"),
    "korean": re.compile(r"[가-힯]"),
}

# 行内标签（EPUB 强调/注音/链接）——丢失只警告，回写时会按原文结构补回
_INLINE_TAG = re.compile(
    r"<\s*/?\s*(i|b|em|strong|u|s|sup|sub|ruby|rt|rp|span|code|small|big|a|img)\b[^>]*>",
    re.IGNORECASE,
)


def inline_tags(text: str) -> set[str]:
    return {m.group(1).lower() for m in _INLINE_TAG.finditer(text or "")}


def extract_placeholders(text: str) -> set[str]:
    found: set[str] = set()
    for pat in PLACEHOLDER_PATTERNS:
        found.update(m.group(0) for m in pat.finditer(text))
    return found


def check_chunk(
    source_lines: list[str],
    translations: dict[int, str],
    target_lang: str,
    strict_placeholders: bool = True,
) -> list[str]:
    """硬校验：结构问题（缺段/空译/丢占位符）。不通过则整块重试。"""
    errors: list[str] = []
    expect = set(range(1, len(source_lines) + 1))
    got = set(translations.keys())
    missing = expect - got
    if missing:
        errors.append(f"缺少 {len(missing)} 段译文: {sorted(missing)[:8]}")
        return errors

    for i, src in enumerate(source_lines, 1):
        tgt = translations.get(i, "")
        if not tgt or not tgt.strip():
            errors.append(f"第 {i} 段译文为空")
            continue
        if strict_placeholders:
            src_ph = extract_placeholders(src)
            tgt_ph = extract_placeholders(tgt)
            lost = src_ph - tgt_ph
            if lost:
                errors.append(f"第 {i} 段丢失占位符: {sorted(lost)[:5]}")
    return errors


def warn_chunk(
    source_lines: list[str],
    translations: dict[int, str],
    target_lang: str,
) -> list[str]:
    """软校验：疑似问题仅警告不重试（如标题/人名原样保留是合法翻译）。"""
    script = _TARGET_SCRIPT.get(target_lang)
    if script is None:
        return []
    warns: list[str] = []
    for i, src in enumerate(source_lines, 1):
        tgt = translations.get(i, "")
        if not tgt:
            continue
        # 行内标签丢失（回写时会按原文结构补回，仅提示）
        src_tags = inline_tags(src)
        if src_tags:
            lost = src_tags - inline_tags(tgt)
            if lost:
                warns.append(f"第 {i} 段行内标签丢失: {sorted(lost)}")
        # 译文与原文完全相同，且不含任何目标语言字符，且原文是实质文本（非短标题）
        if tgt.strip() == src.strip() and not script.search(tgt):
            if len(src.strip()) >= 12 and re.search(r"[a-z]", src):
                warns.append(f"第 {i} 段译文与原文相同（疑似未翻译）: {src.strip()[:30]}")
    return warns


def glossary_violations(
    source_lines: list[str],
    translations: dict[int, str],
    glossary_rows: list[dict],
) -> list[str]:
    """术语遵从检查（软性，仅报告不重试）。"""
    warns: list[str] = []
    for row in glossary_rows:
        src_term = row.get("src", "")
        dst_term = row.get("dst", "")
        if not src_term or not dst_term:
            continue
        for i, line in enumerate(source_lines, 1):
            tgt = translations.get(i, "")
            if src_term in line and dst_term not in tgt:
                warns.append(f"第 {i} 段术语「{src_term}」未按表译为「{dst_term}」")
    return warns
