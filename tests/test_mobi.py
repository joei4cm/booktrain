"""MOBI 适配器测试。

约定：不联网、不依赖真实 MOBI 文件——MOBI 头用合成的最小字节流构造，
HTML → 段落走纯函数，转换器（kindleunpack / Calibre）用 monkeypatch 顶替。
"""
from __future__ import annotations

import struct

import pytest

from booktran.adapters import READERS
from booktran.adapters import mobi as mobi_mod
from booktran.adapters.epub import EpubReader
from booktran.adapters.mobi import (
    MobiReader,
    _html_to_segments,
    _pack_html_as_epub3,
    _sniff_mobi,
    mobi_to_epub,
)
from booktran.model import Status

HTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>测试书</title></head>
<body>
  <h1>第一章　始まり</h1>
  <p>これは最初の段落です。物語が始まります。</p>
  <h2>小節</h2>
  <p>二番目の段落には <i>強調</i> が含まれています。</p>
  <div><p>div の中の段落は単独の塊になる。</p></div>
  <p>...</p>
  <p>123</p>
  <p>   </p>
  <p></p>
</body>
</html>
"""


def make_mobi_bytes(*, crypto_type: int = 0, drm: bool = False, text: bytes = b"hello") -> bytes:
    """构造最小 MOBI 字节流（PalmDB 库头 + PalmDOC 头 + MOBI 头）。

    只保证头部能被正确嗅探，不保证正文可解——真实解包交给 kindleunpack/Calibre。
    """
    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), 1, 4096, crypto_type, 0)
    mobi = bytearray(0xC0)
    mobi[0:4] = b"MOBI"
    struct.pack_into(">L", mobi, 0x04, len(mobi))       # MOBI 头长度
    struct.pack_into(">L", mobi, 0x08, 2)              # 类型：2 = 图书
    struct.pack_into(">L", mobi, 0x0C, 1252)           # 编码
    struct.pack_into(">L", mobi, 0x14, 8)              # 版本
    # DRM 字段相对 MOBI 头起点（rec0 + 0x10）
    struct.pack_into(">L", mobi, 0x98, 0x1234 if drm else 0)
    struct.pack_into(">L", mobi, 0xA4, 1 if drm else 0)
    rec0 = palmdoc + bytes(mobi)

    header = bytearray(78)
    header[0:32] = b"BOOKMOBI".ljust(32, b"\x00")
    header[0x3C:0x40] = b"BOOK"
    header[0x40:0x44] = b"MOBI"
    struct.pack_into(">H", header, 0x4C, 1)            # 1 条记录
    rec0_offset = 78 + 8 * 1 + 2
    record_info = struct.pack(">L", rec0_offset) + b"\x00\x00\x00\x00"
    return bytes(header) + record_info + b"\x00\x00" + rec0


def _converter_available() -> bool:
    """环境里已经有转换器时，相关用例改成跳过（不该依赖外部工具）。"""
    return mobi_mod._has_kindleunpack() or mobi_mod._has_calibre()


# ---------------- 头部嗅探 ----------------

def test_synth_mobi_header_parsed(tmp_path):
    """合成的最小 MOBI 头能被正确解析。"""
    p = tmp_path / "book.mobi"
    p.write_bytes(make_mobi_bytes())
    info = _sniff_mobi(p)
    assert info.crypto_type == 0
    assert info.encrypted is False
    assert info.text_length == 5
    assert info.text_records == 1
    assert info.codepage == 1252
    assert info.has_mobi_header is True


def test_synth_mobi_drm_rejected(tmp_path):
    """DRM 文件的报错文案与"损坏"区分开。"""
    p = tmp_path / "drm.mobi"
    p.write_bytes(make_mobi_bytes(crypto_type=2))
    with pytest.raises(SystemExit) as exc:
        _sniff_mobi(p)
    assert "DRM" in str(exc.value)


def test_synth_mobi_old_encryption_rejected(tmp_path):
    """旧式 MobiPocket 加密同样不可读，文案单独说。"""
    p = tmp_path / "enc.mobi"
    p.write_bytes(make_mobi_bytes(crypto_type=1))
    with pytest.raises(SystemExit) as exc:
        _sniff_mobi(p)
    assert "加密" in str(exc.value)


def test_drm_section_flag_rejected(tmp_path):
    """MOBI 头里的 DRM 段存在也算受保护。"""
    p = tmp_path / "drm2.mobi"
    p.write_bytes(make_mobi_bytes(drm=True))
    with pytest.raises(SystemExit) as exc:
        _sniff_mobi(p)
    assert "DRM" in str(exc.value)


# ---------------- 读取错误路径 ----------------

def test_read_corrupt_file_message(tmp_path):
    """损坏文件给出可读的错误（而不是堆栈）。"""
    p = tmp_path / "broken.mobi"
    p.write_bytes(b"not a mobi at all, just random bytes" * 4)
    with pytest.raises(SystemExit) as exc:
        MobiReader().read(p, "broken.mobi")
    msg = str(exc.value)
    assert "损坏" in msg or "不是有效的 MOBI" in msg


def test_read_truncated_file_message(tmp_path):
    """头部不完整也算损坏。"""
    p = tmp_path / "short.mobi"
    p.write_bytes(b"BOOK")
    with pytest.raises(SystemExit) as exc:
        MobiReader().read(p, "short.mobi")
    assert "损坏" in str(exc.value) or "不是有效的 MOBI" in str(exc.value)


def test_read_without_converter_message(tmp_path):
    """既没有 kindleunpack 也没有 Calibre 时，错误要说清缺什么、怎么装。"""
    if _converter_available():
        pytest.skip("环境里已有转换器，用例前提不成立")
    p = tmp_path / "book.mobi"
    p.write_bytes(make_mobi_bytes())
    with pytest.raises(SystemExit) as exc:
        MobiReader().read(p, "book.mobi")
    msg = str(exc.value)
    assert "kindleunpack" in msg or "Calibre" in msg
    assert "pip install" in msg or "apt install" in msg


def test_mobi_to_epub_without_converter_message(tmp_path):
    """mobi_to_epub 在没有转换器时同样给出清晰错误。"""
    if _converter_available():
        pytest.skip("环境里已有转换器，用例前提不成立")
    p = tmp_path / "book.mobi"
    p.write_bytes(make_mobi_bytes())
    out = tmp_path / "book.epub"
    with pytest.raises(SystemExit) as exc:
        mobi_to_epub(p, out)
    msg = str(exc.value)
    assert "kindleunpack" in msg or "Calibre" in msg
    assert not out.exists()


def test_mobi_to_epub_rejects_corrupt(tmp_path):
    """损坏文件在转换前就被拦下。"""
    p = tmp_path / "broken.mobi"
    p.write_bytes(b"\x00" * 200)
    with pytest.raises(SystemExit) as exc:
        mobi_to_epub(p, tmp_path / "out.epub")
    assert "损坏" in str(exc.value) or "不是有效的 MOBI" in str(exc.value)


# ---------------- HTML → 段落 ----------------

def test_html_to_segments_blocks():
    """块级标签切段：标题层级、行内标签保留、无意义块标 EXCLUDED。"""
    segs = _html_to_segments(HTML, "book.mobi")
    kinds = [(s.meta["kind"], s.meta["level"]) for s in segs]
    assert kinds == [
        ("title", 0),      # <head><title>
        ("heading", 1),    # h1
        ("para", 0),
        ("heading", 2),    # h2
        ("para", 0),
        ("para", 0),       # div 里的 p（div 本身不成段）
        ("para", 0),       # "..."
        ("para", 0),       # "123"
        ("para", 0),       # 空白行
    ]
    # 标题文本
    assert segs[1].source == "第一章　始まり"
    assert segs[3].source == "小節"
    # 行内标签保留在正文里（模型据此判断强调位置）
    assert "<i>強調</i>" in segs[4].source
    # 纯符号 / 纯数字 / 空白 → EXCLUDED
    assert segs[6].status == Status.EXCLUDED
    assert segs[7].status == Status.EXCLUDED
    assert segs[8].status == Status.EXCLUDED
    # 正文段是 PENDING
    assert segs[0].status == Status.PENDING
    assert segs[2].status == Status.PENDING
    # 完全空的 <p></p> 不成段
    assert len(segs) == 9


def test_html_to_segments_sids_continuous():
    """sid 连续，且沿用 relpath#i 的格式。"""
    segs = _html_to_segments(HTML, "book.mobi")
    assert [s.sid for s in segs] == [f"book.mobi#{i}" for i in range(len(segs))]


