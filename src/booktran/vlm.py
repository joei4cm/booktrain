"""VLM 扫描页识别：把 PDF 页渲染成图片，交给多模态模型抽取「文本块 + 版面 bbox」。

用途：
- 扫描件（无文本层）PDF 的文字提取（替代 OCR）
- 与文本层页面混合时，只对缺文本层的页调用 VLM（hybrid 模式）

坐标约定：模型返回归一化 bbox [x0,y0,x1,y1]（左上原点，0~1），
本模块再按页面尺寸换算成 PDF 点坐标（左下原点），与 pymupdf 的 bbox 一致。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只为类型注解，运行时不导入（避免循环依赖）
    from .model import SourceDoc

VLM_SYSTEM = """你是书稿页面的版面识别助手。阅读给定的这一页图片，输出页面上的文本块。

要求：
1. 按阅读顺序输出全部文本块（标题、段落、对话都算）
2. 每个文本块给出 bbox：相对整页的归一化坐标 [x0, y0, x1, y1]，左上角为原点，取值 0~1
3. text 为该块的原文，逐字转写；换行用 \\n
4. 只输出 JSON，不要翻译、不要解释：
{"blocks":[{"bbox":[0.1,0.2,0.9,0.24],"text":"..."}]}
5. 页面没有可识别文字时输出 {"blocks":[]}"""


def _import_pymupdf():
    try:
        import pymupdf  # noqa: PLC0415
        return pymupdf
    except ImportError as e:
        raise SystemExit("VLM-PDF 需要 pymupdf：pip install 'booktran[pdf]'") from e


def render_page_png(pdf_path: Path, page_index: int, dpi: int = 150) -> bytes:
    """渲染某一页为 PNG 字节（page_index 从 0 开始）。"""
    pymupdf = _import_pymupdf()
    with pymupdf.open(pdf_path) as pdf:
        if page_index >= pdf.page_count:
            raise IndexError(f"页码越界: {page_index} >= {pdf.page_count}")
        page = pdf[page_index]
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def page_rect(pdf_path: Path, page_index: int) -> tuple[float, float]:
    pymupdf = _import_pymupdf()
    with pymupdf.open(pdf_path) as pdf:
        r = pdf[page_index].rect
        return float(r.width), float(r.height)


def parse_blocks(text: str) -> list[dict]:
    """从模型回复里抽出 blocks（容错：代码块/顶层数组/缺 bbox）。"""
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if m:
        raw = m.group(1).strip()
    obj = None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", raw, re.DOTALL)
        if m2:
            try:
                obj = json.loads(m2.group(0))
            except json.JSONDecodeError:
                obj = None
    if obj is None:
        m3 = re.search(r"\[.*\]", raw, re.DOTALL)
        if m3:
            try:
                obj = json.loads(m3.group(0))
            except json.JSONDecodeError:
                obj = []
    if isinstance(obj, dict):
        items = obj.get("blocks") or obj.get("texts") or obj.get("lines") or []
    elif isinstance(obj, list):
        items = obj
    else:
        items = []
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text_value = it.get("text") or it.get("content") or it.get("t") or ""
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        bbox = it.get("bbox") or it.get("box") or it.get("rect")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            bbox = None
        else:
            try:
                bbox = [float(x) for x in bbox]
            except (TypeError, ValueError):
                bbox = None
        out.append({"text": text_value.strip(), "bbox": bbox})
    return out


async def extract_scanned_pages(
    pdf_path: Path,
    provider=None,
    *,
    dpi: int = 150,
    pages: list[int] | None = None,
    on_page=None,
    channel=None,
) -> list[dict]:
    """对指定页（或所有无文本层的页）调用 VLM 抽取文本块。

    返回 [{"page": 1-based, "bbox": [x0,y0,x1,y1] 归一化, "text": "..."}]。
    on_page: 可选回调 async fn(page_no, blocks)。
    """
    pymupdf = _import_pymupdf()
    targets: list[int] = []
    with pymupdf.open(pdf_path) as pdf:
        for i, page in enumerate(pdf):
            if pages is not None:
                if i not in pages:
                    continue
            elif page.get_text("text").strip():
                continue  # 有文本层 → 走普通抽取，不浪费 VLM 调用
            targets.append(i)

    results: list[dict] = []
    for i in targets:
        png = render_page_png(pdf_path, i, dpi=dpi)
        if channel is not None:
            result = await channel.recognize_page(png, dpi=dpi)
            blocks = [{"bbox": b.bbox, "text": b.text} for b in result.blocks]
        else:
            blocks = await _recognize_page(provider, png)
        for b in blocks:
            results.append({"page": i + 1, "bbox": b["bbox"], "text": b["text"]})
        if on_page is not None:
            out = on_page(i + 1, blocks)
            if hasattr(out, "__await__"):
                await out
    return results


def blocks_to_segments(blocks: list[dict], relpath: str, page_sizes: dict[int, tuple[float, float]]):
    """把 VLM 块转成 Segment（bbox 换算为 PDF 点坐标，写进 meta）。"""
    from .model import Segment, Status
    from .util import is_trivial_line

    segs = []
    for b in blocks:
        text = b["text"]
        bbox = b.get("bbox")
        meta = {"page": b["page"], "line_break": 0, "vlm": True}
        if bbox:
            w, h = page_sizes.get(b["page"], (595.0, 842.0))
            x0, y0, x1, y1 = bbox
            meta["bbox"] = [
                round(x0 * w, 2),
                round((1.0 - y1) * h, 2),   # PDF 坐标自下而上
                round(x1 * w, 2),
                round((1.0 - y0) * h, 2),
            ]
        segs.append(Segment(
            sid=f"{relpath}#{len(segs)}",
            source=text,
            status=Status.EXCLUDED if is_trivial_line(text) else Status.PENDING,
            meta=meta,
        ))
    return segs


async def _recognize_page(provider, png: bytes) -> list[dict]:
    """让多模态模型识别一页 → 文本块列表。"""
    res = await provider.complete_vision(VLM_SYSTEM, "识别这一页的文本块。", [png])
    return parse_blocks(res.text)


async def read_pdf_vlm(path: Path, relpath: str, provider=None, *, dpi: int = 150,
                       channel=None) -> SourceDoc:
    """混合模式读取 PDF：有文本层的页用常规抽取，扫描页交给识别通道。

    channel 不为空时，扫描页走 OCR 通道（本地 OCR → 置信度不够再升级 VLM）；
    否则用 provider 直接走 VLM。
    产出的 Segment 带 page/bbox meta，可直接用于版面保真的 PDF 输出。
    """
    from .adapters.pdf import PdfReader
    from .model import Segment, SourceDoc, Status
    from .util import is_trivial_line

    pymupdf = _import_pymupdf()
    doc = SourceDoc(relpath=relpath, fmt="pdf")
    sizes: dict[int, tuple[float, float]] = {}
    scanned = 0
    with pymupdf.open(path) as pdf:
        for page_index, page in enumerate(pdf):
            page_no = page_index + 1
            rect = page.rect
            sizes[page_no] = (float(rect.width), float(rect.height))
            groups = PdfReader._page_groups(page)
            if groups:
                for group in groups:
                    for para, bbox in PdfReader._merge_items(group):
                        meta = {"page": page_no, "line_break": 0}
                        if bbox:
                            meta["bbox"] = [round(v, 2) for v in
                                            PdfReader._to_pdf_bbox(bbox, float(rect.height))]
                        doc.segments.append(Segment(
                            sid=f"{relpath}#{len(doc.segments)}",
                            source=para,
                            status=Status.EXCLUDED if is_trivial_line(para) else Status.PENDING,
                            meta=meta,
                        ))
                continue
            # 扫描页 → VLM
            scanned += 1
            png = render_page_png(path, page_index, dpi=dpi)
            if channel is not None:
                result = await channel.recognize_page(png, dpi=dpi)
                blocks = [{"bbox": b.bbox, "text": b.text} for b in result.blocks]
            else:
                blocks = await _recognize_page(provider, png)
            w, h = sizes[page_no]
            for b in blocks:
                bbox = b.get("bbox")
                meta = {"page": page_no, "line_break": 0, "vlm": True}
                if bbox:
                    x0, y0, x1, y1 = bbox
                    meta["bbox"] = [
                        round(x0 * w, 2),
                        round((1.0 - y1) * h, 2),
                        round(x1 * w, 2),
                        round((1.0 - y0) * h, 2),
                    ]
                doc.segments.append(Segment(
                    sid=f"{relpath}#{len(doc.segments)}",
                    source=b["text"],
                    status=Status.EXCLUDED if is_trivial_line(b["text"]) else Status.PENDING,
                    meta=meta,
                ))
    if scanned:
        print(f"提示: {relpath} 有 {scanned} 页无文本层，已由多模态模型识别")
    if not doc.segments:
        raise SystemExit(f"PDF 既无文本层也无识别结果: {path}")
    return doc
