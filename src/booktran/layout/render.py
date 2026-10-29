"""渲染器：把文档 + 样式表渲染成书页（HTML）。

预览与导出**共用**同一个渲染器——避免"预览好看、导出走样"。
产出的是一份完整 HTML（内联 CSS），可直接在浏览器里看，也可作为 EPUB 的样式来源。
"""
from __future__ import annotations

import html
from pathlib import Path

from ..ir import StyleSheet

PREVIEW_CSS = """
:root {{
  --paper: #ffffff;
  --ink: #1a1a1a;
  --desk: #eceef0;
  --measure: 40em;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--desk); color: var(--ink);
  font-family: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", Georgia, serif;
}}
.sheet {{
  max-width: var(--measure); margin: 0 auto; padding: 48px 56px 64px;
  background: var(--paper); box-shadow: 0 1px 3px rgba(20,22,26,.10), 0 12px 32px rgba(20,22,26,.12);
  min-height: 100vh;
}}
.sheet h1, .sheet h2, .sheet h3, .sheet h4 {{
  font-family: "Source Han Sans SC", "Noto Sans CJK SC", -apple-system, "Segoe UI", sans-serif;
  line-height: 1.35; letter-spacing: 0; text-wrap: balance;
}}
.sheet p {{ margin: 0 0 {margin_after}; text-align: {text_align}; }}
.sheet p.indent {{ text-indent: 2em; }}
.sheet h1 {{ font-size: {h1_size}; margin: {h1_before} 0 {h1_after}; }}
.sheet h2 {{ font-size: {h2_size}; margin: {h2_before} 0 {h2_after}; }}
.sheet h3 {{ font-size: {h3_size}; margin: {h3_before} 0 {h3_after}; }}
.sheet .quote {{ border-left: 2px solid #d8dce1; padding-left: 1em; color: #4a5260; }}
.sheet .code {{ font-family: ui-monospace, "JetBrains Mono", monospace; font-size: .92em;
  background: #f6f7f9; padding: .6em .8em; border-radius: 4px; }}
.sheet .pending {{ color: #9aa1ab; }}
.sheet .pending::after {{ content: " ·待译"; font-size: .7em; letter-spacing: .04em; }}
.sheet .mark {{ border-bottom: 2px solid {accent}; }}
.sheet .meta {{ color: #6b7280; font-size: .8em; margin-bottom: 2em;
  font-family: -apple-system, "Segoe UI", sans-serif; }}
.sheet .page-no {{ text-align: center; color: #9aa1ab; font-size: .8em; margin-top: 2.5em;
  font-family: -apple-system, "Segoe UI", sans-serif; }}
@media print {{
  body {{ background: #fff; }}
  .sheet {{ box-shadow: none; max-width: none; padding: 0; }}
}}
"""


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _sheet_css(sheet: StyleSheet, *, accent: str = "#2C5AA0") -> str:
    body = sheet.body or {}
    headings = sheet.headings or {}

    def hv(level: int, key: str, default: str) -> str:
        return str((headings.get(level) or {}).get(key, default))

    h1_size = hv(1, "font_size", "1.6em")
    h2_size = hv(2, "font_size", "1.35em")
    h3_size = hv(3, "font_size", "1.15em")
    return PREVIEW_CSS.format(
        margin_after=body.get("margin_after", "0.9em"),
        text_align=body.get("text_align", "justify"),
        h1_size=h1_size, h2_size=h2_size, h3_size=h3_size,
        h1_before=hv(1, "margin_before", "1.8em"), h1_after=hv(1, "margin_after", "0.7em"),
        h2_before=hv(2, "margin_before", "1.5em"), h2_after=hv(2, "margin_after", "0.6em"),
        h3_before=hv(3, "margin_before", "1.2em"), h3_after=hv(3, "margin_after", "0.5em"),
        accent=accent,
    )


def _para_html(seg, lang: str) -> str:
    """段落 → HTML（未译显示原文并标注；译文优先）。"""
    from ..ir import KIND_CAPTION, KIND_CODE, KIND_HEADING, KIND_LIST, KIND_QUOTE
    text = seg.source or ""
    tgt = ""
    if lang:
        t = seg.tracks.get(lang)
        if t is not None and t.status.value == "translated" and t.target:
            tgt = t.target
    kind = getattr(seg, "kind", "para") or "para"
    level = getattr(seg, "level", 0) or 0
    body = tgt or text
    cls = ""
    if kind == KIND_QUOTE:
        cls = ' class="quote"'
    elif kind == KIND_CODE:
        cls = ' class="code"'
    if not tgt:
        cls = (cls + ' class="pending"').replace(' class="', ' pending-', 1) if cls else ' class="pending"'
    if kind == KIND_HEADING:
        lv = min(6, max(1, level or 1))
        return f"<h{lv}>{_esc(body)}</h{lv}>"
    if kind == KIND_LIST:
        return f"<p>· {_esc(body)}</p>"
    if kind == KIND_CAPTION:
        return f"<p><small>{_esc(body)}</small></p>"
    return f"<p{cls}>{_esc(body)}</p>"


def render_html(doc, *, lang: str = "", sheet: StyleSheet | None = None,
                title: str = "", keep_anchors: bool = False) -> str:
    """文档 → 完整 HTML（书页预览）。"""
    sheet = sheet or StyleSheet.from_source(doc.segments)
    parts: list[str] = []
    meta_bits = []
    if lang:
        meta_bits.append(f"语言 {lang}")
    meta_bits.append(f"{len(doc.segments)} 段")
    if title:
        meta_bits.insert(0, title)
    parts.append(f'<div class="meta">{" · ".join(_esc(b) for b in meta_bits)}</div>')
    for seg in doc.segments:
        parts.append(_para_html(seg, lang))
    parts.append('<div class="page-no">—</div>')
    return f"""<!doctype html>
<html lang="{_esc(lang or 'zh-CN')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title or doc.relpath)}</title>
<style>{_sheet_css(sheet)}</style>
</head>
<body>
<article class="sheet">
{chr(10).join(parts)}
</article>
</body>
</html>
"""


def write_preview(project_dir: Path, doc, *, lang: str = "",
                  sheet: StyleSheet | None = None, policy: str = "original") -> Path:
    """写预览产物：artifacts/preview/<stem>.<lang>.html（随翻随更新）。"""
    d = Path(project_dir) / "artifacts" / "preview"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{Path(doc.relpath).stem}.{lang or 'src'}.html"
    path = d / name
    path.write_text(render_html(doc, lang=lang, sheet=sheet, title=Path(doc.relpath).stem),
                    encoding="utf-8")
    return path


def preview_index(project_dir: Path) -> list[Path]:
    """项目里已有的预览产物。"""
    d = Path(project_dir) / "artifacts" / "preview"
    return sorted(d.glob("*.html")) if d.exists() else []
