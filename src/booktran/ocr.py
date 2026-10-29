"""独立 OCR 通道：本地 OCR 引擎优先，置信度不够时升级到多模态模型。

与 vlm.py 的分工：
- vlm.py：整本 PDF 的扫描页识别（有文本层的页走常规抽取，扫描页整页丢给多模态模型）
- ocr.py：**单页**识别的独立通道，可嵌入任意管线。先跑本地引擎（便宜、离线、可控），
  结果不够好（置信度低 / 结果为空 / 没有可用引擎）才升级到多模态模型；
  升级失败则退回本地结果——宁可粗糙也不要空白。

坐标约定与 vlm.py 完全一致：bbox 为归一化 [x0, y0, x1, y1]，左上原点，取值 0~1；
转 Segment 时再按页尺寸换算成 PDF 点坐标（左下原点），与 pymupdf 的 bbox 一致。

依赖策略：tesseract（系统二进制）与 paddleocr（Python 包）都是**可选**依赖，
缺依赖时对应引擎的 available() 返回 False，通道自动降级，不抛异常、不需要装包。
本模块自身不发网络请求——多模态模型由调用方注入（实现 complete_vision 的对象）。
"""
from __future__ import annotations

import asyncio
import inspect
import io
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .vlm import VLM_SYSTEM, parse_blocks

# 多模态模型不报置信度，给一个「可信但非满分」的定值：
# 本地引擎很难超过它 → 升级成功时总是采用模型结果。
VLM_CONFIDENCE = 0.95

# 升级到多模态模型时用的用户提示（与 vlm.py 保持一致，便于复用同一套提示词约定）
VLM_USER = "识别这一页的文本块。"


@dataclass
class OcrBlock:
    """一个文本块。

    bbox：归一化 [x0, y0, x1, y1]，左上原点（与 vlm.py 同约定）；拿不到位置时为 None。
    confidence：0~1。
    """

    text: str
    bbox: list[float] | None = None
    confidence: float = 0.0


@dataclass
class OcrResult:
    """一页的识别结果。

    text：整页文本（块之间空行分隔）。
    blocks：带 bbox 的文本块（可能为空，例如只拿到纯文本时）。
    confidence：页级置信度（0~1），用于判断要不要升级到多模态模型。
    engine：实际来源（"tesseract" / "paddleocr" / "vlm" / "tesseract(fallback)"）。
    """

    text: str = ""
    blocks: list[OcrBlock] = field(default_factory=list)
    confidence: float = 0.0
    engine: str = ""

    @property
    def is_vlm(self) -> bool:
        """结果是否来自多模态模型（决定 Segment.meta["vlm"]）。"""
        return self.engine == "vlm" or self.engine.startswith("vlm(")

    def to_segments(self, relpath: str, page: int = 0, *,
                    page_sizes: dict[int, tuple[float, float]] | None = None,
                    sid_offset: int = 0) -> list[Segment]:
        """转成 Segment（复用 vlm.blocks_to_segments 的字段约定）。

        meta 与 vlm.py 一致：page / line_break / bbox（PDF 点坐标）/ vlm；
        另外附一个 ocr_engine 便于排查是哪条路径产出的。
        page_sizes：页号 → (宽, 高)，用于把归一化 bbox 换算成 PDF 点坐标；
        缺页时沿用 vlm.py 的 A4 默认值。sid_offset：跨页拼接时避免 sid 撞号。
        """
        from .model import Segment, Status
        from .vlm import blocks_to_segments

        if not self.blocks:
            if not self.text.strip():
                return []
            # 没有版面信息（只有纯文本）→ 整页当一段，保证下游不丢内容
            return [Segment(
                sid=f"{relpath}#{sid_offset}",
                source=self.text,
                status=Status.PENDING,
                meta={"page": page, "line_break": 0, "vlm": self.is_vlm,
                      "ocr_engine": self.engine},
            )]
        blocks = [{"text": b.text, "bbox": b.bbox, "page": page} for b in self.blocks]
        segs = blocks_to_segments(blocks, relpath, page_sizes or {})
        for i, seg in enumerate(segs):
            seg.meta["vlm"] = self.is_vlm
            if self.engine:
                seg.meta["ocr_engine"] = self.engine
            if sid_offset:
                seg.sid = f"{relpath}#{sid_offset + i}"
        return segs


