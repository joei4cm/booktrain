"""OpenAI 兼容 provider（chat/completions，非流式）。覆盖 DeepSeek/各类网关。"""
from __future__ import annotations

import httpx

from ..config import LLMConfig
from .base import LLMResult
from .ratelimit import LLMError, RateLimiter, request_with_retry

DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, cfg: LLMConfig, limiter: RateLimiter | None = None) -> None:
        self.model = cfg.model
        self.cfg = cfg
        base = (cfg.base_url or DEFAULT_BASE).rstrip("/")
        if not base.endswith("/v1") and "/v1" not in base:
            base += "/v1"
        self.url = f"{base}/chat/completions"
        self.limiter = limiter or RateLimiter(cfg.rpm, cfg.concurrency)
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {},
            timeout=cfg.timeout,
            limits=httpx.Limits(max_connections=max(8, cfg.concurrency * 2)),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def complete(self, system: str, user: str) -> LLMResult:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.cfg.max_output_tokens:
            body["max_tokens"] = self.cfg.max_output_tokens
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature

        async def _do() -> LLMResult:
            resp = await self.client.post(self.url, json=body)
            if resp.status_code != 200:
                raise LLMError(
                    f"HTTP {resp.status_code}: {resp.text[:500]}", resp.status_code
                )
            data = resp.json()
            choices = data.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content") or ""
            # 某些模型把推理内容混在 content 里
            if "</think>" in text:
                text = text.split("</think>", 1)[1]
            usage = data.get("usage") or {}
            return LLMResult(
                text=text.strip(),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )

        await self.limiter.acquire()
        try:
            return await request_with_retry(_do, max_retries=self.cfg.max_retries)
        finally:
            self.limiter.release()

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        """图文补全：image_url 用 data URI（多数兼容网关支持）。"""
        import base64

        content: list[dict] = []
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        content.append({"type": "text", "text": user})

        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if self.cfg.max_output_tokens:
            body["max_tokens"] = self.cfg.max_output_tokens
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature

        async def _do() -> LLMResult:
            resp = await self.client.post(self.url, json=body)
            if resp.status_code != 200:
                raise LLMError(
                    f"HTTP {resp.status_code}: {resp.text[:500]}", resp.status_code
                )
            data = resp.json()
            choices = data.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content") or ""
            if "</think>" in text:
                text = text.split("</think>", 1)[1]
            usage = data.get("usage") or {}
            return LLMResult(
                text=text.strip(),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )

        await self.limiter.acquire()
        try:
            return await request_with_retry(_do, max_retries=self.cfg.max_retries)
        finally:
            self.limiter.release()
