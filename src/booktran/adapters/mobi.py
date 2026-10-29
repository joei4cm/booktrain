"""MOBI/AZW3 适配器：把 Kindle 电子书读成可翻译的段落。

解析策略（按优先级逐级回退）：
1. kindleunpack（可选依赖：PyPI 的 `mobi` 是它的无 GUI 分支，GitHub 的 `kindleunpack` 同理）
   → 解包出 HTML/EPUB → 复用 EPUB 的 HTML 解析逻辑切段；
2. Calibre 的 `ebook-convert`（系统命令）
   → 转成 .epub → 用 EpubReader 读取；
3. 两者都没有 → 抛 SystemExit，说明缺什么依赖、怎么装。

DRM/加密 与 损坏文件 分开报错：前者只能换书，后者要重下或换工具，
混成一句话用户没法判断下一步该做什么，所以两类各有各的文案。

MOBI 是 PalmDB（PDB）容器：0x00 是 78 字节的库头，
随后是每条记录 8 字节的记录索引，索引之后才是各条记录的数据；
正文在第 0 条记录里，开头 16 字节是 PalmDOC 头（压缩方式/加密类型等）。
"""
from __future__ import annotations

import html as html_mod
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from ..model import Segment, SourceDoc, Status
from ..util import detect_encoding, is_trivial_line
from .base import Reader
from .epub import EpubReader

# ---------- 容器常量 ----------
_PDB_HEADER_SIZE = 78      # PalmDB 库头长度
_PALMDOC_HEADER_SIZE = 16  # PalmDOC 头长度（压缩/加密/正文长度等）
_MOBI_MAGIC = b"MOBI"      # MOBI 头标识（紧跟在 PalmDOC 头之后）

# 块级标签：与 EPUB reader 保持同一套思路
_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "div", "td")
_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}

# 缺依赖时的统一文案（说清缺什么、怎么装）
_MISSING_READER = (
    "MOBI 读取需要 kindleunpack（pip install kindleunpack，或 pip install mobi）"
    "或 Calibre（apt install calibre）"
)
_MISSING_CONVERTER = (
    "MOBI 转 EPUB 需要 kindleunpack（pip install kindleunpack，或 pip install mobi）"
    "或 Calibre（apt install calibre）"
)


@dataclass
class MobiInfo:
    """MOBI 头部摘要（够判断"能不能读"就行）。"""

    compression: int = 0
    crypto_type: int = 0
    text_length: int = 0
    text_records: int = 0
    codepage: int = 0
    version: int = 0
    drm_offset: int = 0
    drm_flags: int = 0
    has_mobi_header: bool = False

    @property
    def encrypted(self) -> bool:
        return self.crypto_type != 0


# ---------------- 头部嗅探：损坏 / DRM 分开报错 ----------------

def _sniff_mobi(path: Path) -> MobiInfo:
    """解析 MOBI 头部；文件损坏或受保护时抛 SystemExit（文案区分两类问题）。"""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SystemExit(f"MOBI 文件读取失败: {path}（{e}）") from e

    if len(data) < _PDB_HEADER_SIZE:
        raise SystemExit(f"文件损坏或不是有效的 MOBI（库头不完整）: {path}")
    if data[0x3C:0x40] != b"BOOK" or data[0x40:0x44] != b"MOBI":
        raise SystemExit(f"不是有效的 MOBI 文件（缺少 BOOKMOBI 标识）: {path}")
    num_records = struct.unpack_from(">H", data, 0x4C)[0]
    if num_records == 0:
        raise SystemExit(f"文件损坏或不是有效的 MOBI（没有任何记录）: {path}")
    # 第 0 条记录的数据偏移在记录索引表的第一项
    rec0_off = struct.unpack_from(">L", data, _PDB_HEADER_SIZE)[0]
    if rec0_off + _PALMDOC_HEADER_SIZE > len(data):
        raise SystemExit(f"文件损坏或不是有效的 MOBI（记录偏移越界）: {path}")

    rec0 = data[rec0_off:]
    compression = struct.unpack_from(">H", rec0, 0x00)[0]
    text_length = struct.unpack_from(">L", rec0, 0x04)[0]
    text_records = struct.unpack_from(">H", rec0, 0x08)[0]
    crypto_type = struct.unpack_from(">H", rec0, 0x0C)[0]
    has_mobi = rec0[0x10:0x14] == _MOBI_MAGIC

    codepage = version = drm_offset = drm_flags = 0
    if has_mobi and len(rec0) >= 0x28:
        codepage = struct.unpack_from(">L", rec0, 0x1C)[0]
        version = struct.unpack_from(">L", rec0, 0x24)[0]
    if has_mobi and len(rec0) >= 0xB8:
        drm_offset = struct.unpack_from(">L", rec0, 0xA8)[0]
        drm_flags = struct.unpack_from(">L", rec0, 0xB4)[0]

    info = MobiInfo(
        compression=compression,
        crypto_type=crypto_type,
        text_length=text_length,
        text_records=text_records,
        codepage=codepage,
        version=version,
        drm_offset=drm_offset,
        drm_flags=drm_flags,
        has_mobi_header=has_mobi,
    )
    # 加密类型：0=无，1=旧式 MobiPocket 加密，2=DRM
    if crypto_type == 2 or (drm_offset and drm_flags & 0x01):
        raise SystemExit(f"该文件受 DRM 保护，无法读取: {path}")
    if crypto_type == 1:
        raise SystemExit(f"该文件受加密保护（MobiPocket 旧式加密），无法读取: {path}")
    return info


