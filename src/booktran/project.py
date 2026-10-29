"""项目创建与源文档读取（CLI init 与网页端上传共用）。"""
from __future__ import annotations

import copy
from pathlib import Path

from .adapters import READERS
from .config import AppConfig, apply_platform, resolve_task_platform, save_project_manifest
from .model import SourceDoc, Stats, Status
from .store import Store

SUPPORTED_EXTS = {".txt", ".epub", ".pdf", ".mobi", ".azw3",
                  ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp"}


def iter_source_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    files = [p for p in sorted(source.rglob("*"))
             if p.suffix.lower() in SUPPORTED_EXTS and p.is_file()]
    if not files:
        raise SystemExit(f"目录中没有可翻译文件（支持 {'/'.join(SUPPORTED_EXTS)}）: {source}")
    return files


def _relpath(f: Path, source_root: Path) -> str:
    return str(f.relative_to(source_root if source_root.is_dir() else f.parent))


async def read_docs(files: list[Path], source_root: Path,
                     vlm: bool = False, vlm_cfg: AppConfig | None = None,
                     vlm_dpi: int = 150, fake: bool = False,
                     platform: str = "", ocr: bool = False) -> list[SourceDoc]:
    """读取源文件为 SourceDoc。

    vlm=True：PDF 扫描页交多模态模型识别。
    ocr=True：PDF 扫描页走 OCR 通道（本地引擎 → 置信度不够升级多模态）。
    图片文件（jpg/png/webp/tiff/bmp）始终走 OCR 通道识别。
    provider 的创建/使用/关闭必须在同一个事件循环里 —— 因此这里是单个协程，
    调用方（CLI）用 asyncio.run 执行一次，（web）直接 await。
    platform 显式指定接口档案（优先于 routing[vlm]）。
    """
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp"}
    image_files = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
    other_files = [f for f in files if f.suffix.lower() not in IMAGE_EXTS]

    if not vlm and not ocr and not image_files:
        return [READERS[f.suffix.lower().lstrip(".")]().read(f, _relpath(f, source_root))
                for f in other_files]

    from .llm import make_provider
    cfg = copy.deepcopy(vlm_cfg) if vlm_cfg else AppConfig()
    name = platform or resolve_task_platform(cfg, "vlm")
    if name:
        apply_platform(cfg, name)
    provider = make_provider(cfg)
    if fake:
        from .llm.base import FakeProvider
        provider = FakeProvider()
    channel = None
    if ocr or image_files:
        # OCR 通道：本地引擎先跑，置信度不够升级多模态模型
        from .ocr import default_channel
        ocr_profile = cfg.profiles.get(resolve_task_platform(cfg, "ocr") or "")
        channel = default_channel(ocr_profile, provider=provider)
    try:
        out: list[SourceDoc] = []
        for f in image_files:
            from .adapters.image import ImageReader
            out.append(await ImageReader().read(f, _relpath(f, source_root), channel=channel))
        for f in other_files:
            fmt = f.suffix.lower().lstrip(".")
            if fmt == "pdf":
                from .vlm import read_pdf_vlm
                out.append(await read_pdf_vlm(f, _relpath(f, source_root), provider,
                                              dpi=vlm_dpi, channel=channel))
            else:
                out.append(READERS[fmt]().read(f, _relpath(f, source_root)))
        return out
    finally:
        if hasattr(provider, "aclose"):
            await provider.aclose()


def init_project(source: Path, project_dir: Path, cfg: AppConfig,
                 docs: list[SourceDoc] | None = None,
                 files: list[Path] | None = None) -> dict:
    """落盘 manifest + 首个快照，返回摘要。

    docs 与 files 二选一：有 docs 直接用；否则给 files（或省略 = 从 source 推导）。
    不在此处读文档内容——读取（尤其 VLM）是异步的，由调用方先完成。
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    cfg.ensure_langs()
    # 项目清单只存标识（源路径 + 创建时间）；**不存配置快照**——
    # 配置是引用（全局档案 + 项目 booktran.toml 的差异），改了全局立刻生效。
    save_project_manifest(project_dir, source)
    if docs is None:
        files = files or iter_source_files(source)
        root = source if source.is_dir() else source.parent
        docs = [READERS[f.suffix.lower().lstrip(".")]().read(f, _relpath(f, root)) for f in files]
    total = active = 0
    for doc in docs:
        total += len(doc.segments)
        for s in doc.segments:
            s.ensure_tracks(cfg.tr.target_langs)
        active += sum(1 for s in doc.segments
                      if s.status != Status.EXCLUDED
                      and any(t.status != Status.TRANSLATED for t in s.tracks.values()))
    Store(project_dir).save_snapshot(docs, Stats())
    return {
        "dir": str(project_dir),
        "docs": len(docs),
        "segments": total,
        "pending": active,
        "excluded": total - active,
        "target_langs": list(cfg.tr.target_langs),
        "provider": cfg.llm.provider,
        "model": cfg.llm.model,
    }
