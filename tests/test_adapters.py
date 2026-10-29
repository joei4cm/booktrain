
from conftest import set_translated

from booktran.adapters.epub import EpubReader, EpubWriter
from booktran.adapters.txt import TxtReader, TxtWriter
from booktran.model import Status


def test_txt_roundtrip(txt_path, tmp_path):
    doc = TxtReader().read(txt_path, "novel.txt")
    sources = [s.source for s in doc.segments]
    # 纯符号/数字段保留在列表中但标记 EXCLUDED
    assert sources == ["第一段。", "第二段。", "...", "123", "第三段。"]
    assert [s.status for s in doc.segments] == [
        Status.PENDING, Status.PENDING, Status.EXCLUDED, Status.EXCLUDED, Status.PENDING,
    ]
    # line_break 记录空行（末段后无空行 → 0）
    assert [s.meta["line_break"] for s in doc.segments] == [1, 1, 1, 1, 0]

    for s in doc.segments:
        if s.status != Status.EXCLUDED:
            set_translated(s, "【译】" + s.source)

    out = tmp_path / "out.txt"
    TxtWriter().write(doc, txt_path, out)
    assert out.read_text("utf-8") == "【译】第一段。\n\n【译】第二段。\n\n...\n\n123\n\n【译】第三段。\n"

def test_txt_bilingual(txt_path, tmp_path):
    doc = TxtReader().read(txt_path, "novel.txt")
    for s in doc.segments:
        set_translated(s, "译" + s.source)
    out = tmp_path / "bi.txt"
    TxtWriter().write(doc, txt_path, out, bilingual=True)
    text = out.read_text("utf-8")
    assert "译第一段。\n第一段。" in text


def test_epub_read(epub_path):
    doc = EpubReader().read(epub_path, "book.epub")
    sources = [s.source for s in doc.segments]
    # 正文（h1 → p → p(含i) → div内p → div直下文本），NCX 目录文本 + head/title 附后
    assert sources == [
        "第一章　始まり",
        "これは最初の段落です。物語が始まります。",
        "二番目の段落には <i>強調</i> が含まれています。",  # 行内标签保留
        "div の中の段落は div ではなく p として抽出される。",
        "div 直下のテキストは div として抽出される。",
        "测试书",  # ch1.xhtml 的 <head><title>
        "第一章　始まり",  # toc.ncx
    ]
    # 纯符号/数字过滤
    statuses = [s.status for s in doc.segments]
    assert Status.EXCLUDED not in statuses
    # title 段带 tag 标记
    assert doc.segments[5].meta["tag"] == "title"


def test_epub_write(epub_path, tmp_path):
    doc = EpubReader().read(epub_path, "book.epub")
    for s in doc.segments:
        set_translated(s, "【译】" + s.source)
    out = tmp_path / "out.epub"
    EpubWriter().write(doc, epub_path, out, target_lang_code="zh", lang="chinese_simplified")

    # 重新读取验证
    doc2 = EpubReader().read(out, "out.epub")
    sources = [s.source for s in doc2.segments]
    assert all(s.startswith("【译】") for s in sources), sources
    assert len(sources) == len(doc.segments)

    # lang 元数据被替换
    import zipfile
    with zipfile.ZipFile(out) as zf:
        ch1 = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        mimetype = zf.read("mimetype").decode()
    assert 'lang="zh"' in ch1
    assert "<dc:language>zh</dc:language>" in opf
    assert mimetype == "application/epub+zip"
    # 标签结构保留（注：段内内联标签如 <i> 在译文标签重建时不保留，与 AiNiee 行为一致）
    assert "<h1" in ch1 and "</h1>" in ch1


def test_epub_inline_tags_preserved(epub_path, tmp_path):
    """行内标签（<i>）在回写时保留：模型保留标签则沿用，否则按原文结构套回。"""
    from bs4 import BeautifulSoup
    doc = EpubReader().read(epub_path, "book.epub")
    seg = next(s for s in doc.segments if "<i>" in s.source)
    set_translated(seg, "第二段包含强调这个词。")
    out = tmp_path / "out.epub"
    EpubWriter().write(doc, epub_path, out, target_lang_code="zh", lang="chinese_simplified")
    import zipfile
    with zipfile.ZipFile(out) as zf:
        ch1 = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert "<i>" in ch1 and "</i>" in ch1
    # 去掉标签后文本完整（模型未保留标签时按比例切分，文字不丢）
    plain = BeautifulSoup(ch1, "html.parser").get_text()
    assert "第二段包含强调这个词。" in plain


def test_epub_inline_tags_model_structure_kept(epub_path, tmp_path):
    """模型自己保住了 <i> 时，沿用模型的结构与原文的属性。"""
    doc = EpubReader().read(epub_path, "book.epub")
    seg = next(s for s in doc.segments if "<i>" in s.source)
    set_translated(seg, "第二段包含<i>强调</i>这个词。")
    out = tmp_path / "out.epub"
    EpubWriter().write(doc, epub_path, out, target_lang_code="zh", lang="chinese_simplified")
    import zipfile
    with zipfile.ZipFile(out) as zf:
        ch1 = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
    assert "<i>强调</i>" in ch1


def test_epub_title_translated(epub_path, tmp_path):
    """<head><title> 与 OPF dc:title 随译文更新。"""
    doc = EpubReader().read(epub_path, "book.epub")
    for s in doc.segments:
        if s.meta.get("tag") == "title":
            set_translated(s, "测试书（译）")
    out = tmp_path / "out.epub"
    EpubWriter().write(doc, epub_path, out, target_lang_code="zh", lang="chinese_simplified")
    import zipfile
    with zipfile.ZipFile(out) as zf:
        ch1 = zf.read("OEBPS/ch1.xhtml").decode("utf-8")
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert "<title>测试书（译）</title>" in ch1
    assert "<dc:title>测试书（译）</dc:title>" in opf


def test_epub3_nav_translated(epub3_path, tmp_path):
    """EPUB3 nav 文档被读取并翻译；<a href> 结构保留。"""
    doc = EpubReader().read(epub3_path, "book3.epub")
    nav_segs = [s for s in doc.segments if s.meta.get("file", "").endswith("nav.xhtml")]
    texts = [s.source for s in nav_segs]
    assert any("第一章　始まり" in t for t in texts)
    assert any("第二章　続き" in t for t in texts)
    for s in nav_segs:
        set_translated(s, "译" + s.source)
    out = tmp_path / "out3.epub"
    EpubWriter().write(doc, epub3_path, out, target_lang_code="zh", lang="chinese_simplified")
    import zipfile
    with zipfile.ZipFile(out) as zf:
        nav = zf.read("OEBPS/nav.xhtml").decode("utf-8")
    assert "译第一章　始まり" in nav
    assert 'href="ch1.xhtml"' in nav      # 链接结构保留
    assert 'href="ch2.xhtml"' in nav