@runtime_checkable
class OcrEngine(Protocol):
    """OCR 引擎协议（同步即可；通道也接受返回 awaitable 的实现）。"""

    name: str

    def available(self) -> bool:
        """依赖是否就绪；缺依赖/缺二进制时返回 False（不要抛异常）。"""
        ...

    def recognize(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        """识别一页图片（PNG/JPEG 字节），返回带 bbox 与置信度的结果。"""
        ...


# ---------------------------------------------------------------- 图像尺寸


def _png_size(data: bytes) -> tuple[int, int] | None:
    """从 PNG 的 IHDR 里读宽高（纯 stdlib，避免为了一张图引入 Pillow 依赖）。"""
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """从 JPEG 的 SOF 段里读宽高。"""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + seg_len
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """读图片宽高；PNG/JPEG 走文件头，Pillow 在场时兜底其他格式。"""
    size = _png_size(data) or _jpeg_size(data)
    if size:
        return size
    try:  # Pillow 是可选依赖（paddleocr 会带），没有就用不了 bbox
        from PIL import Image  # noqa: PLC0415
        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001 — 读不到尺寸不该让识别失败
        return None


# ---------------------------------------------------------------- Tesseract


def parse_tsv(tsv: str, width: int, height: int) -> OcrResult:
    """解析 tesseract 的 TSV 输出 → OcrResult。

    TSV 列：level page block par line word left top width height conf text
    - level=5 才是词行；conf=-1 表示非词行
    - 同一个 (block, par) 的词聚成一个块：行内空格连接，行间换行
    - 词级置信度求平均 → 块置信度；页置信度 = 全部词的均值
    """
    groups: dict[tuple[int, int], list[tuple]] = {}
    all_conf: list[float] = []
    for raw in tsv.splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < 12:
            continue
        try:
            level = int(parts[0])
            block_no = int(parts[2])
            par_no = int(parts[3])
            line_no = int(parts[4])
            word_no = int(parts[5])
            left = int(parts[6])
            top = int(parts[7])
            w = int(parts[8])
            h = int(parts[9])
            conf = float(parts[10])
        except ValueError:
            continue
        if level != 5 or conf < 0:
            continue
        text = parts[11].strip()
        if not text:
            continue
        groups.setdefault((block_no, par_no), []).append(
            (line_no, word_no, text, left, top, w, h, conf))
        all_conf.append(conf)

    blocks: list[OcrBlock] = []
    for words in groups.values():
        words.sort(key=lambda t: (t[0], t[1]))
        lines: list[str] = []
        cur_line, cur_text = None, []
        x0 = y0 = x1 = y1 = 0
        confs: list[float] = []
        for line_no, _word_no, text, left, top, w, h, conf in words:
            if cur_line is None:
                x0, y0 = left, top
                x1, y1 = left + w, top + h
            else:
                x0 = min(x0, left)
                y0 = min(y0, top)
                x1 = max(x1, left + w)
                y1 = max(y1, top + h)
            if line_no != cur_line:
                if cur_line is not None:
                    lines.append(" ".join(cur_text))
                cur_line, cur_text = line_no, [text]
            else:
                cur_text.append(text)
            confs.append(conf)
        if cur_line is not None:
            lines.append(" ".join(cur_text))
        body = "\n".join(lines)
        if not body.strip():
            continue
        bbox = None
        if width > 0 and height > 0:
            bbox = [
                max(0.0, min(1.0, x0 / width)),
                max(0.0, min(1.0, y0 / height)),
                max(0.0, min(1.0, x1 / width)),
                max(0.0, min(1.0, y1 / height)),
            ]
        blocks.append(OcrBlock(
            text=body,
            bbox=bbox,
            confidence=round(sum(confs) / len(confs) / 100.0, 4),
        ))

    page_conf = round(sum(all_conf) / len(all_conf) / 100.0, 4) if all_conf else 0.0
    return OcrResult(
        text="\n\n".join(b.text for b in blocks),
        blocks=blocks,
        confidence=page_conf,
        engine="tesseract",
    )


class TesseractEngine:
    """调用系统 tesseract：stdin 喂 PNG，stdout 拿 TSV。

    为什么用 TSV 而不是纯文本：TSV 带每个词的 bbox 与置信度，
    通道要靠置信度决定要不要升级到多模态模型。
    语言包（chi_sim 等）缺失时 tesseract 会直接报错，这里自动退化到 eng。
    """

    name = "tesseract"

    def __init__(self, *, binary: str = "tesseract", lang: str = "chi_sim+eng",
                 fallback_lang: str = "eng", timeout: float = 60.0,
                 psm: int | None = None) -> None:
        self.binary = binary
        self.lang = lang
        self.fallback_lang = fallback_lang
        self.timeout = timeout
        self.psm = psm
        self._available: bool | None = None
        self._lang_ok = True   # 语言包是否可用（chi_sim 缺失 → 退化成 eng）

    def available(self) -> bool:
        if self._available is None:
            self._available = shutil.which(self.binary) is not None
        return self._available

    def _cmd(self, lang: str, dpi: int) -> list[str]:
        cmd = [self.binary, "stdin", "stdout", "-l", lang, "--dpi", str(dpi)]
        if self.psm:
            cmd += ["--psm", str(self.psm)]
        cmd.append("tsv")   # 配置名放最后：输出 TSV 到 stdout
        return cmd

    def _run(self, image: bytes, lang: str, dpi: int) -> tuple[str, str, int]:
        proc = subprocess.run(
            self._cmd(lang, dpi), input=image,
            capture_output=True, timeout=self.timeout)
        return (proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"),
                proc.returncode)

    def recognize(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        if not self.available():
            raise RuntimeError("tesseract 不可用（未找到二进制）")
        lang = self.lang if self._lang_ok else self.fallback_lang
        out, err, code = self._run(image, lang, dpi)
        if code != 0 and "Failed loading language" in err and lang != self.fallback_lang:
            # 语言包缺失（多半是 chi_sim）：退化到 eng 再试一次，并记住结论
            self._lang_ok = False
            lang = self.fallback_lang
            out, err, code = self._run(image, lang, dpi)
        if code != 0:
            raise RuntimeError(f"tesseract 失败({code}): {err.strip()[:200]}")
        size = image_size(image) or (0, 0)
        return parse_tsv(out, size[0], size[1])


# ---------------------------------------------------------------- PaddleOCR


class PaddleEngine:
    """可选引擎：paddleocr（Python 包）。

    没装 paddleocr（或缺 numpy/Pillow）时 available() 返回 False，通道自动跳过它。
    paddleocr 2.x 与 3.x 的返回结构不同，这里两种都认。
    """

    name = "paddleocr"

    def __init__(self, *, lang: str = "ch", use_gpu: bool = False,
                 **kwargs: Any) -> None:
        self.lang = lang
        self.use_gpu = use_gpu
        self.kwargs = kwargs
        self._ocr: Any = None
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            import importlib.util
            self._available = importlib.util.find_spec("paddleocr") is not None
        return self._available

    def _get_ocr(self) -> Any:
        if self._ocr is None:
            from paddleocr import PaddleOCR  # noqa: PLC0415
            try:
                self._ocr = PaddleOCR(lang=self.lang, use_gpu=self.use_gpu, **self.kwargs)
            except TypeError:
                # 不同版本的构造参数不一样，退回最小参数集
                self._ocr = PaddleOCR(lang=self.lang)
        return self._ocr

    @staticmethod
    def _to_array(image: bytes):
        try:
            import numpy as np  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover — 依赖 paddleocr 时必然已装
            raise RuntimeError(f"paddleocr 需要 numpy 与 Pillow：{e}") from e
        with Image.open(io.BytesIO(image)) as im:
            return np.array(im.convert("RGB"))

    @staticmethod
    def _poly_bbox(poly: Any, width: int, height: int) -> list[float] | None:
        """把 paddleocr 的多边形/矩形位置归一化成 [x0,y0,x1,y1]。"""
        if poly is None:
            return None
        xs: list[float] = []
        ys: list[float] = []
        try:
            if isinstance(poly, (list, tuple)) and len(poly) == 4 and all(
                    isinstance(v, (int, float)) for v in poly):
                x, y, w, h = (float(v) for v in poly)
                xs, ys = [x, x + w], [y, y + h]
            else:
                for pt in poly:
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
        except (TypeError, ValueError, IndexError):
            return None
        if not xs or width <= 0 or height <= 0:
            return None
        return [
            max(0.0, min(1.0, min(xs) / width)),
            max(0.0, min(1.0, min(ys) / height)),
            max(0.0, min(1.0, max(xs) / width)),
            max(0.0, min(1.0, max(ys) / height)),
        ]

    def _iter_items(self, raw: Any):
        """把 paddleocr 各版本的输出摊平成 (bbox_px, text, conf)。"""
        if raw is None:
            return
        pages = raw if isinstance(raw, (list, tuple)) else [raw]
        for page in pages:
            if isinstance(page, dict):
                # 3.x：{"rec_texts": [...], "rec_scores": [...], "dt_polys"/"rec_polys": [...]}
                texts = page.get("rec_texts") or []
                scores = page.get("rec_scores") or []
                polys = page.get("rec_polys") or page.get("dt_polys") or []
                for i, t in enumerate(texts):
                    sc = float(scores[i]) if i < len(scores) else 0.0
                    poly = polys[i] if i < len(polys) else None
                    yield poly, str(t), sc
                continue
            if not isinstance(page, (list, tuple)):
                continue
            for item in page:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                poly, rec = item[0], item[1]
                if isinstance(rec, (list, tuple)) and rec:
                    text = str(rec[0])
                    conf = float(rec[1]) if len(rec) > 1 else 0.0
                else:
                    text, conf = str(rec), 0.0
                yield poly, text, conf

    def recognize(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        if not self.available():
            raise RuntimeError("paddleocr 未安装")
        ocr = self._get_ocr()
        arr = self._to_array(image)
        try:
            raw = ocr.ocr(arr, cls=True)      # 2.x
        except (AttributeError, TypeError):
            raw = ocr.predict(arr)             # 3.x
        size = image_size(image) or (0, 0)
        blocks: list[OcrBlock] = []
        for poly, text, conf in self._iter_items(raw):
            if not text.strip():
                continue
            blocks.append(OcrBlock(
                text=text.strip(),
                bbox=self._poly_bbox(poly, size[0], size[1]),
                confidence=round(max(0.0, min(1.0, conf)), 4),
            ))
        conf = round(sum(b.confidence for b in blocks) / len(blocks), 4) if blocks else 0.0
        return OcrResult(
            text="\n\n".join(b.text for b in blocks),
            blocks=blocks,
            confidence=conf,
            engine=self.name,
        )


class NullEngine:
    """永远不可用：显式关闭 OCR 通道时用它（也让 engines=[] 有明确语义）。"""

    name = "null"

    def available(self) -> bool:
        return False

    def recognize(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        raise RuntimeError("NullEngine 不可用")


def default_engines(lang: str = "chi_sim+eng") -> list[OcrEngine]:
    """默认本地引擎组合（Tesseract 优先：轻量、无需下载模型；Paddle 作为补充）。"""
    return [TesseractEngine(lang=lang), PaddleEngine()]


# ---------------------------------------------------------------- 通道


class OcrChannel:
    """单页识别通道：本地引擎 → 置信度不够 → 升级到多模态模型 → 失败则退回本地结果。

    判定顺序：
    1. prefer_vlm=True：直接走视觉模型（失败则退回本地引擎）
    2. 否则依次尝试可用引擎，取 confidence 最高的非空结果
    3. 无可用引擎 / 置信度 < min_confidence / 结果为空 → 升级到视觉模型
    4. 升级失败 → 退回本地结果，engine 标成 "<引擎>(fallback)"（宁可粗糙也不要空白）
    """

    def __init__(self, engines: list[OcrEngine] | None = None, *, vision: Any = None,
                 min_confidence: float = 0.55, prefer_vlm: bool = False) -> None:
        self.engines: list[OcrEngine] = list(engines) if engines is not None else default_engines()
        self.vision = vision
        self.min_confidence = float(min_confidence)
        self.prefer_vlm = bool(prefer_vlm)

    # ---- 对外 ----

    async def recognize_page(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        """识别一页图片（PNG/JPEG 字节），返回 OcrResult（engine 标明实际来源）。"""
        if not image:
            return OcrResult(engine="none")

        if self.prefer_vlm:
            vlm_res = await self._try_vision(image)
            if vlm_res is not None:
                return vlm_res
            ocr_res = await self._best_ocr(image, dpi=dpi)
            if ocr_res is not None:
                ocr_res.engine = f"{ocr_res.engine}(fallback)"
                return ocr_res
            return OcrResult(engine="none")

        ocr_res = await self._best_ocr(image, dpi=dpi)
        need_vlm = (ocr_res is None
                    or not ocr_res.text.strip()
                    or ocr_res.confidence < self.min_confidence)
        if need_vlm:
            vlm_res = await self._try_vision(image)
            if vlm_res is not None:
                return vlm_res
            if ocr_res is not None:
                # 升级失败 → 退回本地结果（宁可粗糙也不要空白）
                ocr_res.engine = f"{ocr_res.engine}(fallback)"
                return ocr_res
            return OcrResult(engine="none")
        return ocr_res

    def available_engines(self) -> list[str]:
        """当前可用的本地引擎名（用于提示/诊断）。"""
        out = []
        for e in self.engines:
            try:
                if e.available():
                    out.append(e.name)
            except Exception:  # noqa: BLE001 — 引擎探测不该影响主流程
                continue
        return out

    # ---- 内部 ----

    async def _best_ocr(self, image: bytes, *, dpi: int) -> OcrResult | None:
        """依次尝试可用引擎，取置信度最高的非空结果（都没有文本时退而取置信度最高的）。"""
        results: list[OcrResult] = []
        for engine in self.engines:
            try:
                if not engine.available():
                    continue
            except Exception:  # noqa: BLE001
                continue
            res = await self._call_engine(engine, image, dpi)
            if res is None:
                continue
            if not res.engine:
                res.engine = getattr(engine, "name", "") or "ocr"
            results.append(res)
        if not results:
            return None
        usable = [r for r in results if r.text.strip()]
        pool = usable or results
        return max(pool, key=lambda r: r.confidence)

    @staticmethod
    async def _call_engine(engine: OcrEngine, image: bytes, dpi: int) -> OcrResult | None:
        """调用引擎（同步实现丢到线程里跑，别堵住事件循环；失败返回 None）。"""
        try:
            out = await asyncio.to_thread(engine.recognize, image, dpi=dpi)
            if inspect.isawaitable(out):
                out = await out
        except Exception:  # noqa: BLE001 — 单个引擎失败不该拖垮整页
            return None
        return out

    async def _try_vision(self, image: bytes) -> OcrResult | None:
        """调用多模态模型解析这一页；没有模型/调用失败/结果为空都返回 None。"""
        if self.vision is None:
            return None
        fn = getattr(self.vision, "complete_vision", None)
        if fn is None:
            return None
        try:
            res = await fn(VLM_SYSTEM, VLM_USER, [image])
        except Exception:  # noqa: BLE001 — 升级失败由调用方回退
            return None
        text = res if isinstance(res, str) else (getattr(res, "text", "") or "")
        blocks = parse_blocks(text)
        if not blocks:
            return None
        out_blocks = [OcrBlock(text=b["text"], bbox=b["bbox"], confidence=VLM_CONFIDENCE)
                      for b in blocks]
        return OcrResult(
            text="\n\n".join(b.text for b in out_blocks),
            blocks=out_blocks,
            confidence=VLM_CONFIDENCE,
            engine="vlm",
        )


def default_channel(profile: Any = None, *, provider: Any = None, vision: Any = None,
                    engines: list[OcrEngine] | None = None,
                    min_confidence: float = 0.55, prefer_vlm: bool | None = None,
                    lang: str = "chi_sim+eng") -> OcrChannel:
    """按档案类型组装通道。

    - profile.kind == "ocr"：OCR 专用档案 → 直接走模型（prefer_vlm=True），本地引擎只作兜底
    - 其余（vision / chat / local）：本地引擎先跑，置信度不够再升级到模型
    - 升级通道优先用显式传入的 vision，其次 provider（实现 complete_vision 的对象）
    - 本模块不发网络请求：provider 由调用方注入
    """
    if engines is None:
        engines = default_engines(lang=lang)
    vis = vision if vision is not None else provider
    kind = str(getattr(profile, "kind", "") or "").lower()
    if prefer_vlm is None:
        prefer_vlm = kind == "ocr"
    return OcrChannel(engines, vision=vis, min_confidence=min_confidence,
                      prefer_vlm=prefer_vlm)


__all__ = ["OcrBlock", "OcrResult", "OcrEngine", "OcrChannel",
           "TesseractEngine", "PaddleEngine", "NullEngine",
           "default_engines", "default_channel", "parse_tsv", "image_size",
           "VLM_CONFIDENCE"]

if TYPE_CHECKING:  # 只为类型注解，运行时不导入（避免循环依赖）
    from .model import Segment
