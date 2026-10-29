"""文档中间表示（IR）：与格式无关的段落结构 + 排版样式表。

**单一存储原则**：IR 的字段直接长在 `model.Segment` 上
（`kind` / `level` / `spans` / `anchors` / `style` / `src_lang`），
本模块只提供**结构化视图**与渲染工具——不产生第二份真相。

排版策略（`layout`）：
- ``original``：样式表从源文档继承（`StyleSheet.from_source`）
- ``model``：样式表由模型产出（JSON schema 约束），渲染器按它落盘
- ``hybrid``：模型产出样式表，但保留源里的位置锚点
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 段落种类
KIND_PARA = "para"
KIND_HEADING = "heading"
KIND_LIST = "list"
KIND_QUOTE = "quote"
KIND_CODE = "code"
KIND_CAPTION = "caption"
KIND_FOOTNOTE = "footnote"
KIND_PAGEBREAK = "pagebreak"

# 块级标签 → (kind, level)
TAG_KINDS: dict[str, tuple[str, int]] = {
    "h1": (KIND_HEADING, 1), "h2": (KIND_HEADING, 2), "h3": (KIND_HEADING, 3),
    "h4": (KIND_HEADING, 4), "h5": (KIND_HEADING, 5), "h6": (KIND_HEADING, 6),
    "p": (KIND_PARA, 0), "div": (KIND_PARA, 0), "section": (KIND_PARA, 0),
    "li": (KIND_LIST, 0), "blockquote": (KIND_QUOTE, 0), "pre": (KIND_CODE, 0),
    "td": (KIND_PARA, 0), "th": (KIND_PARA, 0), "dt": (KIND_PARA, 0),
    "dd": (KIND_PARA, 0), "figcaption": (KIND_CAPTION, 0), "caption": (KIND_CAPTION, 0),
    "title": (KIND_HEADING, 1),
}


def kind_of_tag(tag: str) -> tuple[str, int]:
    """块级标签 → (kind, level)。"""
    return TAG_KINDS.get((tag or "").lower(), (KIND_PARA, 0))


@dataclass
class Span:
    """行内标记（粗体/斜体/链接/注音）。"""

    text: str = ""
    bold: bool = False
    italic: bool = False
    href: str = ""
    ruby_base: str = ""
    ruby_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Span:
        return cls(
            text=d.get("text", ""), bold=bool(d.get("bold")),
            italic=bool(d.get("italic")), href=d.get("href", ""),
            ruby_base=d.get("ruby_base", ""), ruby_text=d.get("ruby_text", ""))


@dataclass
class StyleSheet:
    """排版样式表（original / model 两种来源共用同一结构）。"""

    body: dict = field(default_factory=lambda: {
        "font_family": "", "font_size": "18px", "line_height": "1.9",
        "text_align": "justify", "margin_after": "0.9em",
        "color": "#1a1a1a", "background": "#ffffff",
    })
    headings: dict[int, dict] = field(default_factory=dict)
    paragraph: dict = field(default_factory=dict)
    page: dict = field(default_factory=lambda: {
        "width": "210mm", "height": "297mm",
        "margin_top": "20mm", "margin_bottom": "20mm",
        "margin_inner": "18mm", "margin_outer": "14mm",
    })

    def to_dict(self) -> dict:
        return {
            "body": dict(self.body),
            "headings": {str(k): dict(v) for k, v in self.headings.items()},
            "paragraph": dict(self.paragraph),
            "page": dict(self.page),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> StyleSheet:
        d = d or {}
        s = cls()
        if isinstance(d.get("body"), dict):
            s.body.update(d["body"])
        if isinstance(d.get("paragraph"), dict):
            s.paragraph.update(d["paragraph"])
        if isinstance(d.get("page"), dict):
            s.page.update(d["page"])
        for k, v in (d.get("headings") or {}).items():
            try:
                s.headings[int(k)] = dict(v or {})
            except (TypeError, ValueError):
                continue
        return s

    @classmethod
    def from_source(cls, segments) -> StyleSheet:
        """original 策略：从源文档继承样式（有标题就配标题样式）。"""
        s = cls()
        levels = [seg.level for seg in segments if getattr(seg, "kind", "") == KIND_HEADING]
        if levels:
            base = 1.55
            step = 0.82
            for lv in sorted(set(levels)):
                size = round(base * (step ** (lv - 1)), 2)
                s.headings[lv] = {
                    "font_size": f"{size}em",
                    "font_weight": "700",
                    "margin_before": f"{round(size * 1.1, 2)}em",
                    "margin_after": f"{round(size * 0.45, 2)}em",
                    "text_align": "left",
                }
        return s

    def merged_with(self, other: StyleSheet | dict | None) -> StyleSheet:
        """模型产出的样式表覆盖继承来的（hybrid：样式换、位置留）。"""
        if other is None:
            return self
        o = other if isinstance(other, StyleSheet) else StyleSheet.from_dict(other)
        merged = StyleSheet.from_dict(self.to_dict())
        merged.body.update({k: v for k, v in o.body.items() if v})
        merged.paragraph.update({k: v for k, v in o.paragraph.items() if v})
        for k, v in o.page.items():
            if v:
                merged.page[k] = v
        for lv, v in o.headings.items():
            merged.headings.setdefault(lv, {}).update({k: x for k, x in v.items() if x})
        return merged


@dataclass
class BlockView:
    """段落的结构化视图（字段来自 Segment，不额外存储）。"""

    bid: str = ""
    kind: str = KIND_PARA
    level: int = 0
    text: str = ""
    spans: list[Span] = field(default_factory=list)
    style: dict = field(default_factory=dict)
    anchors: list[dict] = field(default_factory=list)
    src_lang: str = ""

    @classmethod
    def of(cls, seg) -> BlockView:
        return cls(
            bid=seg.sid,
            kind=getattr(seg, "kind", KIND_PARA) or KIND_PARA,
            level=getattr(seg, "level", 0) or 0,
            text=seg.source or "",
            spans=[Span.from_dict(s) for s in (getattr(seg, "spans", None) or [])],
            style=dict(getattr(seg, "style", None) or {}),
            anchors=list(getattr(seg, "anchors", None) or []),
            src_lang=getattr(seg, "src_lang", "") or "",
        )


def block_views(doc) -> list[BlockView]:
    """文档 → 块视图序列。"""
    return [BlockView.of(s) for s in doc.segments]


def plain_text(seg) -> str:
    """段落纯文本（去掉行内标记）。"""
    text = seg.source or ""
    if "<" not in text:
        return text
    import re
    return re.sub(r"<[^>]+>", "", text)


def spans_from_html(html: str) -> list[dict]:
    """从 HTML 片段提取行内标记（粗体/斜体/链接/注音）。

    用 bs4（已是硬依赖）；解析失败时返回空列表——**绝不因此中断导入**。
    """
    if not html:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []

    def walk(node, bold: bool = False, italic: bool = False, href: str = "",
             ruby_base: str = "") -> None:
        for child in node.children:
            if isinstance(child, str):
                t = str(child)
                if t:
                    out.append({"text": t, "bold": bold, "italic": italic,
                                "href": href, "ruby_base": ruby_base,
                                "ruby_text": ""})
                continue
            name = getattr(child, "name", None)
            if not name:
                continue
            if name == "ruby":
                base = child.find("rb")
                rt = child.find("rt")
                walk(base if base else child, bold, italic, href,
                     ruby_base=(base.get_text() if base else ""))
                if rt is not None:
                    out.append({"text": rt.get_text(), "bold": bold, "italic": italic,
                                "href": href, "ruby_base": "",
                                "ruby_text": rt.get_text()})
                continue
            walk(child,
                 bold or name in ("b", "strong"),
                 italic or name in ("i", "em"),
                 (child.get("href") or href) if name == "a" else href,
                 ruby_base)

    walk(soup)
    return out


def stylesheet_from_segments(segments) -> StyleSheet:
    """便捷函数：源继承样式表。"""
    return StyleSheet.from_source(segments)


def apply_stylesheet(doc, sheet: StyleSheet) -> None:
    """把样式表落到段落的 style 上（渲染器据此排版）。"""
    for seg in doc.segments:
        kind = getattr(seg, "kind", KIND_PARA)
        style: dict[str, Any] = {}
        if kind == KIND_HEADING:
            style.update(sheet.headings.get(seg.level, {}))
        else:
            style.update(sheet.paragraph or {})
        seg.style = style
