"""密钥解析：环境变量优先，其次档案内显式值，最后 keys.toml。

**项目目录永不存密钥。** 全局 `~/.config/booktran/keys.toml`（建议 0600）形如：

    [key.anthropic]
    api_key = "sk-ant-..."

    [key.claude]        # 也可按 profile id 存
    api_key = "sk-ant-..."
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .models import Profile

DEFAULT_ENV_BY_PROVIDER = {
    "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
    "openai": ["OPENAI_API_KEY"],
    "openai-compat": ["OPENAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"],
    "ollama": ["OLLAMA_API_KEY"],
    "vllm": ["VLLM_API_KEY", "OPENAI_API_KEY"],
}
DEFAULT_ENV_BY_PROVIDER["local"] = ["BOOKTRAN_API_KEY"]

DEFAULT_BASE_BY_PROVIDER = {
    "anthropic": ["ANTHROPIC_BASE_URL"],
    "openai": ["OPENAI_BASE_URL", "OPENAI_API_BASE"],
    "openai-compat": ["OPENAI_BASE_URL", "OPENAI_API_BASE"],
}


def load_keys_file(path: Path | None) -> dict[str, str]:
    """读 keys.toml → {档案 id 或 provider 名: api_key}。"""
    out: dict[str, str] = {}
    if path is None:
        return out
    try:
        data = tomllib.loads(Path(path).read_text("utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return out
    for name, val in data.items():
        if name == "key" and isinstance(val, dict):
            for kid, kv in val.items():
                if isinstance(kv, dict) and kv.get("api_key"):
                    out[kid] = str(kv["api_key"])
                elif isinstance(kv, str):
                    out[kid] = kv
        elif isinstance(val, dict) and val.get("api_key"):
            out[name] = str(val["api_key"])
        elif isinstance(val, str):
            out[name] = val
    return out


def resolve_api_key(profile: Profile, *, keys: dict[str, str] | None = None,
                    env: dict[str, str] | None = None) -> str:
    """按优先级解析该档案的 api_key（不含 key 池）。"""
    env = os.environ if env is None else env
    keys = keys or {}
    if profile.api_key:
        return profile.api_key
    if profile.key_env and env.get(profile.key_env):
        return env[profile.key_env]
    if keys.get(profile.id):
        return keys[profile.id]
    if keys.get(profile.provider):
        return keys[profile.provider]
    for name in DEFAULT_ENV_BY_PROVIDER.get(profile.provider, ["BOOKTRAN_API_KEY"]):
        if env.get(name):
            return env[name]
    return ""


def resolve_base_url(profile: Profile, *, env: dict[str, str] | None = None) -> str:
    """档案没写 base_url 时，从环境变量兜底。"""
    env = os.environ if env is None else env
    if profile.base_url:
        return profile.base_url
    for name in DEFAULT_BASE_BY_PROVIDER.get(profile.provider, []):
        if env.get(name):
            return env[name]
    return ""


def key_source(profile: Profile, *, keys: dict[str, str] | None = None,
               env: dict[str, str] | None = None) -> str:
    """密钥来源说明（给 doctor 用）：env:NAME / keys.toml / 档案内 / 未找到。"""
    env = os.environ if env is None else env
    keys = keys or {}
    if profile.api_key:
        return "档案内（不推荐）"
    if profile.key_env and env.get(profile.key_env):
        return f"env:{profile.key_env}"
    if keys.get(profile.id):
        return f"keys.toml:{profile.id}"
    if keys.get(profile.provider):
        return f"keys.toml:{profile.provider}"
    for name in DEFAULT_ENV_BY_PROVIDER.get(profile.provider, []):
        if env.get(name):
            return f"env:{name}"
    return "未找到"
