"""配置数据模型：Profile（模型档案）/ RoleSpec（角色路由）/ Budget（预算）。

设计要点（v0.5）：
- **Profile 是可复用的引用**，不属于任何项目；项目只写"用哪个 profile"。
- **Role 把工作类型映射到 profile**，同一份配置里可以并存多个模型。
- 价格用 Optional：未配置 = 未知（显示 —），而不是 0（撒谎）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

# 后端能力（替代"厂商"抽象）
CAP_TEXT = "text"
CAP_VISION = "vision"
CAP_OCR = "ocr"
CAP_EMBED = "embed"

KIND_CAPS = {
    "chat": {CAP_TEXT},
    "vision": {CAP_TEXT, CAP_VISION},
    "ocr": {CAP_TEXT, CAP_OCR},
    "local": {CAP_TEXT},
}


def caps_of(kind: str) -> set[str]:
    return set(KIND_CAPS.get((kind or "chat").lower(), {CAP_TEXT}))


@dataclass
class Profile:
    """一个模型端点档案（可任意多个，跨项目复用）。"""

    id: str = ""
    kind: str = "chat"                     # chat | vision | ocr | local
    provider: str = "anthropic"            # anthropic | openai | fake | ...
    model: str = ""
    base_url: str = ""
    key_env: str = ""                      # 从哪个环境变量读密钥
    api_key: str = ""                      # 不建议写在文件里；由 keyring 注入
    api_keys: list[str] = field(default_factory=list)
    concurrency: int = 4
    rpm: int = 0
    max_output_tokens: int = 8192
    temperature: float | None = None
    timeout: float = 300.0
    max_retries: int = 4
    prompt_preset: str = "default"         # default | local
    price_in: float | None = None          # $/Mtok（None = 未配置 = 未知）
    price_out: float | None = None
    price_cache_read: float | None = None
    # 配置文件里**显式出现**的字段名：用于"档案未定义 → 保持原值"的字段级合并
    explicit: list[str] = field(default_factory=list)

    @property
    def caps(self) -> set[str]:
        return caps_of(self.kind)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        return d

    def is_explicit(self, name: str) -> bool:
        return name in (self.explicit or [])

    @classmethod
    def from_dict(cls, pid: str, d: dict | None) -> Profile:
        d = dict(d or {})
        p = cls(id=pid)
        for f in fields(cls):
            if f.name in d:
                setattr(p, f.name, d[f.name])
        p.id = pid
        return p


@dataclass
class RouteRule:
    """级联规则：命中条件 → 用哪个 profile。"""

    match: dict[str, Any] = field(default_factory=dict)
    use: str = ""

    @classmethod
    def from_dict(cls, d: dict | None) -> RouteRule:
        d = dict(d or {})
        return cls(match=dict(d.get("match") or {}), use=str(d.get("use") or ""))


@dataclass
class RoleSpec:
    """角色 → profile 的绑定。

    - profile: 直接指定一个档案
    - default: 级联的默认档案
    - fallback: 失败降级档案
    - rules: 按段特征选模的级联规则
    """

    profile: str = ""
    default: str = ""
    fallback: str = ""
    rules: list[RouteRule] = field(default_factory=list)

    @property
    def target(self) -> str:
        return self.profile or self.default

    def to_dict(self) -> dict:
        return {
            "profile": self.profile, "default": self.default,
            "fallback": self.fallback,
            "rules": [{"match": r.match, "use": r.use} for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> RoleSpec:
        d = dict(d or {})
        rules = [RouteRule.from_dict(r) for r in (d.get("rules") or [])]
        return cls(
            profile=str(d.get("profile") or ""),
            default=str(d.get("default") or ""),
            fallback=str(d.get("fallback") or ""),
            rules=rules,
        )


@dataclass
class Budget:
    """预算闸：超限即暂停，不静默烧钱。"""

    max_cost: float = 0.0        # 0 = 不限（美元）
    max_tokens: int = 0          # 0 = 不限

    def to_dict(self) -> dict:
        return {"max_cost": self.max_cost, "max_tokens": self.max_tokens}

    @classmethod
    def from_dict(cls, d: dict | None) -> Budget:
        d = dict(d or {})
        b = cls()
        if "max_cost" in d:
            b.max_cost = float(d["max_cost"] or 0)
        if "max_tokens" in d:
            b.max_tokens = int(d["max_tokens"] or 0)
        return b


# ---------------------------------------------------------------- 运行时配置

@dataclass
class LLMConfig:
    provider: str = "anthropic"            # anthropic | openai | fake
    model: str = "claude-sonnet-4-5"
    base_url: str = ""                     # 留空用各家默认 / 环境变量
    api_key: str = ""                      # 留空时从环境变量取
    api_key_env: str = ""                  # 指定环境变量名（优先于默认探测）
    api_keys: list[str] = field(default_factory=list)  # key 池：轮询摊薄 RPM
    concurrency: int = 4
    rpm: int = 0                           # 0 = 不限
    max_output_tokens: int = 8192
    temperature: float | None = None       # None = 不传，用服务端默认
    timeout: float = 300.0
    max_retries: int = 4                   # 单请求级指数退避
    prompt_preset: str = "default"         # default | local


@dataclass
class TranslateConfig:
    source_lang: str = "auto"              # auto = 按每个文档的字符分布独立检测
    target_langs: list[str] = field(default_factory=lambda: ["chinese_simplified"])
    chunk_budget_tokens: int = 4000        # 每块输入 token 预算
    min_budget_tokens: int = 400           # 减半下限
    rounds: int = 6                        # 最大轮次，之后剩余标记 error
    pre_context_chars: int = 1200          # 上文窗口（字符）
    strict_placeholders: bool = True       # 占位符缺失视为整块失败
    exclude_symbols: bool = True           # 纯符号/数字行直接 EXCLUDED
    system_prompt: str = ""                # 整体自定义提示词
    system_prompt_file: str = ""           # 从文件读取（优先于 system_prompt 字符串）
    zh_convert: str = ""                   # 简繁转换：s2t | t2s，空=不转换
    layout: str = "original"               # original | model | hybrid
    domain: str = ""                       # 领域预设：religion/law/medicine/academic/technical/literary

    @property
    def target_lang(self) -> str:
        """主目标语言（第一个）。向后兼容单语言访问。"""
        return self.target_langs[0] if self.target_langs else "chinese_simplified"

    @target_lang.setter
    def target_lang(self, lang: str) -> None:
        self.target_langs = [lang] if lang else ["chinese_simplified"]

    @classmethod
    def from_dict(cls, d: dict | None) -> TranslateConfig:
        cfg = cls()
        if not d:
            return cfg
        d = dict(d)
        legacy = d.pop("target_lang", None)
        for f in fields(cls):
            if f.name in d:
                setattr(cfg, f.name, d[f.name])
        if cfg.target_langs == ["chinese_simplified"] and legacy:
            cfg.target_langs = [legacy]
        cfg.target_langs = [x.strip() for x in (cfg.target_langs or []) if x and x.strip()] or ["chinese_simplified"]
        return cfg


@dataclass
class AppConfig:
    """解析后的运行配置（全局 + 项目差异合并的结果）。"""

    llm: LLMConfig = field(default_factory=LLMConfig)
    tr: TranslateConfig = field(default_factory=TranslateConfig)
    # 兼容层：platforms/routing 是 profile/roles 的旧视图
    platforms: dict[str, dict] = field(default_factory=dict)
    routing: dict[str, str] = field(default_factory=dict)
    active_platform: str = ""
    # 新模型
    profiles: dict[str, Profile] = field(default_factory=dict)
    roles: dict[str, RoleSpec] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)

    def ensure_langs(self) -> None:
        """保证 target_langs 至少有一个语言（并清洗空项）。"""
        self.tr.target_langs = [x.strip() for x in (self.tr.target_langs or []) if x and x.strip()]
        if not self.tr.target_langs:
            self.tr.target_langs = ["chinese_simplified"]

    def to_dict(self) -> dict:
        llm = asdict(self.llm)
        # 密钥不落盘进项目快照（避免 project.json 变成凭据泄漏点）
        llm["api_key"] = ""
        llm["api_keys"] = []
        return {
            "llm": llm,
            "translate": asdict(self.tr),
            "routing": self.routing,
            "active_platform": self.active_platform,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> AppConfig:
        cfg = cls()
        if not d:
            return cfg
        if isinstance(d.get("llm"), dict):
            for f in fields(LLMConfig):
                if f.name in d["llm"]:
                    setattr(cfg.llm, f.name, d["llm"][f.name])
        if isinstance(d.get("translate"), dict):
            cfg.tr = TranslateConfig.from_dict(d["translate"])
        cfg.routing = dict(d.get("routing") or {})
        cfg.active_platform = d.get("active_platform", "")
        return cfg

    # ---- profile / role 便捷访问 ----

    def profile(self, pid: str) -> Profile | None:
        return self.profiles.get(pid)

    def role(self, name: str) -> RoleSpec:
        return self.roles.get(name) or RoleSpec()

    def profile_for_role(self, name: str) -> str:
        """角色 → 档案 id（profile 优先，其次级联 default，最后 active_platform）。"""
        spec = self.role(name)
        return spec.target or self.active_platform or ""

    def sync_legacy_views(self) -> None:
        """把 profiles/roles 同步到旧的 platforms/routing 视图（向后兼容）。"""
        if not self.platforms and self.profiles:
            for pid, p in self.profiles.items():
                d = p.to_dict()
                d.pop("price_in", None)
                d.pop("price_out", None)
                d.pop("price_cache_read", None)
                self.platforms[pid] = d
        if not self.routing and self.roles:
            for name, spec in self.roles.items():
                if spec.target:
                    self.routing[name] = spec.target
        if not self.active_platform and self.roles:
            spec = self.roles.get("translate")
            if spec and spec.target:
                self.active_platform = spec.target
