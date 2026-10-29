"""EPUB 适配器。

读取：解包 zip → 解析 OPF spine 顺序 → 对每个 XHTML/NCX 文件用块级标签正则
提取文本段（与 AiNiee EpubReader 相同的 original_html 片段策略）；
行内标签（<i>/<b>/<ruby>/<a> 等）保留在段落文本里，让模型看得见强调位置；
额外提取 <head><title> 与 EPUB3 nav 文档。

回写：对已翻译段做 original_html 字符串替换（按文档顺序 replace(…,1)），
把译文按原文的行内结构重新套上标签（模型已保留标签则直接采用），
重建块级标签保留原属性；同时更新 <html lang>、OPF <dc:language>/<dc:title>。
"""
from __future__ import annotations

import copy
import re
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

from ..ir import kind_of_tag, spans_from_html
from ..model import Segment, SourceDoc, Status
from ..util import is_trivial_line
from .base import Reader, Writer

# 单遍块级标签提取（文档顺序扫描；div 用平衡提取修正非贪婪截断）
BLOCK_TAG = re.compile(r"<(h[1-7]|li|p|blockquote|td|div)\b[^>]*>.*?</\1>", re.DOTALL)
# div 内部出现这些块级子标签时跳过（其子标签会被单独匹配到）
DIV_FORBIDDEN = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td")

NCX_TEXT = re.compile(r"<text\b[^>]*>(.*?)</text>", re.DOTALL)
TITLE_TAG = re.compile(r"<title\b[^>]*>.*?</title>", re.DOTALL | re.IGNORECASE)

# 行内标签：回写时按原文结构重新套用
INLINE_TAGS = {
    "i", "b", "em", "strong", "u", "s", "sup", "sub", "small", "big",
    "code", "kbd", "samp", "var", "cite", "q", "abbr", "mark", "time",
    "tt", "del", "ins", "font", "span", "ruby", "rt", "rp", "a", "img",
}
# 读音注记（<rt>）保留原文，不参与译文切分
KEEP_TEXT_TAGS = ("rt", "rp")

_ORIG_STYLE = "opacity:.75;color:#888;font-size:.85em;font-style:italic"


def _attrs_str(tag) -> str:
    parts = []
    for k, v in tag.attrs.items():
        if isinstance(v, list):
            v = " ".join(v)
        parts.append(f' {k}="{v}"')
    return "".join(parts)


def _inline_slots(node) -> list[dict]:
    """把节点内容拍平成槽位序列（text/raw/open/close），用于回写时套用行内结构。"""
    slots: list[dict] = []

    def walk(n, keep: bool = False) -> None:
        for child in n.children:
            if isinstance(child, NavigableString):
                text = str(child)
                if text:
                    slots.append({"kind": "text", "value": text, "keep": keep})
                continue
            name = getattr(child, "name", None)
            if not name:
                continue
            if name == "br":
                slots.append({"kind": "raw", "value": "<br/>"})
            elif name in INLINE_TAGS:
                slots.append({"kind": "open", "value": f"<{name}{_attrs_str(child)}>"})
                walk(child, keep or name in KEEP_TEXT_TAGS)
                slots.append({"kind": "close", "value": f"</{name}>"})
            else:
                walk(child, keep)

    walk(node)
    return slots


def _split_translation(translated: str, slots: list[dict]) -> str:
    """按原文各文本槽的长度比例，把译文切回各槽（保住强调位置）。"""
    split_slots = [s for s in slots if s["kind"] == "text" and not s["keep"] and s["value"].strip()]
    if not split_slots:
        return translated
    total = sum(len(s["value"]) for s in split_slots)
    if total <= 0:
        return translated
    out: list[str] = []
    consumed = 0
    for s in slots:
        if s["kind"] != "text":
            out.append(s["value"])
            continue
        if s["keep"]:
            out.append(s["value"])
            continue
        nxt = consumed + len(s["value"])
        start = int(round(consumed / total * len(translated)))
        end = int(round(nxt / total * len(translated)))
        consumed = nxt
        # 拉丁文避免把单词从中间切开
        if start > 0 and translated[start - 1].isalnum() and translated[start].isalnum():
            sp = translated.rfind(" ", 0, start)
            if sp > 0:
                start = sp + 1
        if end < len(translated) and translated[end - 1].isalnum() and translated[end].isalnum():
            sp = translated.find(" ", end)
            if sp > 0:
                end = sp
        out.append(translated[start:end].strip())
    return "".join(out)


def _tag_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z][\w-]*)", text):
        name = m.group(2).lower()
        counts[name] = counts.get(name, 0) + (1 if not m.group(1) else -1)
    return counts