# ---------------- 依赖探测 ----------------

def _import_optional(name: str):
    """按需导入可选依赖；没装或导入失败都返回 None（不抛错，交由上层给可操作提示）。"""
    try:
        import importlib

        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 — 可选依赖缺失/损坏都不该让整个流程崩掉
        return None


def _has_kindleunpack() -> bool:
    if _import_optional("mobi") is not None or _import_optional("kindleunpack") is not None:
        return True
    return bool(shutil.which("kindleunpack") or shutil.which("mobiunpack"))


def _has_calibre() -> bool:
    return bool(shutil.which("ebook-convert"))


# ---------------- 转换器：kindleunpack → Calibre ----------------

def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout,
    )


def _looks_encrypted(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "encrypt" in text or "drm" in text


def _try_kindleunpack(path: Path, workdir: Path, errors: list[str]) -> Path | None:
    """用 kindleunpack 家族解包，返回解包产物路径（epub/html/pdf）；失败返回 None。

    依次尝试：PyPI 的 `mobi`（KindleUnpack 无 GUI 分支）→ `kindleunpack` 模块 → 命令行。
    """
    # 1) PyPI 包 mobi：extract() 返回 (临时目录, 产物文件)
    mod = _import_optional("mobi")
    if mod is not None and hasattr(mod, "extract"):
        try:
            tempdir, filepath = mod.extract(str(path))
        except Exception as e:  # noqa: BLE001
            if _looks_encrypted(e):
                raise SystemExit(f"该文件受 DRM/加密保护，无法读取: {path}") from e
            errors.append(f"mobi 解包失败：{e}")
            return None
        try:
            dest = workdir / "ku"
            shutil.copytree(tempdir, dest, dirs_exist_ok=True)
            return dest / Path(filepath).relative_to(tempdir)
        except Exception as e:  # noqa: BLE001
            errors.append(f"mobi 解包产物读取失败：{e}")
            return None
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    # 2) GitHub 版 kindleunpack（可作为模块导入）
    mod = _import_optional("kindleunpack")
    if mod is not None:
        unpack = getattr(mod, "unpackBook", None)
        if unpack is None:
            inner = getattr(mod, "kindleunpack", None)
            unpack = getattr(inner, "unpackBook", None) if inner else None
        if unpack is not None:
            outdir = workdir / "ku"
            outdir.mkdir(parents=True, exist_ok=True)
            try:
                unpack(str(path), str(outdir))
            except Exception as e:  # noqa: BLE001
                if _looks_encrypted(e):
                    raise SystemExit(f"该文件受 DRM/加密保护，无法读取: {path}") from e
                errors.append(f"kindleunpack 解包失败：{e}")
                return None
            found = _find_unpacked(outdir)
            if found:
                return found
            errors.append("kindleunpack 解包没有产出 html/epub")

    # 3) 命令行（各版本参数不一致，逐个试）
    exe = shutil.which("kindleunpack") or shutil.which("mobiunpack")
    if exe:
        outdir = workdir / "ku"
        outdir.mkdir(parents=True, exist_ok=True)
        for args in (
            [exe, "--epub_version=3", str(path), str(outdir)],
            [exe, str(path), str(outdir)],
            [exe, "-o", str(outdir), str(path)],
        ):
            try:
                proc = _run(args, cwd=outdir)
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{Path(exe).name} 调用失败：{e}")
                break
            if proc.returncode == 0:
                found = _find_unpacked(outdir)
                if found:
                    return found
                errors.append(f"{Path(exe).name} 没有产出 html/epub")
                continue
            if _looks_encrypted(RuntimeError(proc.stderr or "")):
                raise SystemExit(f"该文件受 DRM/加密保护，无法读取: {path}")
            errors.append(f"{Path(exe).name} 退出码 {proc.returncode}：{(proc.stderr or '').strip()[:200]}")
    return None


def _find_unpacked(outdir: Path) -> Path | None:
    """在解包目录里找正文产物：优先 epub，其次 html。"""
    for pattern in ("*.epub", "**/*.epub"):
        found = sorted(outdir.glob(pattern))
        if found:
            return found[0]
    for pattern in ("*.html", "*.htm", "**/*.html", "**/*.htm"):
        found = sorted(outdir.glob(pattern))
        if found:
            # 同一批里挑最大的那个，通常是正文
            return max(found, key=lambda p: p.stat().st_size)
    return None


def _try_calibre(path: Path, workdir: Path, errors: list[str]) -> Path | None:
    """用 Calibre 的 ebook-convert 转成 epub；失败返回 None。"""
    exe = shutil.which("ebook-convert")
    if not exe:
        return None
    out = workdir / (path.stem + ".epub")
    try:
        proc = _run([exe, str(path), str(out)])
    except (OSError, subprocess.SubprocessError) as e:
        errors.append(f"ebook-convert 调用失败：{e}")
        return None
    if proc.returncode != 0 or not out.exists():
        errors.append(f"ebook-convert 失败：{(proc.stderr or '').strip()[:300]}")
        return None
    return out


def _extract_content(path: Path, workdir: Path) -> tuple[str, Path]:
    """按优先级拿到内容产物，返回 (kind, path)，kind ∈ {"epub", "html"}。"""
    errors: list[str] = []
    ku = _try_kindleunpack(path, workdir, errors)
    if ku is not None:
        suffix = ku.suffix.lower()
        if suffix == ".epub":
            return "epub", ku
        if suffix in (".html", ".htm", ".xhtml"):
            return "html", ku
        raise SystemExit(
            f"MOBI 解包结果是 {suffix or '未知格式'}（Kindle Print Replica 版式），"
            f"当前不支持：{path}"
        )
    cal = _try_calibre(path, workdir, errors)
    if cal is not None:
        return "epub", cal
    msg = _MISSING_READER
    if errors:
        msg += "；已尝试的转换器都没成功：" + "；".join(errors)
    raise SystemExit(msg)


# ---------------- HTML → 段落 ----------------

def _plain_text(tag) -> str:
    """块内纯文本（不含标签），用于判断是否"无意义"。"""
    return "".join(str(s) for s in tag.strings)


def _has_block_child(tag) -> bool:
    """块里是否还套着块级标签（有的话交给子块，避免父子重复切段）。"""
    return any(child.name in _BLOCK_TAGS for child in tag.find_all(True) if child.name)


def _kind_level(tag_name: str) -> tuple[str, int]:
    if tag_name in _HEADING_LEVEL:
        return "heading", _HEADING_LEVEL[tag_name]
    return "para", 0


def _html_to_segments(
    html: str,
    relpath: str,
    *,
    src_file: str = "",
    start: int = 0,
) -> list[Segment]:
    """HTML → 段落（纯函数，方便直接测）。

    与 EPUB reader 的差别只有一处：这里**保留**"无意义"的块（纯符号、空行），
    标成 EXCLUDED 而不是丢掉——MOBI 正文里这类块多，丢掉会让回写时原文对不上。
    """
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup.find_all(["script", "style"]):
        bad.decompose()

    segs: list[Segment] = []
    idx = start

    # 文档标题单独成段（与 EPUB reader 一致）
    if soup.title is not None:
        title_plain = _plain_text(soup.title).strip()
        if title_plain:
            segs.append(_make_segment(
                title_plain, title_plain, relpath, "title", 0, src_file, idx,
                html=str(soup.title), tag="title",
            ))
            idx += 1

    for tag in soup.find_all(_BLOCK_TAGS):
        if _has_block_child(tag):
            continue  # 容器块：内部子块会各自成段
        plain = _plain_text(tag)
        if not plain:
            continue  # 完全空的块（如 <p></p>、只有图片的块）没有可翻译内容
        # 复用 EPUB reader 的块内文本提取：行内标签（<i>/<b>/<a>…）保留在正文里，
        # 模型据此判断强调位置，回写时再按原文结构套回去
        text = EpubReader._block_text(tag)
        kind, level = _kind_level(tag.name)
        segs.append(_make_segment(
            text, plain, relpath, kind, level, src_file, idx,
            html=str(tag), tag=tag.name,
        ))
        idx += 1
    return segs


def _make_segment(
    text: str,
    plain: str,
    relpath: str,
    kind: str,
    level: int,
    src_file: str,
    idx: int,
    *,
    html: str = "",
    tag: str = "",
) -> Segment:
    """造一个 Segment（meta 里留好回写要用的原始块 HTML）。"""
    return Segment(
        sid=f"{relpath}#{idx}",
        source=text,
        status=Status.EXCLUDED if is_trivial_line(plain) else Status.PENDING,
        meta={
            "html": html,
            "file": relpath,
            "kind": kind,
            "level": level,
            "tag": tag,
            "src_file": src_file,
        },
    )


def _segments_from_html(html: str, relpath: str, *, src_file: str = "") -> list[Segment]:
    """HTML → Segment 列表（meta 里带原始块 HTML，供回写用）。"""
    return _html_to_segments(html, relpath, src_file=src_file)


def _segments_from_epub(epub_path: Path, relpath: str) -> list[Segment]:
    """EPUB → Segment 列表（复用 EpubReader 的解析，统一成 MOBI 的 meta 形状）。"""
    doc = EpubReader().read(epub_path, relpath)
    out: list[Segment] = []
    for i, seg in enumerate(doc.segments):
        tag = seg.meta.get("tag", "")
        kind, level = _kind_level(tag)
        if tag == "title":
            kind, level = "title", 0
        out.append(Segment(
            sid=f"{relpath}#{i}",
            source=seg.source,
            status=seg.status,
            meta={
                "html": seg.meta.get("original_html", ""),
                "file": relpath,
                "kind": kind,
                "level": level,
                "tag": tag,
                "src_file": seg.meta.get("file", ""),
            },
        ))
    return out


# ---------------- 读入 ----------------

class MobiReader(Reader):
    """MOBI/AZW3 读取器（需要 kindleunpack 或 Calibre 之一）。"""

    fmt = "mobi"

    def read(self, path: Path, relpath: str) -> SourceDoc:
        path = Path(path)
        info = _sniff_mobi(path)  # 损坏 / DRM 在这里就分清楚报错
        with tempfile.TemporaryDirectory(prefix="booktran-mobi-") as tmp:
            kind, payload = _extract_content(path, Path(tmp))
            if kind == "html":
                raw = payload.read_bytes()
                html = raw.decode(detect_encoding(raw), errors="replace")
                segments = _segments_from_html(html, relpath, src_file=payload.name)
            else:
                segments = _segments_from_epub(payload, relpath)

        doc = SourceDoc(relpath=relpath, fmt="mobi")
        doc.segments = segments
        doc.meta = {"mobi": {
            "compression": info.compression,
            "text_length": info.text_length,
            "text_records": info.text_records,
            "codepage": info.codepage,
            "version": info.version,
        }}
        return doc


# ---------------- MOBI → EPUB3 ----------------

def _xhtml_escape(text: str) -> str:
    return html_mod.escape(text, quote=True)


def _xhtml_document(body: str, title: str, lang: str = "zh") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">\n'
        f"<head><title>{_xhtml_escape(title)}</title></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _nav_document(title: str, href: str = "text.xhtml") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">\n'
        "<head><title>目录</title></head>\n"
        '<body><nav epub:type="toc"><ol>'
        f'<li><a href="{_xhtml_escape(href)}">{_xhtml_escape(title)}</a></li>'
        "</ol></nav></body>\n</html>\n"
    )


