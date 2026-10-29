"""Anthropic Messages API provider（httpx 直连，非流式）。"""
from __future__ import annotations

import httpx

from ..config import LLMConfig
from .base import LLMResult
from .ratelimit import LLMError, RateLimiter, request_with_retry

DEFAULT_BASE = "https://api.anthropic.com"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, limiter: RateLimiter | None = None) -> None:
        self.model = cfg.model
        self.cfg = cfg
        base = (cfg.base_url or DEFAULT_BASE).rstrip("/")
        self.url = f"{base}/v1/messages"
        self.limiter = limiter or RateLimiter(cfg.rpm, cfg.concurrency)
        headers = {"anthropic-version": "2023-06-01"}
        if cfg.api_key:
            # 官方 key 用 x-api-key；网关 token（ANTHROPIC_AUTH_TOKEN）用 Bearer
            if cfg.api_key.startswith("sk-ant-"):
                headers["x-api-key"] = cfg.api_key
            else:
                headers["Authorization"] = f"Bearer {cfg.api_key}"
        self.client = httpx.AsyncClient(
            headers=headers, timeout=cfg.timeout,
            limits=httpx.Limits(max_connections=max(8, cfg.concurrency * 2)),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def complete(self, system: str, user: str) -> LLMResult:
        body: dict = {
            "model": self.model,
            "max_tokens": self.cfg.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature

        async def _do() -> LLMResult:
            resp = await self.client.post(self.url, json=body)
            if resp.status_code != 200:
                raise LLMError(
                    f"HTTP {resp.status_code}: {resp.text[:500]}", resp.status_code
                )
            data = resp.json()
            if data.get("type") == "error":
                err = data.get("error", {})
                raise LLMError(f"API error: {err.get('type')}: {err.get('message')}")
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResult(
                text=text,
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            )

        await self.limiter.acquire()
        try:
            return await request_with_retry(_do, max_retries=self.cfg.max_retries)
        finally:
            self.limiter.release()

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        """图文补全：图片以 base64 放在 user content 的 image 块里。"""
        import base64

        content: list[dict] = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": user})

        body: dict = {
            "model": self.model,
            "max_tokens": self.cfg.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature

        async def _do() -> LLMResult:
            resp = await self.client.post(self.url, json=body)
            if resp.status_code != 200:
                raise LLMError(
                    f"HTTP {resp.status_code}: {resp.text[:500]}", resp.status_code
                )
            data = resp.json()
            if data.get("type") == "error":
                err = data.get("error", {})
                raise LLMError(f"API error: {err.get('type')}: {err.get('message')}")
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResult(
                text=text,
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
            )

        await self.limiter.acquire()
        try:
            return await request_with_retry(_do, max_retries=self.cfg.max_retries)
        finally:
            self.limiter.release()
