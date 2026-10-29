"""TXT 适配器：按行切段，meta.line_break 记录段后空行数，输出时精确还原空行。"""
from __future__ import annotations

from pathlib import Path

from ..model import Segment, SourceDoc, Status
from ..util import detect_encoding, is_trivial_line
from .base import Reader
from .lines import LineWriter


class TxtReader(Reader):
    def read(self, path: Path, relpath: str) -> SourceDoc:
        raw = path.read_bytes()
        _encoding = detect_encoding(raw)  # 读取用；译文输出统一 utf-8
        text = raw.decode(_encoding, errors="replace")
        doc = SourceDoc(relpath=relpath, fmt="txt")
        lines = text.split("\n")
        # 去掉文件末尾的空行尾巴（还原时统一补一个结尾换行）
        while lines and lines[-1].strip() == "":
            lines.pop()
        idx = 0
        last_seg: Segment | None = None
        for line in lines:
            if line.strip() == "":
                if last_seg is not None:
                    last_seg.meta["line_break"] += 1  # 空行归属上一段
                continue
            stripped = line.rstrip("\r")
            status = Status.EXCLUDED if is_trivial_line(stripped) else Status.PENDING
            seg = Segment(
                sid=f"{relpath}#{idx}",
                source=stripped,
                status=status,
                meta={"line_break": 0},
                kind="para",
            )
            doc.segments.append(seg)
            last_seg = seg
            idx += 1
        return doc


# TXT 输出逻辑与 PDF 共用（行式还原）
TxtWriter = LineWriter
