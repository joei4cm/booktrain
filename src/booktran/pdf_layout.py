"""版面保真的 PDF 输出（PyMuPDF 重排）。

两种版式：
- 双语（bilingual=True）：左栏 = 原页渲染图（版面原样），右栏 = 译文；
  译文按源段落的纵向位置对齐，读者可以左右对照。
- 纯译文（bilingual=False）：译文直接落在源段落的位置（缩放字号以适应）。

CJK 用 PyMuPDF 内置字体（china-s / japan / korea），不依赖外部字体文件，
输出 PDF 保留可复制的文本层。
"""
from __future__ import annotations

from pathlib import Path

from .model import SourceDoc, Status

# PyMuPDF 内置 CJK 字体（无需外部字体文件）
CJK_FONTS = {
    "zh": "china-s",
    "zh-tw": "china-t",
    "ja": "japan",
    "ko": "korea",
}

_MARGIN = 24.0          # 页边距（点）
_COL_GAP = 18.0         # 双语两栏之间的间隙
_LEADING = 1.28         # 行距系数


def _import_pymupdf():
    try:
        import pymupdf  # noqa: PLC0415
        return pymupdf
    except ImportError as e:
        raise SystemExit("PDF 版面输出需要 pymupdf：pip install 'booktran[pdf]'") from e


def _font_for(target_lang_code: str) -> str:
    key = (target_lang_code or "zh").lower()
    if key in CJK_FONTS:
        return CJK_FONTS[key]
    return "helv"          # 拉丁：Helvetica（base-14）


def _wrap(text: str, font, size: float, width: float) -> list[str]:
    """按可用宽度折行；优先在空格处断（拉丁），否则逐字断（CJK）。"""
    lines: list[str] = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if not cur or font.text_length(trial, fontsize=size) <= width:
                cur = trial
                continue
            sp = cur.rfind(" ")
            if sp > 0:
                lines.append(cur[:sp])
                cur = cur[sp + 1:] + ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [""]


def _fit(text: str, font, width: float, height: float,
         max_size: float, min_size: float) -> tuple[float, list[str]]:
    """在给定矩形内选最大字号（逐档缩小），返回 (字号, 折行结果)。"""
    size = max_size
    while size >= min_size:
        lines = _wrap(text, font, size, width)
        if len(lines) * size * _LEADING <= height:
            return size, lines
        size -= 0.5
    return min_size, _wrap(text, font, min_size, width)


class PdfLayoutWriter:
    """把译文按原版面写成 PDF（双语对照 / 纯译文）。"""

    def write(
        self,
        doc: SourceDoc,
        source_path: Path,
        out_path: Path,
        *,
        bilingual: bool = True,
        lang: str = "",
        target_lang_code: str = "zh",
        dpi: int = 120,
        jpeg_quality: int = 75,
        max_font: float = 12.0,
        min_font: float = 5.0,
    ) -> None:
        pymupdf = _import_pymupdf()
        font_name = _font_for(target_lang_code)
        font = pymupdf.Font(font_name)
        self._lang = lang

        pages: dict[int, list] = {}
        for seg in doc.segments:
            if seg.status == Status.EXCLUDED:
                continue
            pages.setdefault(int(seg.meta.get("page") or 1), []).append(seg)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out = pymupdf.open()
        with pymupdf.open(source_path) as src:
            for page_index in range(src.page_count):
                page = src[page_index]
                w, h = float(page.rect.width), float(page.rect.height)
                segs = pages.get(page_index + 1, [])
                if bilingual:
                    out_page = out.new_page(width=w * 2 + _COL_GAP, height=h)
                    pix = page.get_pixmap(dpi=dpi)
                    try:
                        img = pix.tobytes("jpeg", jpg_quality=jpeg_quality)
                    except Exception:  # noqa: BLE001 — 少数版本不支持 jpg_quality
                        img = pix.tobytes("jpeg")
                    out_page.insert_image(pymupdf.Rect(0, 0, w, h), stream=img)
                    out_page.draw_line(pymupdf.Point(w + _COL_GAP / 2, 0),
                                       pymupdf.Point(w + _COL_GAP / 2, h),
                                       color=(0.75, 0.75, 0.75), width=0.6)
                    self._draw_column(out_page, segs, font, font_name,
                                      x0=w + _COL_GAP, width=w - _MARGIN,
                                      page_h=h, max_font=max_font, min_font=min_font)
                else:
                    out_page = out.new_page(width=w, height=h)
                    self._draw_column(out_page, segs, font, font_name,
                                      x0=_MARGIN, width=w - _MARGIN * 2, page_h=h,
                                      max_font=max_font, min_font=min_font,
                                      align_to_bbox=True)
        out.save(str(out_path))
        out.close()

    # ---------- 内部 ----------
    def _draw_column(self, page, segs, font, font_name, x0, width, page_h,
                     max_font, min_font, align_to_bbox: bool = False) -> None:
        """按源段落的纵向位置放译文（PyMuPDF 坐标：左上原点）。

        每个译文的“可用带”= 从本段顶部到下一段顶部（末段到页底），
        字号在带内自适应缩小 —— 既保持与原文的纵向对应，又避免互相重叠。
        align_to_bbox=True（纯译文模式）时沿用源段落的左缩进。
        """
        pymupdf = _import_pymupdf()
        if not segs:
            return
        tops: list[float] = []
        for seg in segs:
            bbox = seg.meta.get("bbox")
            if bbox and len(bbox) == 4:
                # meta 里是 PDF 坐标（左下原点）→ 转成 PyMuPDF 的上原点
                tops.append(min(max(page_h - float(bbox[3]), _MARGIN), page_h - _MARGIN))
            else:
                tops.append(_MARGIN)
        for i, seg in enumerate(segs):
            top = tops[i]
            band_bottom = tops[i + 1] if i + 1 < len(tops) else page_h - _MARGIN
            band = max(band_bottom - top, 8.0)
            sx, swidth = x0, width
            bbox = seg.meta.get("bbox")
            if align_to_bbox and bbox and len(bbox) == 4:
                left = float(bbox[0])
                if x0 < left < x0 + width * 0.5:
                    sx = left
                    swidth = width - (left - x0)
            lang = getattr(self, "_lang", "")
            done = seg.is_done(lang) if lang else any(
                t.status == Status.TRANSLATED for t in seg.tracks.values())
            size, lines = _fit(seg.text_for(lang) if lang else seg.final_text,
                               font, swidth, band, max_font, min_font)
            lead = size * _LEADING
            y = top + size * 0.92
            color = (0, 0, 0) if done else (0.45, 0.45, 0.45)
            for line in lines:
                if line:
                    page.insert_text(pymupdf.Point(sx, y), line,
                                     fontname=font_name, fontsize=size, color=color)
                y += lead