def _strip_tags(text: str) -> str:
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()


def _structure_key(slots: list[dict]) -> list[tuple]:
    """槽位结构指纹（标签名序列），用于判断模型是否保住了同样的行内结构。"""
    key: list[tuple] = []
    for s in slots:
        if s["kind"] == "open":
            name = re.match(r"<\s*([a-zA-Z][\w-]*)", s["value"])
            key.append(("open", name.group(1).lower() if name else ""))
        elif s["kind"] == "close":
            key.append(("close", ""))
        elif s["kind"] == "raw":
            key.append(("raw", ""))
        else:
            key.append(("text", ""))
    return key


def _apply_original_structure(text: str, original_slots: list[dict]) -> str:
    """把译文套回原文的行内结构。

    - 模型保住了同样的标签序列 → 采用模型的文本 + 原文的标签（属性以原文为准，
      避免模型改坏 <a href>）
    - 否则 → 按原文各文本槽长度比例切分译文（强调位置近似保留）
    """
    if not any(s["kind"] in ("open", "close") for s in original_slots):
        return text
    model_slots = _inline_slots(BeautifulSoup(text, "html.parser"))
    if _structure_key(model_slots) == _structure_key(original_slots):
        out: list[str] = []
        for oslot, mslot in zip(original_slots, model_slots, strict=False):
            if oslot["kind"] == "text":
                out.append(mslot["value"])
            else:
                out.append(oslot["value"])
        return "".join(out)
    return _split_translation(_strip_tags(text), original_slots)


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_balanced(html: str, tag_name: str, start: int = 0) -> str | None:
    """提取同名嵌套平衡的完整标签（AiNiee 同款算法）。"""
    open_pat = re.compile(rf"<{tag_name}(?![\w-])[^>]*>", re.IGNORECASE)
    close_pat = re.compile(rf"</{tag_name}>", re.IGNORECASE)
    m = open_pat.search(html, start)
    if not m:
        return None
    tag_start, depth, pos = m.start(), 1, m.end()
    while depth > 0:
        nxt_open = open_pat.search(html, pos)
        nxt_close = close_pat.search(html, pos)
        if nxt_close is None:
            return None
        if nxt_open is not None and nxt_open.start() < nxt_close.start():
            depth += 1
            pos = nxt_open.end()
        else:
            depth -= 1
            pos = nxt_close.end()
            if depth == 0:
                return html[tag_start:pos]
    return None


