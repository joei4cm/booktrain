"""图片适配器：单张/多张图片 → OCR 通道 → Segment 序列。

支持的输入：.jpg / .jpeg / .png / .webp / .tiff / .bmp（扫描页、拍照页）。
识别走 `booktran.ocr` 的通道：本地引擎（tesseract / paddle）先跑，
置信度不够自动升级多模态模型；没有可用引擎时直接走视觉模型。
"""
from __future__ import annotations

from pathlib import Path

from ..model import Segment, SourceDoc, Status
from ..ocr import OcrChannel
from ..util import is_trivial_line
from .base import Reader


class ImageReader(Reader):
    """单张图片 → 一个 SourceDoc（一个段落 = 一个文本块）。"""

    fmt = "image"

    async def read(self, path: Path, relpath: str, *,
                   channel: OcrChannel | None = None) -> SourceDoc:
        if channel is None:
            raise SystemExit(
                f"图片识别需要 OCR 通道：{path} —— "
                "请先定义 ocr 角色档案（booktran profile add ocr --kind ocr …）")
        data = path.read_bytes()
        result = await channel.recognize_page(data)
        doc = SourceDoc(relpath=relpath, fmt="image")
        engine = getattr(result, "engine", "") or "unknown"
        for b in result.blocks:
            meta = {"page": 1, "line_break": 0, "ocr_engine": engine}
            if b.bbox:
                meta["bbox"] = b.bbox
            doc.segments.append(Segment(
                sid=f"{relpath}#{len(doc.segments)}",
                source=b.text,
                status=Status.EXCLUDED if is_trivial_line(b.text) else Status.PENDING,
                meta=meta,
            ))
        if not doc.segments:
            raise SystemExit(
                f"图片识别无结果（引擎：{engine}）: {path} —— "
                "可尝试换 vlm 角色档案（booktran role set vlm=...）")
        return doc


def read_images(paths: list[Path], relpaths: list[str],
                channel: OcrChannel) -> list[SourceDoc]:
    """批量：多张图片各自成一个 SourceDoc（目录上传场景）。"""
    import asyncio

    async def _one(path: Path, relpath: str) -> SourceDoc:
        return await ImageReader().read(path, relpath, channel=channel)

    return asyncio.run(asyncio.gather(*(_one(p, r) for p, r in zip(paths, relpaths, strict=False))))
