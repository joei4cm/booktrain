"""Provider 协议与公共类型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


class Provider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str) -> LLMResult:
        """单次补全。实现方负责限流与重试。"""
        ...

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        """图文补全（扫描页 OCR / 版面理解）。images 为 PNG/JPEG 字节。"""
        ...


class KeyPool:
    """key 池：多个同构 provider 轮询，共享限流器（全局 RPM）。"""

    def __init__(self, providers: list) -> None:
        self.providers = providers
        self._i = 0

    @property
    def name(self) -> str:
        return self.providers[0].name if self.providers else "pool"

    @property
    def model(self) -> str:
        return self.providers[0].model if self.providers else ""

    async def complete(self, system: str, user: str) -> LLMResult:
        provider = self.providers[self._i % len(self.providers)]
        self._i += 1
        return await provider.complete(system, user)

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        provider = self.providers[self._i % len(self.providers)]
        self._i += 1
        fn = getattr(provider, "complete_vision", None)
        if fn is None:
            from .ratelimit import LLMError
            raise LLMError(f"provider {provider.name} 不支持图文请求")
        return await fn(system, user, images)

    async def aclose(self) -> None:
        for p in self.providers:
            if hasattr(p, "aclose"):
                await p.aclose()


class FakeProvider:
    """零成本演示/测试：从 user 内容里解析编号原文，回填【译】前缀译文。"""

    name = "fake"
    model = "fake"

    def __init__(self, fail_first: int = 0) -> None:
        import re
        self._line = re.compile(r"^(\d+)\.(.*)$", re.MULTILINE)
        self.calls = 0
        self._fail_first = fail_first
        self.vision_calls = 0

    async def complete(self, system: str, user: str) -> LLMResult:
        self.calls += 1
        if self.calls <= self._fail_first:
            from .ratelimit import LLMError
            raise LLMError("模拟失败", status_code=429)
        import json
        items = [{"i": int(m.group(1)), "t": f"【译】{m.group(2).strip()}"}
                 for m in self._line.finditer(user)]
        text = json.dumps({"translations": items}, ensure_ascii=False)
        return LLMResult(
            text=text,
            prompt_tokens=(len(system) + len(user)) // 3,
            completion_tokens=len(text) // 3,
        )

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        """假图文：返回占位文本块，用于离线跑通 VLM 流程。"""
        self.vision_calls += 1
        import json

        from .ratelimit import LLMError
        if self.vision_calls <= self._fail_first:
            raise LLMError("模拟失败", status_code=429)
        payload = {"blocks": [{"bbox": [0.05, 0.05, 0.95, 0.15],
                               "text": f"【扫描页】第{self.vision_calls}页文本"}]}
        text = json.dumps(payload, ensure_ascii=False)
        return LLMResult(text=text, prompt_tokens=(len(system) + len(user)) // 3,
                         completion_tokens=len(text) // 3)
