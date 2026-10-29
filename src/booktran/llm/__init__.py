from .base import FakeProvider, KeyPool, LLMResult, Provider
from .ratelimit import LLMError, RateLimiter
from .router import Backend, ModelRouter, RouteContext
from .usage import Usage, UsageLog, price_of


def _build_one(cfg, limiter: RateLimiter):
    """按 cfg 构造单个 provider（cfg.api_keys 时由调用方拆分为 key 池）。"""
    name = (cfg.provider or "").lower()
    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(cfg, limiter)
    if name in ("openai", "openai-compat", "deepseek", "custom", "local", "vllm", "ollama"):
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(cfg, limiter)
    raise SystemExit(f"未知 provider: {name}（支持 anthropic | openai | fake）")


def make_provider(cfg):
    """按配置构造 provider。

    cfg 可以是 LLMConfig 或 AppConfig（后者自动取 .llm）。

    - provider=fake → FakeProvider（零成本演示/测试）
    - api_keys 非空 → KeyPool 轮询（共享限流器）
    - 其余 → 单个 provider
    """
    cfg = getattr(cfg, "llm", cfg)
    if (cfg.provider or "").lower() == "fake":
        return FakeProvider()
    limiter = RateLimiter(cfg.rpm, cfg.concurrency)
    keys = list(cfg.api_keys or [])
    if cfg.api_key and cfg.api_key not in keys:
        keys.append(cfg.api_key)
    if len(keys) > 1:
        import dataclasses
        providers = []
        for key in keys:
            sub = dataclasses.replace(cfg, api_key=key, api_keys=[])
            providers.append(_build_one(sub, limiter))
        return KeyPool(providers)
    return _build_one(cfg, limiter)


__all__ = ["Provider", "LLMResult", "FakeProvider", "KeyPool", "LLMError",
           "RateLimiter", "make_provider", "ModelRouter", "Backend",
           "RouteContext", "Usage", "UsageLog", "price_of"]