def test_html_to_segments_meta_keeps_source():
    """meta 保留原始块 HTML 与来源信息，供后续回写。"""
    segs = _html_to_segments(HTML, "book.mobi")
    seg = segs[2]
    assert seg.meta["file"] == "book.mobi"
    assert seg.meta["html"].startswith("<p>")
    assert "これは最初の段落です。" in seg.meta["html"]
    assert seg.meta["kind"] == "para"
    assert seg.meta["level"] == 0


def test_html_to_segments_start_offset():
    """start 参数让多份 HTML 的 sid 接着往下排。"""
    first = _html_to_segments(HTML, "book.mobi")
    second = _html_to_segments(HTML, "book.mobi", start=len(first))
    assert second[0].sid == f"book.mobi#{len(first)}"


# ---------------- Reader 流程 ----------------

def test_reader_uses_unpacked_html(monkeypatch, tmp_path):
    """用顶替的解包器喂 HTML，验证 Reader 的整体流程与段元数据。"""
    html = tmp_path / "book.html"
    html.write_text(HTML, encoding="utf-8")
    mobi_file = tmp_path / "book.mobi"
    mobi_file.write_bytes(make_mobi_bytes())

    monkeypatch.setattr(
        mobi_mod, "_try_kindleunpack",
        lambda path, workdir, errors: html,
    )
    doc = MobiReader().read(mobi_file, "book.mobi")
    assert doc.fmt == "mobi"
    assert doc.relpath == "book.mobi"
    assert doc.segments[0].source == "测试书"
    assert doc.segments[1].meta["kind"] == "heading"
    assert doc.segments[1].meta["level"] == 1
    assert doc.segments[1].sid == "book.mobi#1"
    assert doc.meta["mobi"]["text_records"] == 1


