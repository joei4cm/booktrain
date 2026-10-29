"""版面保真 PDF 输出 + VLM 扫描页识别。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

from conftest import set_translated  # noqa: E402

from booktran.adapters.pdf import PdfReader  # noqa: E402
from booktran.model import Status
from booktran.pdf_layout import PdfLayoutWriter  # noqa: E402
from booktran.vlm import parse_blocks, read_pdf_vlm  # noqa: E402


@pytest.fixture
def scanned_pdf(tmp_path) -> Path:
    """无文本层的 PDF（只有矢量图形），模拟扫描页。"""
    p = tmp_path / "scanned.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 200), color=(0, 0, 0), width=2)
        doc.save(p)
    return p


def _mark_translated(doc, prefix="译文"):
    for s in doc.segments:
        if s.status != Status.EXCLUDED:
            set_translated(s, prefix + s.source)


def test_reader_records_bbox(pdf_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    assert all("bbox" in s.meta for s in doc.segments)
    bboxes = [s.meta["bbox"] for s in doc.segments]
    # y 是自下而上的 PDF 坐标：第一段应在上方（y 更大）
    assert bboxes[0][3] > bboxes[2][1]
    assert all(b[0] < b[2] and b[1] < b[3] for b in bboxes)


def test_layout_pdf_target_only(pdf_path, tmp_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    _mark_translated(doc)
    out = tmp_path / "out.pdf"
    PdfLayoutWriter().write(doc, pdf_path, out, bilingual=False, target_lang_code="zh", lang="chinese_simplified")
    assert out.exists()
    with pymupdf.open(out) as pdf:
        assert pdf.page_count == 2
        # 纯译文页尺寸 = 原页尺寸
        assert abs(pdf[0].rect.width - 595.28) < 2
        assert abs(pdf[0].rect.height - 841.89) < 2


def test_layout_pdf_bilingual(pdf_path, tmp_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    _mark_translated(doc)
    out = tmp_path / "bi.pdf"
    PdfLayoutWriter().write(doc, pdf_path, out, bilingual=True, target_lang_code="zh", lang="chinese_simplified")
    with pymupdf.open(out) as pdf:
        assert pdf.page_count == 2
        # 双语页宽 = 2×原宽 + 间隙
        assert pdf[0].rect.width > 595 * 2
        assert pdf[0].rect.height == pytest.approx(841.89, abs=2)
        # 左栏是原页渲染图（有位图）
        assert pdf[0].get_images() or pdf[0].get_drawings()


def test_layout_pdf_text_present(pdf_path, tmp_path):
    """译文内容确实写进了 PDF（用文本提取校验）。"""
    doc = PdfReader().read(pdf_path, "book.pdf")
    for s in doc.segments:
        if s.status != Status.EXCLUDED:
            set_translated(s, "银色天空下的少年")
    out = tmp_path / "out.pdf"
    PdfLayoutWriter().write(doc, pdf_path, out, bilingual=False, target_lang_code="zh", lang="chinese_simplified")
    with pymupdf.open(out) as pdf:
        text = "".join(pdf[i].get_text() for i in range(pdf.page_count))
    assert "银色天空下的少年" in text


def test_parse_blocks_variants():
    assert parse_blocks('{"blocks":[{"bbox":[0,0,1,0.1],"text":"hello"}]}') == [
        {"text": "hello", "bbox": [0, 0, 1, 0.1]}]
    assert parse_blocks('```json\n{"blocks":[{"text":"x"}]}\n```') == [
        {"text": "x", "bbox": None}]
    assert parse_blocks('[{"text":"a","bbox":[0,0,1,1]}]') == [
        {"text": "a", "bbox": [0, 0, 1, 1]}]
    assert parse_blocks("garbage") == []


def test_read_pdf_vlm_scanned_page(scanned_pdf):
    """扫描页：文本层为空 → 走 VLM，产出带 bbox 的段。"""
    from booktran.llm.base import FakeProvider

    provider = FakeProvider()
    doc = asyncio.run(read_pdf_vlm(scanned_pdf, "scanned.pdf", provider))
    assert len(doc.segments) == 1
    seg = doc.segments[0]
    assert seg.source.startswith("【扫描页】")
    assert seg.meta["page"] == 1
    assert seg.meta["vlm"] is True
    assert len(seg.meta["bbox"]) == 4
    assert provider.vision_calls == 1


def test_read_pdf_vlm_skips_text_pages(pdf_path):
    """有文本层的页不调用 VLM。"""
    from booktran.llm.base import FakeProvider

    provider = FakeProvider()
    doc = asyncio.run(read_pdf_vlm(pdf_path, "book.pdf", provider))
    assert provider.vision_calls == 0
    assert len(doc.segments) == 6


def test_vlm_json_protocol_roundtrip():
    """VLM 回复的 JSON 结构约定（供文档/提示词对齐）。"""
    payload = {"blocks": [{"bbox": [0.1, 0.2, 0.9, 0.24], "text": "第一段"}]}
    assert json.loads(json.dumps(payload))["blocks"][0]["text"] == "第一段"