class EpubReader(Reader):
    def read(self, path: Path, relpath: str) -> SourceDoc:
        doc = SourceDoc(relpath=relpath, fmt="epub")
        with zipfile.ZipFile(path) as zf:
            opf_name = self._find_opf(zf)
            opf_dir = opf_name.rsplit("/", 1)[0] + "/" if "/" in opf_name else ""
            opf_content = _decode(zf.read(opf_name))
            spine = self._parse_spine(opf_content, opf_dir)
            idx = 0
            for href, is_ncx in spine:
                try:
                    raw = _decode(zf.read(href))
                except KeyError:
                    continue
                if is_ncx:
                    segs = self._read_ncx(raw, href)
                else:
                    segs = self._read_xhtml(raw, href)
                for text, original_html, tag in segs:
                    kind, level = kind_of_tag(tag)
                    doc.segments.append(Segment(
                        sid=f"{relpath}#{idx}",
                        source=text,
                        status=Status.EXCLUDED if is_trivial_line(text) else Status.PENDING,
                        meta={"original_html": original_html, "file": href, "tag": tag},
                        kind=kind,
                        level=level,
                        spans=spans_from_html(original_html),
                    ))
                    idx += 1
        return doc

    @staticmethod
    def _find_opf(zf: zipfile.ZipFile) -> str:
        container = _decode(zf.read("META-INF/container.xml"))
        m = re.search(r'full-path="([^"]+)"', container)
        if not m:
            raise ValueError("EPUB 缺少 container.xml 的 full-path")
        return m.group(1)

    @staticmethod
    def _parse_spine(opf_content: str, opf_dir: str) -> list[tuple[str, bool]]:
        manifest = {}
        for m in re.finditer(
            r'<item\b[^>]*?id="([^"]+)"[^>]*?href="([^"]+)"[^>]*?media-type="([^"]+)"', opf_content
        ):
            manifest[m.group(1)] = (m.group(2), m.group(3))
        # 属性顺序不保证，再做一轮宽松匹配
        for m in re.finditer(r"<item\b([^>]+)/?>", opf_content):
            attrs = dict(re.findall(r'(\w[\w:-]*)="([^"]*)"', m.group(1)))
            if attrs.get("id") and attrs.get("id") not in manifest:
                manifest[attrs["id"]] = (attrs.get("href", ""), attrs.get("media-type", ""))
        result: list[tuple[str, bool]] = []
        for m in re.finditer(r'<itemref\b[^>]*?idref="([^"]+)"', opf_content):
            href, mtype = manifest.get(m.group(1), ("", ""))
            if not href:
                continue
            full = opf_dir + href if not href.startswith("/") else href.lstrip("/")
            if "ncx" in mtype:
                result.append((full, True))
            elif mtype in ("application/xhtml+xml", "text/html") or full.endswith((".xhtml", ".html", ".htm")):
                result.append((full, False))
        # spine 之外的 NCX（老规范 toc.ncx 常不在 spine 里）
        for href, mtype in manifest.values():
            if "ncx" in mtype and not any(h == href for h, _ in result):
                result.append((opf_dir + href if not href.startswith("/") else href.lstrip("/"), True))
        # EPUB3 nav 文档（properties="nav"），通常不在 spine 里
        for m in re.finditer(r"<item\b([^>]+?)/?>", opf_content):
            attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', m.group(1)))
            props = (attrs.get("properties") or "")
            href = attrs.get("href", "")
            if "nav" in props.split() and href and attrs.get("media-type", "") in (
                    "application/xhtml+xml", "text/html", ""):
                full = opf_dir + href if not href.startswith("/") else href.lstrip("/")
                if not any(h == full for h, _ in result):
                    result.append((full, False))
        return result

    def _read_xhtml(self, raw: str, href: str) -> list[tuple[str, str, str]]:
        segs = self._scan_blocks(raw)
        # <head><title>：单独成段，回写时更新标题
        m = TITLE_TAG.search(raw)
        if m:
            title_text = BeautifulSoup(m.group(0), "html.parser").get_text(strip=True)
            if title_text and not is_trivial_line(title_text):
                segs.append((title_text, m.group(0), "title"))
        return segs

    def _scan_blocks(self, raw: str) -> list[tuple[str, str, str]]:
        """单遍扫描保持文档顺序；div 含块级子标签时递归下钻，避免吞段。"""
        segs: list[tuple[str, str, str]] = []
        for m in BLOCK_TAG.finditer(raw):
            tag = m.group(1)
            whole = m.group(0)
            if tag == "div":
                balanced = _extract_balanced(raw, "div", m.start())
                if balanced:
                    whole = balanced
            inner = self._fix_incomplete(whole)
            soup = BeautifulSoup(inner, "html.parser")
            if tag == "div" and any(soup.find(t) for t in DIV_FORBIDDEN):
                # 剥掉外层 div 标签后递归扫描内部块
                inner_content = re.sub(r"^<div\b[^>]*>", "", inner, count=1, flags=re.IGNORECASE)
                inner_content = re.sub(r"</div>\s*$", "", inner_content, count=1, flags=re.IGNORECASE)
                segs.extend(self._scan_blocks(inner_content))
                continue
            # 段落文本保留行内标签（模型据此判断强调位置；回写时按结构套回）
            text = self._block_text(soup)
            plain = soup.get_text(strip=True)
            if not plain or is_trivial_line(plain):
                continue
            segs.append((text, inner, tag))
        return segs

    @staticmethod
    def _block_text(soup) -> str:
        """块内文本（行内标签保留为标记）。"""
        parts: list[str] = []
        for child in soup.contents:
            if isinstance(child, NavigableString):
                parts.append(str(child))
                continue
            name = getattr(child, "name", None)
            if not name:
                continue
            if name in INLINE_TAGS:
                parts.append(f"<{name}{_attrs_str(child)}>")
                parts.append(EpubReader._block_text(child))
                parts.append(f"</{name}>")
            else:
                parts.append(EpubReader._block_text(child))
        text = "".join(parts)
        # 只压缩 ASCII 空白（全角空格 U+3000 是 CJK 排版的缩进，不能动）
        return re.sub(r"[ \t\r\n]+", " ", text).strip()

    def _read_ncx(self, raw: str, href: str) -> list[tuple[str, str, str]]:
        segs = []
        for m in NCX_TEXT.finditer(raw):
            text = m.group(1).strip()
            if not text or is_trivial_line(text):
                continue
            segs.append((text, m.group(0), "ncx_text"))
        return segs

    def _fix_incomplete(self, whole: str) -> str:
        """同名嵌套导致非贪婪正则提前闭合时，取第一个平衡的子标签。"""
        m = re.match(r"<([a-zA-Z][\w-]*)", whole)
        if not m:
            return whole
        tag = m.group(1)
        opens = len(re.findall(rf"<{tag}(?![\w-])[^>]*>", whole, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", whole, re.IGNORECASE))
        if opens > closes:
            first = re.search(rf"<{tag}(?![\w-])[^>]*>", whole, re.IGNORECASE)
            balanced = _extract_balanced(whole, tag, first.end())
            if balanced:
                return balanced
        return whole


class EpubWriter(Writer):
    def write(
        self,
        doc: SourceDoc,
        source_path: Path,
        out_path: Path,
        *,
        bilingual: bool = False,
        source_first: bool = False,
        lang: str = "",
        target_lang_code: str = "zh",
    ) -> None:
        # 按文件分组（保持段序）
        by_file: dict[str, list[Segment]] = {}
        for seg in doc.segments:
            by_file.setdefault(seg.meta["file"], []).append(seg)

        with zipfile.ZipFile(source_path) as zin:
            names = zin.namelist()
            contents: dict[str, bytes] = {}
            opf_name = EpubReader._find_opf(zin)
            for name in names:
                contents[name] = zin.read(name)

            for fname, segs in by_file.items():
                raw = _decode(contents[fname])
                for seg in segs:
                    if not seg.is_done(lang):
                        continue
                    new_html = self._rebuild(
                        seg.meta["original_html"], seg.translated_text(lang),
                        bilingual=bilingual, source_first=source_first,
                    )
                    if new_html:
                        raw = raw.replace(seg.meta["original_html"], new_html, 1)
                if fname.endswith((".xhtml", ".html", ".htm")):
                    raw = re.sub(
                        r"<html\b[^>]*>",
                        lambda mm: re.sub(
                            r'((?:xml:)?lang=")[^"]*"',
                            lambda lmm: lmm.group(1) + target_lang_code + '"',
                            mm.group(0),
                        ),
                        raw, count=1,
                    )
                contents[fname] = raw.encode("utf-8")

            # OPF dc:language
            opf_raw = _decode(contents[opf_name])
            opf_raw = re.sub(
                r"(<dc:language[^>]*>)[^<]*(</dc:language>)",
                rf"\g<1>{target_lang_code}\g<2>", opf_raw, count=1,
            )
            # OPF dc:title：跟随 <head><title> 的译文
            title = next((s.translated_text(lang) for segs in by_file.values() for s in segs
                          if s.meta.get("tag") == "title" and s.is_done(lang)
                          and s.translated_text(lang).strip()), "")
            if title:
                opf_raw = re.sub(
                    r"(<dc:title[^>]*>)[^<]*(</dc:title>)",
                    lambda m: m.group(1) + title.replace("&", "&amp;").replace("<", "&lt;") + m.group(2),
                    opf_raw, count=1,
                )
            contents[opf_name] = opf_raw.encode("utf-8")

            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for name in names:  # 保持原条目顺序，mimetype 仍最前
                    compress = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
                    zout.writestr(name, contents[name], compress_type=compress)

    def _rebuild(self, original_html: str, translated: str, *, bilingual: bool, source_first: bool) -> str | None:
        soup = BeautifulSoup(original_html, "html.parser")
        original_tag = next((e for e in soup.contents if hasattr(e, "name") and e.name), None)
        if original_tag is None:
            return translated

        # 保留原文前导空格（CJK 排版常见全角空格缩进）
        lead = re.match(r"^[ 　]+", original_tag.get_text())
        lead = lead.group(0) if lead else ""
        text = lead + translated.lstrip()

        slots = _inline_slots(original_tag)
        has_inline = any(s["kind"] in ("open", "close") for s in slots)

        if has_inline:
            content = _apply_original_structure(text, slots)
            new = soup.new_tag(original_tag.name, attrs=dict(original_tag.attrs))
            new.append(BeautifulSoup(content, "html.parser"))
            result = str(new)
        else:
            new = soup.new_tag(original_tag.name, attrs=dict(original_tag.attrs))
            new.string = text
            result = str(new)

        if not bilingual:
            return result
        # 双语：原文一份（保留行内结构），译文一份
        orig = copy.deepcopy(original_tag)
        orig_attrs = dict(orig.attrs)
        style = orig_attrs.pop("style", "")
        orig_attrs["style"] = (style + ";" if style and not style.endswith(";") else style) + _ORIG_STYLE
        orig.attrs.clear()
        orig.attrs.update(orig_attrs)
        orig_html = str(orig)
        return f"{orig_html}\n{result}" if source_first else f"{result}\n{orig_html}"