def test_reader_registered():
    """reader 在适配器注册表里（.mobi / .azw3 都指向 MobiReader）。"""
    assert READERS["mobi"] is MobiReader
    assert READERS["azw3"] is MobiReader


# ---------------- EPUB3 打包 ----------------

def test_mobi_to_epub_with_unpacked_html(monkeypatch, tmp_path):
    """解包出 HTML 时，mobi_to_epub 自己打包成 EPUB3（整条流程都被覆盖到）。"""
    html = tmp_path / "book.html"
    html.write_text(HTML, encoding="utf-8")
    src = tmp_path / "book.mobi"
    src.write_bytes(make_mobi_bytes())
    monkeypatch.setattr(
        mobi_mod, "_try_kindleunpack",
        lambda path, workdir, errors: html,
    )
    out = tmp_path / "book.epub"
    mobi_to_epub(src, out)
    assert out.exists()
    doc = EpubReader().read(out, "book.epub")
    assert any("第一章　始まり" in s.source for s in doc.segments)


def test_pack_html_as_epub3(tmp_path):
    """解包出来的 HTML 能打包成 EPUB3，并且能被 EpubReader 读回来。"""
    html = tmp_path / "book.html"
    html.write_text(HTML, encoding="utf-8")
    out = tmp_path / "book.epub"
    _pack_html_as_epub3(html, out)

    import zipfile

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        mimetype = zf.read("mimetype").decode()
        opf = zf.read("OEBPS/content.opf").decode()
    assert mimetype == "application/epub+zip"
    assert "OEBPS/nav.xhtml" in names
    assert "OEBPS/text.xhtml" in names
    assert 'version="3.0"' in opf
    assert 'properties="nav"' in opf

    doc = EpubReader().read(out, "book.epub")
    assert any("第一章　始まり" in s.source for s in doc.segments)


def test_upgrade_epub2_to_epub3(tmp_path):
    """EPUB2 转换产物会被升成 EPUB3（补 nav、改版本号）。"""
    import zipfile

    src = tmp_path / "in.epub"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n'
        ))
        zf.writestr("OEBPS/content.opf", (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            '    <dc:title>测试书</dc:title><dc:language>ja</dc:language>\n'
            '    <dc:identifier id="uid">x</dc:identifier>\n'
            '  </metadata>\n'
            '  <manifest>\n'
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
            '    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>\n'
            '  </manifest>\n'
            '  <spine toc="ncx"><itemref idref="c1"/></spine>\n'
            '</package>\n'
        ))
        zf.writestr("OEBPS/toc.ncx", (
            '<?xml version="1.0"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            '<navMap><navPoint id="n1"><navLabel><text>第一章</text></navLabel>'
            '<content src="ch1.xhtml"/></navPoint></navMap></ncx>\n'
        ))
        zf.writestr("OEBPS/ch1.xhtml", "<html><body><p>本文</p></body></html>")

    out = tmp_path / "out.epub"
    mobi_mod._upgrade_epub_to_epub3(src, out)
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode()
        nav = zf.read("OEBPS/nav.xhtml").decode()
    assert 'version="3.0"' in opf
    assert 'properties="nav"' in opf
    assert "第一章" in nav
