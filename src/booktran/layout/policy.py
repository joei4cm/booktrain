"""排版策略：参照原排版 / 模型设计 / 混合。

- ``original``：样式表从源文档继承，位置锚点原样保留
- ``model``：模型产出样式表（JSON 约束 + 校验），失败自动回退 original
- ``hybrid``：模型产出样式表，但保留源里的位置锚点

产物是 `ir.StyleSheet`；渲染由 `layout.render` 负责（预览与导出共用）。
"""
from __future__ import annotations

import json
import re

from ..ir import StyleSheet
from ..llm.router import RouteContext

# 模型产出样式表时的提示词（只给意图，不给全文——省 token 也少跑题）
TYPESET_SYSTEM = """你是一名书籍排版设计师。下面给你一本书的结构摘要（章节标题与段落类型），
请设计一份**排版样式表**，让这本书读起来舒服、像出版物。

只输出 JSON，结构如下（所有值都是 CSS 风格的值；没有把握的字段可以省略）：
{
  "body": {"font_family": "serif", "font_size": "18px", "line_height": "1.9",
           "text_align": "justify", "margin_after": "0.9em"},
  "headings": {"1": {"font_size": "1.6em", "font_weight": "700",
                     "margin_before": "1.6em", "margin_after": "0.6em",
                     "text_align": "left"}},
  "paragraph": {"text_indent": "2em", "margin_after": "0.6em"},
  "page": {"margin_top": "20mm", "margin_bottom": "20mm",
           "margin_inner": "18mm", "margin_outer": "14mm"}
}

要求：
- 正文字体用衬线（serif），标题可用无衬线或衬线，但要形成层级
- 行高 1.6–2.0；正文两端对齐；首行缩进 2em 是中文书籍的常见做法
- 只针对给出的语言与题材做设计，不要编造不存在的章节
只输出 JSON。"""


def _schema_hint(structure: dict) -> str:
    """结构摘要（章节标题 + 段落类型统计）——给模型看，不给全文。"""
    lines = []
    titles = structure.get("titles") or []
    if titles:
        lines.append("章节标题：")
        lines.extend(f"- {t}" for t in titles[:30])
    kinds = structure.get("kinds") or {}
    if kinds:
        lines.append("段落类型：" + "、".join(f"{k}×{v}" for k, v in kinds.items()))
    if structure.get("sample"):
        lines.append("开篇片段：" + structure["sample"][:300])
    return "\n".join(lines)


def doc_structure(doc, limit: int = 30) -> dict:
    """文档结构摘要（给排版模型看）。"""
    titles: list[str] = []
    kinds: dict[str, int] = {}
    sample = ""
    for seg in doc.segments:
        kind = getattr(seg, "kind", "para") or "para"
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "heading" and len(titles) < limit:
            titles.append(seg.source[:60])
        if not sample and len(seg.source) > 40:
            sample = seg.source[:300]
    return {"titles": titles, "kinds": kinds, "sample": sample}


class OriginalPolicy:
    """参照原排版：样式继承自源文档。"""

    name = "original"

    async def plan(self, doc, *, lang: str = "", ctx=None) -> StyleSheet:
        return StyleSheet.from_source(doc.segments)

    def render(self, doc, *, lang: str = "", sheet: StyleSheet | None = None) -> str:
        from .render import render_html
        return render_html(doc, lang=lang, sheet=sheet or StyleSheet.from_source(doc.segments))


class ModelPolicy:
    """模型设计排版：模型产出样式表，失败回退 original。"""

    name = "model"

    def __init__(self, router=None, *, fallback: OriginalPolicy | None = None) -> None:
        self.router = router
        self.fallback = fallback or OriginalPolicy()

    async def plan(self, doc, *, lang: str = "", ctx=None) -> StyleSheet:
        base = await self.fallback.plan(doc, lang=lang, ctx=ctx)
        if self.router is None:
            return base
        structure = doc_structure(doc)
        user = f"### 结构\n{_schema_hint(structure)}\n\n只输出 JSON。"
        try:
            res, _profile = await self.router.run(
                "typeset", TYPESET_SYSTEM, user,
                ctx=RouteContext(chars=len(user), lang=lang, stage="typeset"),
                stage="typeset", lang=lang)
        except Exception:  # noqa: BLE001 — 排版失败不该毁掉一本书
            return base
        parsed = parse_stylesheet(res.text)
        if parsed is None:
            return base
        return base.merged_with(parsed)

    def render(self, doc, *, lang: str = "", sheet: StyleSheet | None = None) -> str:
        from .render import render_html
        return render_html(doc, lang=lang, sheet=sheet or StyleSheet.from_source(doc.segments))


class HybridPolicy:
    """混合：模型样式表 + 源位置锚点。"""

    name = "hybrid"

    def __init__(self, router=None) -> None:
        self.inner = ModelPolicy(router)

    async def plan(self, doc, *, lang: str = "", ctx=None) -> StyleSheet:
        return await self.inner.plan(doc, lang=lang, ctx=ctx)

    def render(self, doc, *, lang: str = "", sheet: StyleSheet | None = None) -> str:
        from .render import render_html
        return render_html(doc, lang=lang, sheet=sheet or StyleSheet.from_source(doc.segments),
                           keep_anchors=True)


def parse_stylesheet(text: str) -> StyleSheet | None:
    """解析模型返回的样式表 JSON（容错：代码块 / 前后废话）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return StyleSheet.from_dict(obj)


def get_policy(name: str, router=None):
    """按名字取策略。"""
    name = (name or "original").lower()
    if name == "model":
        return ModelPolicy(router)
    if name == "hybrid":
        return HybridPolicy(router)
    return OriginalPolicy()
