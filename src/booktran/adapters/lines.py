"""行式 Writer：每段一行，meta.line_break 还原段后空行。TXT 与 PDF 输出共用。"""
from __future__ import annotations

from pathlib import Path

from ..model import SourceDoc, Status
from .base import Writer


class LineWriter(Writer):
    def write(
        self,
        doc: SourceDoc,
        source_path: Path,
        out_path: Path,
        *,
        bilingual: bool = False,
        source_first: bool = False,
        lang: str = "",
        target_lang_code: str = "zh",  # noqa: ARG002（协议参数）
    ) -> None:
        parts: list[str] = []
        for seg in doc.segments:
            br = seg.meta.get("line_break", 0)
            if seg.status == Status.EXCLUDED:
                parts.append(seg.source + "\n" + "\n" * br)
                continue
            text = seg.text_for(lang) if lang else seg.final_text
            if bilingual:
                a, b = (seg.source, text) if source_first else (text, seg.source)
                parts.append(a + "\n" + b + "\n" + "\n" * br)
            else:
                parts.append(text + "\n" + "\n" * br)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(parts), encoding="utf-8")
