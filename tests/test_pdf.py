"""PDF 测试：fixture（pdf_path/make_pdf）定义在 conftest.py。"""
from __future__ import annotations

from conftest import set_translated  # noqa: E402

from booktran.adapters.pdf import PdfReader, PdfWriter  # noqa: E402
from booktran.model import Status


def test_pdf_read(pdf_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    sources = [s.source for s in doc.segments]
    assert doc.fmt == "pdf"
    # 页1：三行各自成段（行尾均为句末标点）；页2：两段 + CHAPTER 2
    assert sources == [
        "The boy woke to the sound of wind.",
        "A silver sky lay beyond the window.",
        '"Your journey begins today."',
        "The old man spoke very softly.",
        "CHAPTER 2",
        "The boy named Allen prepared.",
    ]
    assert [s.meta["page"] for s in doc.segments] == [1, 1, 1, 2, 2, 2]
    assert doc.segments[2].meta["line_break"] == 1  # 页1 末尾段记空行
    assert doc.segments[-1].meta["line_break"] == 1


def test_pdf_paragraph_merging():
    from booktran.adapters.pdf import PdfReader as R
    # 无空行 PDF：折行合并（拉丁文本补空格）
    paras = R._paragraphs("line one continues\nline two continues\nend here.\nnew para")
    assert paras == ["line one continues line two continues end here.", "new para"]


def test_pdf_cjk_join_no_space():
    from booktran.adapters.pdf import PdfReader as R
    paras = R._paragraphs("少年は風の音で\n目を覚ました。\n窓の外には銀色の空が\n広がっていた。")
    assert paras == ["少年は風の音で目を覚ました。", "窓の外には銀色の空が広がっていた。"]


def test_pdf_dialogue_not_merged():
    from booktran.adapters.pdf import PdfReader as R
    # 行尾已是句末标点 → 不合并
    assert R._paragraphs("He left.\nShe stayed.") == ["He left.", "She stayed."]
    # 下行以对话开始符号开头 → 不合并
    paras = R._paragraphs("He said\n\"Hello there\"")
    assert paras == ["He said", "\"Hello there\""]


def test_pdf_blank_line_blocks():
    from booktran.adapters.pdf import PdfReader as R
    paras = R._paragraphs("First para line one\nline two.\n\nSecond para.")
    assert paras == ["First para line one line two.", "Second para."]


def test_pdf_write_txt(pdf_path, tmp_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    for s in doc.segments:
        if s.status != Status.EXCLUDED:
            set_translated(s, "【译】" + s.source)
    out = tmp_path / "out.txt"
    PdfWriter().write(doc, pdf_path, out)
    text = out.read_text("utf-8")
    assert "【译】" in text
    # 页面分隔空行存在
    assert "\n\n" in text


def test_pdf_write_epub(pdf_path, tmp_path):
    doc = PdfReader().read(pdf_path, "book.pdf")
    for s in doc.segments:
        if s.status != Status.EXCLUDED:
            set_translated(s, "译文内容")
    out = tmp_path / "out.epub"
    PdfWriter().write_epub(doc, pdf_path, out, lang="chinese_simplified")
    import zipfile
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        opf = zf.read("content.opf").decode("utf-8")
        ch1 = zf.read("ch1.xhtml").decode("utf-8")
    assert "mimetype" in names and "content.opf" in names
    assert "<dc:title>Silver Sky</dc:title>" in opf
    assert "译文内容" in ch1
    assert ch1.count("<p>") == len([s for s in doc.segments if s.is_done("chinese_simplified")])
