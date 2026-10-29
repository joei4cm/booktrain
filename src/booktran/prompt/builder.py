"""提示词组装：系统指令 + 术语表 + 上文 + 编号原文（JSON 协议）。"""
from __future__ import annotations

import json

LANG_ZH = {
    "chinese_simplified": "简体中文", "chinese_traditional": "繁體中文",
    "english": "英语", "japanese": "日语", "korean": "韩语",
    "french": "法语", "german": "德语", "spanish": "西班牙语",
    "russian": "俄语", "italian": "意大利语", "portuguese": "葡萄牙语",
    "arabic": "阿拉伯语", "vietnamese": "越南语", "thai": "泰语",
    "hindi": "印地语", "turkish": "土耳其语", "dutch": "荷兰语",
    "polish": "波兰语", "indonesian": "印尼语", "auto": "原文语言",
}

LANG_CODE = {
    "chinese_simplified": "zh", "chinese_traditional": "zh-TW",
    "english": "en", "japanese": "ja", "korean": "ko",
    "french": "fr", "german": "de", "spanish": "es", "russian": "ru",
    "italian": "it", "portuguese": "pt", "arabic": "ar", "vietnamese": "vi",
    "thai": "th", "hindi": "hi", "turkish": "tr", "dutch": "nl",
    "polish": "pl", "indonesian": "id",
}


def lang_display(lang: str) -> str:
    """语言显示名。未收录的语言名原样透传（支持任意语言对）。"""
    return LANG_ZH.get(lang, lang.replace("_", " "))


def lang_code(lang: str) -> str:
    """语言代码（文件名后缀 / html lang）。未收录的语言名原样透传。"""
    return LANG_CODE.get(lang, lang.replace("_", "-"))


SYSTEM_TEMPLATE = """你是一名专业译者，正在将一本{source_language}书籍完整翻译为{target_language}。

## 翻译要求
- 忠实原意，不删减、不增译、不添加解释或注释
- 译文符合{target_language}的文学表达习惯，自然流畅
- 逐段翻译：一个编号对应一段译文，不得合并、拆分或遗漏段落
- 保留原文中的特殊记号并在译文中原样保留：占位符（{{var}}、%s、<br>、【0-9】编号等）、转义符、代码片段
- 保留行内标签（<i>/<b>/<ruby>/<a> 等）本身，并翻译其包裹的文字，标签位置对应原文
- 人名与术语严格遵循下方术语表（如有）；术语表未覆盖的译名保持全书统一
- 对话语气、人称、视角与原文一致；原文的直白或粗俗表述如实翻译，不作淡化

{glossary_section}## 输出格式
只输出一个 JSON 对象，不要输出其他任何内容，不要用代码块包裹：
{{"translations":[{{"i":<段号>,"t":"<对应译文>"}}]}}
多行译文在 JSON 字符串中用 \\n 表示。"""

# 使用自定义整体提示词时，协议段永远追加（保证输出可解析）
OUTPUT_FORMAT = """## 输出格式
只输出一个 JSON 对象，不要输出其他任何内容，不要用代码块包裹：
{"translations":[{"i":<段号>,"t":"<对应译文>"}]}
多行译文在 JSON 字符串中用 \\n 表示。"""

# ---- 本地小模型预设：指令更短，输出用编号纯文本协议（本地模型常把 JSON 写坏） ----
LOCAL_SYSTEM_TEMPLATE = """你是翻译引擎。把{source_language}原文翻译为{target_language}。

规则：
1. 一段一行译文，格式「序号.译文」，序号与原文编号一致
2. 不合并、不拆分、不遗漏、不新增编号
3. 占位符与记号原样保留：{{...}}、%s、<br>、【数字】
4. 术语表（若有）严格照用；对话语气与人称与原文一致
{glossary_section}"""

LOCAL_OUTPUT_FORMAT = """## 输出格式
每行一段译文，格式「序号.译文」。只输出译文行，不要输出其他内容。"""


def _sections(glossary_rows, non_translate, style, notes, background=None,
              domain: str = "") -> str:
    """领域/背景/术语表/禁翻表/文风插槽（内置模板与自定义提示词共用）。"""
    parts = []
    # 领域预设（L1.5 层）：在作品背景之前注入，专业书的"背景知识范围"
    from .presets import preset_text
    domain_text = preset_text(domain) if domain else ""
    if domain_text:
        parts.append("\n" + domain_text + "\n")
    bg_text = _background_text(background)
    if bg_text:
        parts.append("\n## 作品背景\n" + bg_text + "\n")
    if glossary_rows:
        lines = ["", "## 术语表（原文|译文|备注）"]
        for row in glossary_rows:
            note = row.get("note") or " "
            lines.append(f"{row['src']}|{row['dst']}|{note}")
        parts.append("\n".join(lines))
    if non_translate:
        lines = ["", "## 禁翻表（以下内容在译文中保持原样）"]
        lines.extend(non_translate)
        parts.append("\n".join(lines))
    if style:
        parts.append(f"\n## 翻译风格\n{style}\n")
    if notes:
        parts.append(f"\n## 其他要求\n{notes}\n")
    return ("\n".join(parts) + "\n") if parts else ""


