"""共享 fixtures：内存段、假 provider、生成的 EPUB/TXT/PDF 文件。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from booktran.model import Segment, SourceDoc, Status


def set_translated(seg: Segment, text: str, lang: str = "chinese_simplified") -> None:
    """把某段在指定语言上标记为已译（测试构造用）。"""
    t = seg.track(lang)
    t.target = text
    t.status = Status.TRANSLATED


def make_segments(n: int, prefix: str = "第", fmt: str = "txt") -> list[Segment]:
    return [
        Segment(sid=f"doc.txt#{i}", source=f"{prefix}{i}段测试文本，长度适中。",
                meta={"line_break": 1} if fmt == "txt" else {})
        for i in range(n)
    ]


def make_doc(n: int, fmt: str = "txt", relpath: str = "doc.txt") -> SourceDoc:
    return SourceDoc(relpath=relpath, fmt=fmt, segments=make_segments(n, fmt=fmt))


SIMPLE_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja" lang="ja">
<head><title>测试书</title></head>
<body>
  <h1>第一章　始まり</h1>
  <p>これは最初の段落です。物語が始まります。</p>
  <p>二番目の段落には <i>強調</i> が含まれています。</p>
  <div><p>div の中の段落は div ではなく p として抽出される。</p></div>
  <div>div 直下のテキストは div として抽出される。</div>
  <p>...</p>
  <p>123</p>
</body>
</html>
"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章　始まり</text></navLabel><content src="ch1.xhtml"/></navPoint>
  </navMap>
</ncx>
"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试书</dc:title>
    <dc:language>ja</dc:language>
    <dc:identifier id="uid">test-001</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
  </spine>
</package>
"""

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


@pytest.fixture
def epub_path(tmp_path: Path) -> Path:
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF)
        zf.writestr("OEBPS/toc.ncx", NCX)
        zf.writestr("OEBPS/ch1.xhtml", SIMPLE_XHTML)
    return p


EPUB3_NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="ja">
<head><title>目次</title></head>
<body>
  <nav epub:type="toc">
    <ol>
      <li><a href="ch1.xhtml">第一章　始まり</a></li>
      <li><a href="ch2.xhtml">第二章　続き</a></li>
    </ol>
  </nav>
</body>
</html>
"""

OPF3 = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>テスト書</dc:title>
    <dc:language>ja</dc:language>
    <dc:identifier id="uid">test-003</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
  </spine>
</package>
"""


@pytest.fixture
def epub3_path(tmp_path: Path) -> Path:
    """EPUB3：带 nav 文档（properties="nav"）。"""
    p = tmp_path / "book3.epub"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", OPF3)
        zf.writestr("OEBPS/toc.ncx", NCX)
        zf.writestr("OEBPS/nav.xhtml", EPUB3_NAV)
        zf.writestr("OEBPS/ch1.xhtml", SIMPLE_XHTML)
    return p


@pytest.fixture
def txt_path(tmp_path: Path) -> Path:
    p = tmp_path / "novel.txt"
    p.write_text(
        "第一段。\n\n第二段。\n\n...\n\n123\n\n第三段。\n",
        encoding="utf-8",
    )
    return p


# ---------------- PDF fixture ----------------

def make_pdf(path: Path, pages: list[str], title: str = "") -> Path:
    """用 pymupdf 内置 CJK 字体生成测试 PDF（无外部字体依赖）。"""
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as doc:
        for text in pages:
            page = doc.new_page()
            page.insert_text((72, 100), text, fontname="japan", fontsize=11)
        if title:
            doc.set_metadata({"title": title})
        doc.save(path)
    return path


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    # 注意：pymupdf insert_text + 内置 CJK 字体对超长行会截断，fixture 用短行
    return make_pdf(tmp_path / "book.pdf", [
        "The boy woke to the sound of wind.\nA silver sky lay beyond the window.\n\n\"Your journey begins today.\"",
        "The old man spoke very softly.\n\nCHAPTER 2\n\nThe boy named Allen prepared.",
    ], title="Silver Sky")
