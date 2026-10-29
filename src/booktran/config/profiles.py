"""读档案文件：profiles.toml（新格式）与 platforms.toml（v0.4 兼容格式）。

新格式：

    [profile.claude]
    kind = "vision"          # chat | vision | ocr | local
    provider = "anthropic"
    model = "claude-sonnet-4-5"
    key_env = "ANTHROPIC_API_KEY"
    price_in = 3.0           # $/Mtok（不写 = 未知）
    price_out = 15.0

    [roles]
    translate = "claude"

    [roles.translate_cascade]
    default = "claude"
    fallback = "qwen-local"
    rules = [{ match = { chars_gt = 400 }, use = "claude" }]

    [budget]
    max_cost = 5.0
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .models import Budget, Profile, RoleSpec

PROFILE_FIELDS = {
    "kind", "provider", "model", "base_url", "key_env", "api_key", "api_keys",
    "concurrency", "rpm", "max_output_tokens", "temperature", "timeout",
    "max_retries", "prompt_preset", "price_in", "price_out", "price_cache_read",
}


def read_toml(path: Path | None) -> dict[str, Any]:
    """读 TOML；不存在或损坏返回空字典（损坏时抛错由调用方决定）。"""
    if path is None:
        return {}
    try:
        return tomllib.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"配置文件解析失败 {path}: {e}") from e


def _profile_from(data: dict, pid: str) -> Profile:
    p = Profile(id=pid)
    for k, v in data.items():
        if k in PROFILE_FIELDS:
            setattr(p, k, v)
    p.id = pid
    p.explicit = [k for k in data if k in PROFILE_FIELDS]
    return p


def merge_profile(base: Profile, new: Profile) -> Profile:
    """字段级合并：新档案只覆盖显式出现的字段（未出现 → 保持原值）。"""
    merged = Profile.from_dict(base.id, base.to_dict())
    merged.explicit = list(base.explicit or [])
    for k, v in new.to_dict().items():
        if k in ("id", "explicit"):
            continue
        if k in ("price_in", "price_out", "price_cache_read"):
            if v is not None:
                setattr(merged, k, v)
            continue
        if v is None or v == "" or v == [] or v == 0 or v == 0.0:
            continue
        setattr(merged, k, v)
    for k in (new.explicit or []):
        if k not in merged.explicit:
            merged.explicit.append(k)
    merged.id = base.id
    return merged


def parse_profiles(data: dict[str, Any]) -> tuple[dict[str, Profile], dict[str, RoleSpec], Budget]:
    """解析新格式的 profile/roles/budget。"""
    profiles: dict[str, Profile] = {}
    for pid, pdata in (data.get("profile") or {}).items():
        profiles[pid] = _profile_from(dict(pdata or {}), pid)

    roles: dict[str, RoleSpec] = {}
    raw_roles = data.get("roles") or {}
    for name, spec in raw_roles.items():
        if isinstance(spec, str):
            roles[name] = RoleSpec(profile=spec)
        elif isinstance(spec, dict):
            roles[name] = RoleSpec.from_dict(spec)

    # [roles.<name>_cascade] → 合并进同名基础角色（default/fallback/rules）
    for name in list(roles.keys()):
        if not name.endswith("_cascade"):
            continue
        base = name[: -len("_cascade")]
        spec = roles.pop(name)
        if base not in roles:
            roles[base] = spec
            continue
        target = roles[base]
        target.default = target.default or spec.default
        target.fallback = target.fallback or spec.fallback
        target.rules = target.rules or spec.rules

    budget = Budget.from_dict(data.get("budget"))
    return profiles, roles, budget


def parse_legacy(data: dict[str, Any]) -> tuple[dict[str, Profile], dict[str, RoleSpec], Budget]:
    """解析 v0.4 的 platforms.toml 格式（[platforms.X] + [routing]）。"""
    profiles: dict[str, Profile] = {}
    for pid, pdata in (data.get("platforms") or {}).items():
        profiles[pid] = _profile_from(dict(pdata or {}), pid)

    roles: dict[str, RoleSpec] = {}
    for name, pid in (data.get("routing") or {}).items():
        if isinstance(pid, str):
            roles[name] = RoleSpec(profile=pid)
    return profiles, roles, Budget()


def merge_into(base: dict[str, Profile], new: dict[str, Profile]) -> None:
    """把 new 合并进 base（同 id 字段级合并）。"""
    for pid, p in new.items():
        base[pid] = merge_profile(base[pid], p) if pid in base else p


def merge_roles(base: dict[str, RoleSpec], new: dict[str, RoleSpec]) -> None:
    for name, spec in new.items():
        if name in base:
            old = base[name]
            old.profile = spec.profile or old.profile
            old.default = spec.default or old.default
            old.fallback = spec.fallback or old.fallback
            old.rules = spec.rules or old.rules
        else:
            base[name] = spec


def load_profile_file(path: Path | None, *, legacy: bool = False):
    """读一个档案文件，返回 (profiles, roles, budget)。"""
    data = read_toml(path)
    if not data:
        return {}, {}, Budget()
    if legacy:
        return parse_legacy(data)
    return parse_profiles(data)


# ---------------- 极简 TOML 写出 ----------------

def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        # 内联表：{ match = { chars_gt = 400 }, use = "claude" }
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return '"' + str(v).replace('"', chr(92) + chr(34)) + '"'


def dump_toml(data: dict) -> str:
    """序列化（顶层标量 + 一层表；表内可混合标量与子表）。

    例：[roles] 里既有 translate = "claude"，也有 [roles.translate_cascade] 子表。
    """
    lines: list[str] = []
    tables: dict[str, dict] = {}
    for top, value in data.items():
        if isinstance(value, dict):
            tables[top] = value
        else:
            lines.append(f"{top} = {toml_value(value)}")
    for top, value in tables.items():
        plain = {k: v for k, v in value.items() if not isinstance(v, dict)}
        nested = {k: v for k, v in value.items() if isinstance(v, dict)}
        if plain:
            lines.append(f"[{top}]")
            for k, v in plain.items():
                lines.append(f"{k} = {toml_value(v)}")
            lines.append("")
        for name, sub in nested.items():
            lines.append(f"[{top}.{name}]")
            for k, v in (sub or {}).items():
                lines.append(f"{k} = {toml_value(v)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
