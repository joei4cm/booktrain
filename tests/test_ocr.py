"""独立 OCR 通道：引擎选择、置信度升级/回退、Segment 转换。

全部用假引擎/假视觉模型，**不依赖真实 OCR 引擎**（tesseract/paddleocr 装没装都能跑）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from booktran.config.models import Profile
from booktran.llm.base import LLMResult
from booktran.model import Status
from booktran.ocr import (
    NullEngine,
    OcrBlock,
    OcrChannel,
    OcrResult,
    PaddleEngine,
    TesseractEngine,
    default_channel,
    default_engines,
    parse_tsv,
)

# ---------------------------------------------------------------- 假实现


class FakeEngine:
    """假本地引擎：可控制可用性、置信度、是否抛异常。"""

    def __init__(self, name: str = "fake", *, confidence: float = 0.9,
                 text: str = "本地引擎识别出的文本", available: bool = True,
                 error: Exception | None = None) -> None:
        self.name = name
        self.confidence = confidence
        self.text = text
        self._available = available
        self.error = error
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def recognize(self, image: bytes, *, dpi: int = 150) -> OcrResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return OcrResult(
            text=self.text,
            blocks=[OcrBlock(text=self.text, bbox=[0.1, 0.2, 0.9, 0.3],
                             confidence=self.confidence)],
            confidence=self.confidence,
            engine=self.name,
        )


class FakeVision:
    """假视觉模型：返回标准 VLM JSON（blocks/bbox/text）。"""

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else {
            "blocks": [{"bbox": [0.05, 0.05, 0.95, 0.15], "text": "模型识别出的文本"}]}
        self.error = error
        self.calls = 0
        self.images: list[bytes] = []

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        self.calls += 1
        self.images = list(images)
        if self.error is not None:
            raise self.error
        text = json.dumps(self.payload, ensure_ascii=False)
        return LLMResult(text=text, prompt_tokens=10, completion_tokens=10)


# ---------------------------------------------------------------- 通道判定


def test_high_confidence_keeps_ocr_result():
    """置信度够高 → 不升级到视觉模型。"""
    engine = FakeEngine(confidence=0.92)
    vision = FakeVision()
    channel = OcrChannel([engine], vision=vision, min_confidence=0.55)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "fake"
    assert res.confidence == pytest.approx(0.92)
    assert res.text == "本地引擎识别出的文本"
    assert vision.calls == 0
    assert engine.calls == 1


def test_low_confidence_escalates_to_vision():
    """置信度不够 → 升级到视觉模型。"""
    engine = FakeEngine(confidence=0.2)
    vision = FakeVision()
    channel = OcrChannel([engine], vision=vision, min_confidence=0.55)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert vision.calls == 1
    assert res.engine == "vlm"
    assert res.text == "模型识别出的文本"
    assert res.blocks[0].bbox == [0.05, 0.05, 0.95, 0.15]


def test_no_engine_goes_straight_to_vision():
    """没有可用引擎 → 直接走视觉模型。"""
    vision = FakeVision()
    channel = OcrChannel([FakeEngine(available=False)], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert vision.calls == 1
    assert res.engine == "vlm"


def test_empty_engines_goes_straight_to_vision():
    """engines=[]（显式关闭本地 OCR）→ 直接走视觉模型。"""
    vision = FakeVision()
    channel = OcrChannel([], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert vision.calls == 1
    assert res.engine == "vlm"


def test_vision_failure_falls_back_to_ocr():
    """升级失败 → 退回本地结果，engine 标成 <引擎>(fallback)。"""
    engine = FakeEngine(confidence=0.3)
    vision = FakeVision(error=RuntimeError("模型挂了"))
    channel = OcrChannel([engine], vision=vision, min_confidence=0.55)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert vision.calls == 1
    assert res.engine == "fake(fallback)"
    assert res.text == "本地引擎识别出的文本"
    assert res.confidence == pytest.approx(0.3)


def test_no_engine_and_no_vision_returns_empty():
    """既无引擎又无模型 → 空结果，不抛异常。"""
    channel = OcrChannel([FakeEngine(available=False)], vision=None)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.text == ""
    assert res.blocks == []
    assert res.engine == "none"


def test_prefer_vlm_goes_directly_to_vision():
    """prefer_vlm=True → 不跑本地引擎，直接走视觉模型。"""
    engine = FakeEngine(confidence=0.99)
    vision = FakeVision()
    channel = OcrChannel([engine], vision=vision, prefer_vlm=True)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert engine.calls == 0
    assert vision.calls == 1
    assert res.engine == "vlm"


def test_prefer_vlm_failure_falls_back_to_engine():
    """prefer_vlm=True 但模型挂了 → 退回本地引擎。"""
    engine = FakeEngine(confidence=0.99)
    vision = FakeVision(error=RuntimeError("模型挂了"))
    channel = OcrChannel([engine], vision=vision, prefer_vlm=True)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "fake(fallback)"
    assert res.text == "本地引擎识别出的文本"


def test_prefer_vlm_without_vision_and_engine():
    """prefer_vlm=True 但既无模型也无引擎 → 空结果，不抛异常。"""
    channel = OcrChannel([], vision=None, prefer_vlm=True)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "none"
    assert res.text == ""


def test_best_engine_wins():
    """多个引擎 → 取置信度最高的那个。"""
    low = FakeEngine("low", confidence=0.4)
    high = FakeEngine("high", confidence=0.88)
    vision = FakeVision()
    channel = OcrChannel([low, high], vision=vision, min_confidence=0.55)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "high"
    assert vision.calls == 0
    assert low.calls == 1 and high.calls == 1


def test_broken_engine_is_skipped():
    """某个引擎抛异常 → 跳过它，继续用别的引擎。"""
    broken = FakeEngine("broken", error=RuntimeError("炸了"))
    good = FakeEngine("good", confidence=0.8)
    vision = FakeVision()
    channel = OcrChannel([broken, good], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "good"
    assert vision.calls == 0


def test_empty_text_result_escalates():
    """引擎返回空文本 → 升级到视觉模型。"""
    engine = FakeEngine(confidence=0.99, text="")
    vision = FakeVision()
    channel = OcrChannel([engine], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert vision.calls == 1
    assert res.engine == "vlm"


def test_vision_result_parses_vlm_json_bbox():
    """假视觉模型返回标准 VLM JSON → bbox 被正确解析。"""
    vision = FakeVision(payload={
        "blocks": [{"bbox": [0.1, 0.2, 0.9, 0.3], "text": "第一段"},
                   {"bbox": [0.1, 0.4, 0.9, 0.5], "text": "第二段"}]})
    channel = OcrChannel([], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "vlm"
    assert len(res.blocks) == 2
    assert res.blocks[0].bbox == [0.1, 0.2, 0.9, 0.3]
    assert res.blocks[1].text == "第二段"
    assert res.text == "第一段\n\n第二段"
    # 图片原样传给模型
    assert vision.images == [b"fake-png"]


def test_vision_json_in_code_fence_is_tolerated():
    """模型把 JSON 包在代码块里也要能解析（复用 vlm.parse_blocks 的容错）。"""
    payload = {"blocks": [{"bbox": [0.0, 0.0, 1.0, 0.1], "text": "包在代码块里"}]}
    vision = FakeVision()
    vision.payload = payload

    class _FencedVision(FakeVision):
        async def complete_vision(self, system, user, images):
            self.calls += 1
            text = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
            return LLMResult(text=text, prompt_tokens=1, completion_tokens=1)

    channel = OcrChannel([], vision=_FencedVision())
    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.blocks[0].text == "包在代码块里"
    assert res.blocks[0].bbox == [0.0, 0.0, 1.0, 0.1]


def test_vision_without_text_falls_back_to_ocr():
    """模型返回空 blocks → 退回本地结果。"""
    engine = FakeEngine(confidence=0.4)
    vision = FakeVision(payload={"blocks": []})
    channel = OcrChannel([engine], vision=vision)

    res = asyncio.run(channel.recognize_page(b"fake-png"))

    assert res.engine == "fake(fallback)"


# ---------------------------------------------------------------- Segment 转换


def test_to_segments_fields():
    """to_segments：sid/status/meta（page/bbox/vlm）与 vlm.py 约定一致。"""
    result = OcrResult(
        text="第一段\n\n第二段",
        blocks=[
            OcrBlock(text="第一段", bbox=[0.1, 0.2, 0.9, 0.3], confidence=0.9),
            OcrBlock(text="第二段", bbox=[0.1, 0.4, 0.9, 0.5], confidence=0.8),
        ],
        confidence=0.85,
        engine="vlm",
    )

    segs = result.to_segments("book.pdf", page=3)

    assert len(segs) == 2
    assert segs[0].sid == "book.pdf#0"
    assert segs[1].sid == "book.pdf#1"
    assert segs[0].source == "第一段"
    assert segs[0].status == Status.PENDING
    assert segs[0].meta["page"] == 3
    assert segs[0].meta["line_break"] == 0
    assert segs[0].meta["vlm"] is True
    assert segs[0].meta["ocr_engine"] == "vlm"
    # 归一化 bbox → PDF 点坐标（左下原点），与 vlm.blocks_to_segments 一致
    assert segs[0].meta["bbox"] == pytest.approx([59.5, 589.4, 535.5, 673.6])


def test_to_segments_page_sizes_and_offset():
    """指定页尺寸与 sid 偏移。"""
    result = OcrResult(
        text="x",
        blocks=[OcrBlock(text="x", bbox=[0.1, 0.2, 0.9, 0.3])],
        confidence=0.5,
        engine="tesseract",
    )

    segs = result.to_segments("book.pdf", page=1, page_sizes={1: (100.0, 200.0)},
                              sid_offset=7)

    assert segs[0].sid == "book.pdf#7"
    assert segs[0].meta["bbox"] == pytest.approx([10.0, 140.0, 90.0, 160.0])
    # 本地 OCR 不是模型 → vlm 标记为 False
    assert segs[0].meta["vlm"] is False
    assert segs[0].meta["ocr_engine"] == "tesseract"


def test_to_segments_trivial_line_excluded():
    """纯数字行按 vlm.py 的约定标 EXCLUDED。"""
    result = OcrResult(text="123", blocks=[OcrBlock(text="123")],
                       confidence=0.5, engine="tesseract")

    segs = result.to_segments("book.pdf", page=1)

    assert segs[0].status == Status.EXCLUDED


def test_to_segments_text_only():
    """只有纯文本没有 blocks → 整页当一段，meta 仍带 page/vlm。"""
    result = OcrResult(text="整页文字", confidence=0.5, engine="tesseract")

    segs = result.to_segments("book.pdf", page=2)

    assert len(segs) == 1
    assert segs[0].source == "整页文字"
    assert segs[0].meta["page"] == 2
    assert segs[0].meta["vlm"] is False


def test_to_segments_empty():
    """空结果 → 没有段。"""
    assert OcrResult().to_segments("book.pdf", page=1) == []


# ---------------------------------------------------------------- 引擎可用性


def test_tesseract_unavailable_without_binary(monkeypatch):
    """没有 tesseract 二进制 → available() 为 False，且不抛异常。"""
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    engine = TesseractEngine()
    assert engine.available() is False


def test_tesseract_available_does_not_raise():
    """真实环境里 available() 也不抛异常（有没有 tesseract 都一样）。"""
    engine = TesseractEngine()
    assert isinstance(engine.available(), bool)


def test_tesseract_recognize_without_binary_raises(monkeypatch):
    """没有二进制时调用 recognize 明确报错（由通道捕获后降级）。"""
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        TesseractEngine().recognize(b"not-a-real-png")


def test_null_engine_never_available():
    """NullEngine 永远不可用。"""
    engine = NullEngine()
    assert engine.available() is False
    with pytest.raises(RuntimeError):
        engine.recognize(b"x")


def test_paddle_unavailable_when_not_installed():
    """没装 paddleocr → available() 为 False，不抛异常。"""
    engine = PaddleEngine()
    assert isinstance(engine.available(), bool)
    if not engine.available():
        with pytest.raises(RuntimeError):
            engine.recognize(b"x")


def test_default_engines_contains_local_engines():
    """默认引擎组合：Tesseract + Paddle。"""
    names = [e.name for e in default_engines()]
    assert names == ["tesseract", "paddleocr"]


# ---------------------------------------------------------------- TSV 解析


TSV_SAMPLE = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t",
    "5\t1\t1\t1\t1\t1\t100\t200\t120\t40\t92.5\tHello",
    "5\t1\t1\t1\t1\t2\t240\t200\t80\t40\t87.5\tWorld",
    "5\t1\t1\t1\t2\t1\t100\t260\t200\t40\t60.0\t第二行",
])


def test_parse_tsv_groups_words_into_block():
    """词级置信度取平均、bbox 归一化（左上原点）。"""
    res = parse_tsv(TSV_SAMPLE, width=1000, height=2000)

    assert len(res.blocks) == 1
    block = res.blocks[0]
    assert block.text == "Hello World\n第二行"
    assert block.bbox == pytest.approx([0.1, 0.1, 0.32, 0.15])
    assert block.confidence == pytest.approx((92.5 + 87.5 + 60.0) / 3 / 100, abs=1e-4)
    assert res.confidence == pytest.approx((92.5 + 87.5 + 60.0) / 3 / 100, abs=1e-4)
    assert res.engine == "tesseract"


def test_parse_tsv_ignores_garbage():
    """非词行/坏行被忽略。"""
    res = parse_tsv("garbage\nlevel\tpage_num\n", width=100, height=100)
    assert res.blocks == []
    assert res.text == ""


# ---------------------------------------------------------------- 工厂


def test_default_channel_ocr_profile_prefers_model():
    """kind == 'ocr' 的档案 → 直接走模型（本地引擎只兜底）。"""
    vision = FakeVision()
    channel = default_channel(Profile(id="p", kind="ocr"), vision=vision)

    assert channel.prefer_vlm is True
    assert channel.vision is vision
    assert [e.name for e in channel.engines] == ["tesseract", "paddleocr"]


def test_default_channel_vision_profile_prefers_local():
    """kind == 'vision' 的档案 → 本地引擎先跑，不够再升级。"""
    channel = default_channel(Profile(id="p", kind="vision"), vision=FakeVision())
    assert channel.prefer_vlm is False


def test_default_channel_uses_provider_as_vision():
    """没显式给 vision 时用 provider 顶上。"""
    provider = FakeVision()
    channel = default_channel(Profile(id="p", kind="vision"), provider=provider)
    assert channel.vision is provider


def test_default_channel_explicit_engines():
    """显式传入 engines → 原样使用。"""
    engine = FakeEngine()
    channel = default_channel(Profile(id="p", kind="vision"), engines=[engine])
    assert channel.engines == [engine]
