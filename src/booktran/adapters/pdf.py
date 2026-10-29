"""PDF 适配器（PyMuPDF）：文本层提取 → TXT/MD 输出，或重新打包为 EPUB。

v0.1 定位：
- 有文本层的 PDF：按页提取 → 合并被换行截断的行 → 每段一个 Segment
- 扫描件/图片 PDF：无文本层，给出明确提示（OCR 不在 v0.1 范围）
- 输出：TXT（译文/双语，保留页面分隔）；`--format epub` 生成可阅读 EPUB
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

from ..model import Segment, SourceDoc, Status
from ..util import is_trivial_line
from .base import Reader, Writer
from .lines import LineWriter

# 句末标点（CJK + 西文）：行尾出现则视为段落结束
_SENTENCE_END = tuple("。！？…」』）)】]!?.…")
# 行首开启新段的符号（对话/引文开始）
_PARAGRAPH_START = tuple("「『（(【[\"'")
# 章节标题行：独立成段（整行匹配，允许“第一章 标题”这类带后缀形式）
_HEADING = re.compile(
    r"^(?:"
    r"第\s*[0-9一二三四五六七八九十百千]+\s*[章节回卷部篇话].*|"
    r"(?:CHAPTER|Chapter|Prologue|Epilogue)\s+[\dIVXLC]+.*|"
    r"\d+\s*[.、]\s*\S.*"
    r")$"
)

# 合并上限，防止整页被错误拼成一坨
_MAX_MERGED_CHARS = 600


def _import_pymupdf():
    try:
        import pymupdf  # noqa: PLC0415
        return pymupdf
    except ImportError as e:
        raise SystemExit(
            "PDF 支持需要 pymupdf：pip install 'booktran[pdf]' 或 pip install pymupdf"
        ) from e


class PdfReader(Reader):
    def read(self, path: Path, relpath: str) -> SourceDoc:
        pymupdf = _import_pymupdf()
        doc = SourceDoc(relpath=relpath, fmt="pdf")
        with pymupdf.open(path) as pdf:
            if pdf.page_count == 0:
                raise SystemExit(f"PDF 无页面: {path}")
            empty_pages = 0
            for page_no, page in enumerate(pdf):
                page_h = float(page.rect.height)
                groups = self._page_groups(page)
                if not groups:
                    empty_pages += 1
                    continue
                idx0 = len(doc.segments)
                for group in groups:
                    for para, bbox in self._merge_items(group):
                        meta = {"page": page_no + 1, "line_break": 0}
                        if bbox:
                            meta["bbox"] = [round(v, 2) for v in
                                            self._to_pdf_bbox(bbox, page_h)]
                        anchors = []
                        if bbox:
                            anchors.append({"page": page_no + 1,
                                            "bbox": meta.get("bbox", [])})
                        doc.segments.append(Segment(
                            sid=f"{relpath}#{len(doc.segments)}",
                            source=para,
                            status=Status.EXCLUDED if is_trivial_line(para) else Status.PENDING,
                            meta=meta,
                            anchors=anchors,
                        ))
                # 页面分隔：给页内最后一段记 1 个空行
                if len(doc.segments) > idx0:
                    doc.segments[-1].meta["line_break"] = 1
            if empty_pages:
                print(f"提示: {relpath} 有 {empty_pages}/{pdf.page_count} 页无文本层"
                      "（可能是扫描页，不会被翻译；可加 --vlm 让多模态模型识别）")
            if not doc.segments:
                raise SystemExit(
                    f"PDF 无文本层（扫描件？）: {path}——"
                    "可用 --vlm 让多模态模型识别页面")
        return doc

    @staticmethod
    def _page_groups(page) -> list[list[tuple[str, tuple[float, float, float, float] | None]]]:
        """按 pymupdf 文本块切组；每行带 bbox（供版面保真输出使用）。"""
        groups: list[list[tuple[str, tuple | None]]] = []
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            lines: list[tuple[str, tuple | None]] = []
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", []))
                text = text.strip()
                if not text:
                    continue
                lines.append((text, tuple(line.get("bbox") or ()) or None))
            if lines:
                groups.append(lines)
        return groups

    @classmethod
    def _paragraphs(cls, text: str) -> list[str]:
        """按空行切块，块内按换行合并启发式重构段落（兼容无空行 PDF）。"""
        blocks: list[list[str]] = []
        cur: list[str] = []
        for raw in text.split("\n"):
            ln = raw.strip()
            if not ln:
                if cur:
                    blocks.append(cur)
                    cur = []
            else:
                cur.append(ln)
        if cur:
            blocks.append(cur)
        out: list[str] = []
        for block in blocks:
            out.extend(cls._merge_wrapped(block))
        return out

    @staticmethod
    def _join(prev: str, nxt: str) -> str:
        """合并两行：CJK 直接相接，拉丁文本补空格。"""
        cjk = re.compile(r"[　-鿿가-힯぀-ヿ]")
        if cjk.search(prev[-1:]) or cjk.search(nxt[:1]):
            return prev + nxt
        return prev + " " + nxt

    @classmethod
    def _merge_wrapped(cls, lines: list[str]) -> list[str]:
        """PDF 常见排版：一段被折成多行。行尾非句末标点且下行非新段起点 → 合并。"""
        return [text for text, _ in cls._merge_items([(ln, None) for ln in lines])]

    @classmethod
    def _merge_items(
        cls,
        items: list[tuple[str, tuple | None]],
    ) -> list[tuple[str, tuple | None]]:
        """合并折行 → [(段落文本, bbox 并集)]。bbox 为 None 时不产出 bbox。"""
        paras: list[tuple[str, tuple | None]] = []
        buf = ""
        boxes: list[tuple] = []

        def flush() -> None:
            nonlocal buf, boxes
            if buf:
                paras.append((buf, cls._union(boxes)))
            buf, boxes = "", []

        for text, bbox in items:
            if not buf:
                buf, boxes = text, ([bbox] if bbox else [])
                continue
            if (buf.endswith(_SENTENCE_END)
                    or text.startswith(_PARAGRAPH_START)
                    or _HEADING.match(buf) or _HEADING.match(text)
                    or len(buf) >= _MAX_MERGED_CHARS):
                flush()
                buf, boxes = text, ([bbox] if bbox else [])
            else:
                buf = cls._join(buf, text)
                if bbox:
                    boxes.append(bbox)
        flush()
        return paras

    @staticmethod
    def _union(boxes: list[tuple]) -> tuple | None:
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    @staticmethod
    def _to_pdf_bbox(bbox: tuple, page_h: float) -> tuple:
        """PyMuPDF 的 bbox 是左上原点，PDF 坐标是左下原点 —— 转换 y。"""
        x0, y0, x1, y1 = bbox
        return (x0, page_h - y1, x1, page_h - y0)


class PdfWriter(Writer):
    """PDF 文本输出：TXT（默认）或 EPUB（--format epub）。"""

    def write(
        self,
        doc: SourceDoc,
        source_path: Path,
        out_path: Path,
        *,
        bilingual: bool = False,
        source_first: bool = False,
        lang: str = "",
        target_lang_code: str = "zh",  # noqa: ARG002
    ) -> None:
        LineWriter().write(doc, source_path, out_path, bilingual=bilingual,
                           source_first=source_first, lang=lang)

    def write_epub(self, doc: SourceDoc, source_path: Path, out_path: Path,
                   *, lang: str = "") -> None:
        """把译文段落打包成最小可读 EPUB（EPUB2 结构 + NCX）。"""
        title = self._source_title(source_path)
        xhtml_body = []
        for seg in doc.segments:
            if seg.status == Status.EXCLUDED:
                continue
            text = html.escape(seg.text_for(lang) if lang else seg.final_text)
            xhtml_body.append(f"      <p>{text}</p>")
        ch1 = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            "<head><title></title></head>\n<body>\n"
            + "\n".join(xhtml_body)
            + "\n</body>\n</html>\n"
        )
        opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>zh</dc:language>
    <dc:identifier id="uid">booktran-pdf-{html.escape(doc.relpath)}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/></spine>
</package>"""
        ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="booktran-pdf-{html.escape(doc.relpath)}"/></head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <navMap><navPoint id="n1" playOrder="1">
    <navLabel><text>{html.escape(title)}</text></navLabel><content src="ch1.xhtml"/>
  </navPoint></navMap>
</ncx>"""
        container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", container)
            zf.writestr("content.opf", opf)
            zf.writestr("toc.ncx", ncx)
            zf.writestr("ch1.xhtml", ch1)

    @staticmethod
    def _source_title(source_path: Path) -> str:
        try:
            pymupdf = _import_pymupdf()
            with pymupdf.open(source_path) as pdf:
                return (pdf.metadata or {}).get("title") or source_path.stem
        except SystemExit:
            return source_path.stem