def _container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n</container>\n"
    )


def _opf_document(
    title: str,
    uid: str,
    items: list[tuple[str, str, str, str]],
    spine: list[str],
) -> str:
    """items: (id, href, media-type, 额外属性) 列表。"""
    manifest = "\n".join(
        f'    <item id="{iid}" href="{href}" media-type="{mtype}"{extra}/>'
        for iid, href, mtype, extra in items
    )
    itemrefs = "\n".join(f'    <itemref idref="{iid}"/>' for iid in spine)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="zh">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{_xhtml_escape(title)}</dc:title>
    <dc:language>zh</dc:language>
    <dc:identifier id="uid">{_xhtml_escape(uid)}</dc:identifier>
    <meta property="dcterms:modified">2024-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine>
{itemrefs}
  </spine>
</package>
"""


def _to_xhtml_body(node) -> str:
    """把一段 DOM 变成 XHTML 合法的主体内容。

    解包出来的 HTML 多半不是 XML（未闭合标签、HTML 实体等），
    这里用 BeautifulSoup 归一化后再用 XML 解析器验一遍；
    验不过就退化成纯文本段落——宁可少点样式，也不能产出一个打不开的 EPUB。
    """
    raw = node.decode() if hasattr(node, "decode") else str(node)
    try:
        import xml.etree.ElementTree as ET

        ET.fromstring(f"<div>{raw}</div>")
        return raw
    except Exception:  # noqa: BLE001
        texts = [t.strip() for t in node.get_text("\n").split("\n") if t.strip()]
        if not texts:
            return ""
        return "\n".join(f"<p>{_xhtml_escape(t)}</p>" for t in texts)


def _pack_html_as_epub3(html_path: Path, out_path: Path, *, title: str = "") -> Path:
    """把解包出来的 HTML（连同图片）打包成最小 EPUB3。"""
    html_path = Path(html_path)
    raw = html_path.read_bytes()
    html = raw.decode(detect_encoding(raw), errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup.find_all(["script", "style"]):
        bad.decompose()
    body = soup.body if soup.body is not None else soup
    doc_title = title or (soup.title.get_text(strip=True) if soup.title else html_path.stem)

    # 图片：改成 EPUB 内的相对路径，并把文件收进包里
    images: list[Path] = []
    for img in body.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        src_file = (html_path.parent / src).resolve()
        if not src_file.is_file():
            continue
        img["src"] = f"images/{src_file.name}"
        if src_file not in images:
            images.append(src_file)

    body_xhtml = _to_xhtml_body(body)
    items: list[tuple[str, str, str, str]] = [
        ("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'),
        ("c1", "text.xhtml", "application/xhtml+xml", ""),
    ]
    spine = ["c1"]
    for i, img_file in enumerate(images, start=1):
        mtype = "image/svg+xml" if img_file.suffix.lower() == ".svg" else f"image/{img_file.suffix.lstrip('.').lower()}"
        items.append((f"img{i}", f"images/{img_file.name}", mtype, ""))

    opf = _opf_document(doc_title, f"booktran-mobi-{html_path.stem}", items, spine)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", _nav_document(doc_title))
        zf.writestr("OEBPS/text.xhtml", _xhtml_document(body_xhtml, doc_title))
        for img_file in images:
            zf.writestr(f"OEBPS/images/{img_file.name}", img_file.read_bytes())
    return out_path


def _nav_from_ncx(contents: dict[str, bytes]) -> str:
    """用 toc.ncx 生成 EPUB3 的 nav 文档；没有 NCX 就返回空串。"""
    ncx_name = next((n for n in contents if n.lower().endswith(".ncx")), "")
    if not ncx_name:
        return ""
    raw = contents[ncx_name].decode("utf-8", errors="replace")
    base = ncx_name.rsplit("/", 1)[0] + "/" if "/" in ncx_name else ""
    items: list[str] = []
    for m in re.finditer(r"<navPoint\b.*?</navPoint>", raw, re.DOTALL):
        block = m.group(0)
        label = re.search(r"<text\b[^>]*>(.*?)</text>", block, re.DOTALL)
        src = re.search(r'<content\s+src="([^"]+)"', block)
        if not label or not src:
            continue
        text = re.sub(r"<[^>]+>", "", label.group(1)).strip()
        href = base + src.group(1)
        if text and href:
            items.append(f'<li><a href="{_xhtml_escape(href)}">{_xhtml_escape(text)}</a></li>')
    if not items:
        return ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">\n'
        "<head><title>目录</title></head>\n"
        '<body><nav epub:type="toc"><ol>' + "\n".join(items) + "</ol></nav></body>\n</html>\n"
    )


def _upgrade_epub_to_epub3(src: Path, out_path: Path) -> Path:
    """把 EPUB2 升成 EPUB3（补 nav 文档、改 OPF 版本）；本来就是 EPUB3 就原样复制。"""
    src = Path(src)
    out_path = Path(out_path)
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        contents = {n: zin.read(n) for n in names}
        try:
            opf_name = EpubReader._find_opf(zin)
        except Exception:  # noqa: BLE001
            opf_name = next((n for n in names if n.lower().endswith(".opf")), "")
    if not opf_name:
        raise SystemExit(f"转换产物不是有效的 EPUB（找不到 OPF）: {src}")

    opf = contents[opf_name].decode("utf-8", errors="replace")
    m = re.search(r"<package\b[^>]*\bversion=\"([^\"]+)\"", opf)
    version = m.group(1) if m else "2.0"

    if version.startswith("3"):
        _write_zip(out_path, names, contents)
        return out_path

    # EPUB2 → EPUB3：加 nav 文档 + 把 package version 改成 3.0
    opf_dir = opf_name.rsplit("/", 1)[0] + "/" if "/" in opf_name else ""
    nav_name = opf_dir + "nav.xhtml"
    nav = _nav_from_ncx(contents)
    if not nav:
        first = re.search(r"<itemref\b[^>]*idref=\"([^\"]+)\"", opf)
        href = ""
        if first:
            im = re.search(rf"<item\b[^>]*id=\"{re.escape(first.group(1))}\"[^>]*href=\"([^\"]+)\"", opf)
            if not im:
                im = re.search(rf"<item\b[^>]*href=\"([^\"]+)\"[^>]*id=\"{re.escape(first.group(1))}\"", opf)
            href = im.group(1) if im else ""
        nav = _nav_document("正文", href or "text.xhtml")
    contents[nav_name] = nav.encode("utf-8")
    if nav_name not in names:
        names.append(nav_name)

    opf_new = re.sub(r"(<package\b[^>]*\bversion=\")[^\"]+(\")", r"\g<1>3.0\g<2>", opf, count=1)
    if 'properties="nav"' not in opf_new:
        opf_new = opf_new.replace(
            "</manifest>",
            f'    <item id="nav" href="{nav_name.split("/")[-1]}" '
            'media-type="application/xhtml+xml" properties="nav"/>\n  </manifest>',
            1,
        )
    contents[opf_name] = opf_new.encode("utf-8")
    _write_zip(out_path, names, contents)
    return out_path


def _write_zip(out_path: Path, names: list[str], contents: dict[str, bytes]) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:  # 保持原条目顺序，mimetype 仍排最前
            data = contents.get(name)
            if data is None:
                continue
            compress = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            zf.writestr(name, data, compress_type=compress)


def mobi_to_epub(path: Path, out_path: Path) -> Path:
    """把 MOBI/AZW3 转成 EPUB3 写出，返回输出路径。

    优先 kindleunpack（解包出 HTML 就自己打包最小 EPUB3；本来就是 EPUB 就升到 EPUB3），
    其次 Calibre 的 ebook-convert；都没有则报错说明怎么装。
    """
    path = Path(path)
    out_path = Path(out_path)
    _sniff_mobi(path)  # 损坏 / DRM 提前报错
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="booktran-mobi2epub-") as tmp:
        workdir = Path(tmp)
        ku = _try_kindleunpack(path, workdir, errors)
        if ku is not None:
            if ku.suffix.lower() == ".epub":
                return _upgrade_epub_to_epub3(ku, out_path)
            return _pack_html_as_epub3(ku, out_path)
        cal = _try_calibre(path, workdir, errors)
        if cal is not None:
            return _upgrade_epub_to_epub3(cal, out_path)
    msg = _MISSING_CONVERTER
    if errors:
        msg += "；已尝试的转换器都没成功：" + "；".join(errors)
    raise SystemExit(msg)
