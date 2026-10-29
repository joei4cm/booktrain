"""模型路由：角色 → 档案，支持级联规则与失败降级。

这是"配置一个或多个大模型"的落地处：
同一个作业里，翻译用 A、识别用 B、OCR 用 C、校对用 D，由角色表决定，
项目只写"哪个角色用哪个档案"，不持有模型配置本身。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config.models import AppConfig, Profile
from .base import LLMResult
from .usage import Usage, UsageLog, price_of


@dataclass
class RouteContext:
    """级联规则的匹配上下文。"""

    chars: int = 0
    lang: str = ""
    source_lang: str = ""
    glossary_density: float = 0.0
    stage: str = ""


@dataclass
class Backend:
    """档案 + 底层 provider 的组合（能力与价格挂在档案上）。"""

    profile: Profile
    provider: Any

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def model(self) -> str:
        return self.profile.model or getattr(self.provider, "model", "")

    @property
    def caps(self) -> set[str]:
        return self.profile.caps

    async def complete(self, system: str, user: str) -> LLMResult:
        return await self.provider.complete(system, user)

    async def complete_vision(self, system: str, user: str, images: list[bytes]) -> LLMResult:
        fn = getattr(self.provider, "complete_vision", None)
        if fn is None:
            raise RuntimeError(f"档案 {self.profile.id} 不支持图文请求")
        return await fn(system, user, images)

    async def aclose(self) -> None:
        if hasattr(self.provider, "aclose"):
            await self.provider.aclose()


def _rule_matches(rule_match: dict, ctx: RouteContext, candidate: Profile) -> bool:
    m = rule_match or {}
    if "chars_gt" in m and not ctx.chars > int(m["chars_gt"]):
        return False
    if "chars_lt" in m and not ctx.chars < int(m["chars_lt"]):
        return False
    if "target_lang" in m and ctx.lang != str(m["target_lang"]):
        return False
    if "source_lang" in m and ctx.source_lang != str(m["source_lang"]):
        return False
    if "glossary_density_gt" in m and not ctx.glossary_density > float(m["glossary_density_gt"]):
        return False
    if "glossary_density_lt" in m and not ctx.glossary_density < float(m["glossary_density_lt"]):
        return False
    if "kind" in m and str(m["kind"]).lower() != candidate.kind.lower():
        return False
    return True


class ModelRouter:
    """按角色选模型，支持级联与降级，并统一记账。"""

    def __init__(self, cfg: AppConfig, *, usage_log: UsageLog | None = None,
                 factory: Callable[[Profile], Any] | None = None) -> None:
        self.cfg = cfg
        self.usage_log = usage_log
        self._factory = factory
        self._backends: dict[str, Backend] = {}

    # ---------------- 选模 ----------------

    def pick(self, role: str, ctx: RouteContext | None = None) -> Profile:
        """角色 → 档案（先看级联规则，再看默认，最后回落 active_platform）。"""
        ctx = ctx or RouteContext()
        spec = self.cfg.role(role)
        # 1) 级联规则
        for rule in spec.rules:
            for pid in ([rule.use] if rule.use else []):
                cand = self.cfg.profiles.get(pid)
                if cand is None:
                    continue
                if _rule_matches(rule.match, ctx, cand):
                    return cand
        # 2) 角色直接指定 / 级联默认
        for pid in (spec.profile, spec.default):
            if pid and pid in self.cfg.profiles:
                return self.cfg.profiles[pid]
        # 3) 兼容：旧的 routing / active_platform
        pid = self.cfg.routing.get(role) or self.cfg.active_platform
        if pid and pid in self.cfg.profiles:
            return self.cfg.profiles[pid]
        if pid:
            return Profile.from_dict(pid, self.cfg.platforms.get(pid, {}))
        # 4) 兜底：任意一个档案
        if self.cfg.profiles:
            return next(iter(self.cfg.profiles.values()))
        raise SystemExit(f"没有可用的模型档案（角色 '{role}'）："
                         "请在 ~/.config/booktran/profiles.toml 中定义 [profile.*]")

    def profile_for(self, role: str) -> Profile:
        return self.pick(role, RouteContext())

    # ---------------- 后端 ----------------

    def _make_backend(self, profile: Profile) -> Backend:
        if self._factory is not None:
            return Backend(profile=profile, provider=self._factory(profile))
        from ..config.models import AppConfig, LLMConfig
        from . import make_provider
        llm = LLMConfig(
            provider=profile.provider,
            model=profile.model,
            base_url=profile.base_url,
            api_key=profile.api_key,
            api_keys=list(profile.api_keys),
            api_key_env=profile.key_env,
            concurrency=profile.concurrency,
            rpm=profile.rpm,
            max_output_tokens=profile.max_output_tokens,
            temperature=profile.temperature,
            timeout=profile.timeout,
            max_retries=profile.max_retries,
            prompt_preset=profile.prompt_preset,
        )
        # 传 AppConfig（与 make_provider 的既有入参形状一致：cfg.llm.*）
        app_cfg = AppConfig(llm=llm, active_platform=profile.id)
        return Backend(profile=profile, provider=make_provider(app_cfg))

    def backend(self, profile: Profile) -> Backend:
        if profile.id not in self._backends:
            self._backends[profile.id] = self._make_backend(profile)
        return self._backends[profile.id]

    # ---------------- 调用 ----------------

    async def run(self, role: str, system: str, user: str, *,
                  images: list[bytes] | None = None,
                  ctx: RouteContext | None = None,
                  stage: str = "", lang: str = "",
                  retries: int = 0) -> tuple[LLMResult, Profile]:
        """按角色调用模型；失败时按 fallback 降级重试一次。返回 (结果, 实际使用的档案)。"""
        profile = self.pick(role, ctx)
        try:
            res = await self._call(profile, system, user, images)
        except Exception:
            spec = self.cfg.role(role)
            fb = spec.fallback
            if not fb or fb not in self.cfg.profiles:
                raise
            profile = self.cfg.profiles[fb]
            res = await self._call(profile, system, user, images)
            retries += 1
        self._record(profile, res, role=role, stage=stage, lang=lang, retries=retries)
        return res, profile

    async def _call(self, profile: Profile, system: str, user: str,
                    images: list[bytes] | None) -> LLMResult:
        backend = self.backend(profile)
        t0 = time.monotonic()
        if images:
            res = await backend.complete_vision(system, user, images)
        else:
            res = await backend.complete(system, user)
        res._latency_ms = int((time.monotonic() - t0) * 1000)
        return res

    def _record(self, profile: Profile, res: LLMResult, *, role: str, stage: str,
                lang: str, retries: int) -> None:
        if self.usage_log is None:
            return
        cost = price_of(profile, res.prompt_tokens, res.completion_tokens)
        self.usage_log.append(Usage(
            role=role, profile=profile.id, model=profile.model or res.model,
            stage=stage, lang=lang,
            prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
            requests=1, retries=retries,
            latency_ms=int(getattr(res, "_latency_ms", 0) or 0),
            cost=cost,
        ))

    async def complete(self, system: str, user: str, *, role: str = "analyze",
                       stage: str = "", lang: str = "") -> LLMResult:
        """便捷入口：兼容旧代码里 `provider.complete(system, user)` 的调用形状。"""
        res, _profile = await self.run(role, system, user, stage=stage, lang=lang)
        return res

    async def aclose(self) -> None:
        for b in self._backends.values():
            await b.aclose()
        self._backends.clear()