# 背景字段的中文标签（顺序即展示顺序）
BACKGROUND_LABELS = [
    ("title", "书名/作品名"),
    ("genre", "题材类型"),
    ("era", "时代/世界观"),
    ("setting", "背景设定"),
    ("tone", "基调/氛围"),
    ("characters", "主要人物与关系"),
    ("naming", "命名/称呼规则"),
    ("terminology", "专有名词背景"),
    ("audience", "目标读者"),
    ("notes", "其他背景信息"),
]


def normalize_background(bg) -> dict:
    """把任意输入规整成 {字段: 文本}（未知字段并入 notes）。"""
    if not bg:
        return {}
    if isinstance(bg, str):
        return {"notes": bg.strip()} if bg.strip() else {}
    if not isinstance(bg, dict):
        return {}
    keys = {k for k, _ in BACKGROUND_LABELS}
    out = {k: str(bg.get(k) or "").strip() for k in keys if str(bg.get(k) or "").strip()}
    extra = {k: str(v).strip() for k, v in bg.items() if k not in keys and str(v or "").strip()}
    if extra:
        merged = "\n".join(f"{k}: {v}" for k, v in extra.items())
        out["notes"] = (out.get("notes", "") + "\n" + merged).strip()
    return out


def _background_text(background) -> str:
    bg = normalize_background(background)
    if not bg:
        return ""
    lines = []
    for key, label in BACKGROUND_LABELS:
        if key in bg:
            lines.append(f"- {label}: {bg[key]}")
    return "\n".join(lines) or ""


def build_system(
    source_lang: str,
    target_lang: str,
    glossary_rows: list[dict] | None = None,
    non_translate: list[str] | None = None,
    style: str = "",
    notes: str = "",
    custom_prompt: str = "",
    preset: str = "default",
    background=None,
    domain: str = "",
) -> str:
    """组装系统提示词。

    custom_prompt 非空 = 整体自定义提示词（替换内置模板），
    占位符 {source_language}/{target_language}/{glossary} 可用；
    背景/术语表/禁翻表/文风插槽与输出协议段仍会追加，保证协议稳定。
    preset="local" 时使用本地小模型预设（更短指令 + 编号纯文本协议）。
    """
    sections = _sections(glossary_rows, non_translate, style, notes, background,
                        domain=domain)
    local = preset == "local"
    if custom_prompt and custom_prompt.strip():
        base = (custom_prompt
                .replace("{source_language}", lang_display(source_lang))
                .replace("{target_language}", lang_display(target_lang)))
        if "## 输出格式" in base:
            return base + (("\n" + sections) if sections else "")
        fmt = LOCAL_OUTPUT_FORMAT if local else OUTPUT_FORMAT
        return f"{base.rstrip()}\n\n{sections}{fmt}"
    if local:
        return LOCAL_SYSTEM_TEMPLATE.format(
            source_language=lang_display(source_lang),
            target_language=lang_display(target_lang),
            glossary_section=sections,
        )
    return SYSTEM_TEMPLATE.format(
        source_language=lang_display(source_lang),
        target_language=lang_display(target_lang),
        glossary_section=sections,
        style_section="",
    )


def build_user(
    source_lines: list[str],
    context_src: list[str] | None = None,
    context_tgt: list[str] | None = None,
    preset: str = "default",
) -> str:
    parts: list[str] = []
    if context_src:
        parts.append("### 上文（供理解语境，不要翻译）")
        for src, tgt in zip(context_src, context_tgt or [""] * len(context_src), strict=False):
            if tgt:
                parts.append(f"{src}\n→ {tgt}")
            else:
                parts.append(src)
        parts.append("")
    numbered = "\n".join(f"{i}.{line}" for i, line in enumerate(source_lines, 1))
    parts.append(f"### 待翻译段落\n{numbered}\n\n"
                 + ("只输出译文行。" if preset == "local" else "只输出 JSON。"))
    return "\n".join(parts)


# ---------- 全文术语分析（两阶段的第一阶段） ----------

ANALYZE_SYSTEM = """你是一名翻译项目的前期分析助手。通读给定的书籍片段，提取：
1. characters: 出场人物（原文写法）
2. terms: 反复出现的人名以外的专有名词（地名/组织/魔法/物品等，原文写法）
3. non_translate: 不应翻译、需原样保留的记号（变量名/代码/品牌等）

只输出 JSON：{"characters":[{"name":"..","note":"身份/性别，可空"}],
"terms":[{"name":"..","note":"含义，可空"}],"non_translate":[".."]}
没有则输出空数组。不要输出其他内容。"""


def build_analyze_user(source_lines: list[str]) -> str:
    numbered = "\n".join(f"- {line}" for line in source_lines)
    return f"### 书籍片段\n{numbered}\n\n只输出 JSON。"


def dumps_compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
