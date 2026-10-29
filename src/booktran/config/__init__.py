"""运行配置：默认值 < 全局文件 < 项目差异 < 一次性 override。

项目只存**引用与差异**（booktran.toml），模型档案在全局 profiles.toml。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .keyring import key_source, load_keys_file, resolve_api_key, resolve_base_url
from .models import (
    AppConfig,
    Budget,
    LLMConfig,
    Profile,
    RoleSpec,
    RouteRule,
    TranslateConfig,
    caps_of,
)
from .profiles import (
    dump_toml,
    load_profile_file,
    merge_into,
    merge_profile,
    merge_roles,
    parse_legacy,
    parse_profiles,
    read_toml,
    toml_value,
)
from .resolver import (
    PROJECT_CONFIG_NAME,
    apply_overrides,
    apply_platform,
    describe_key_source,
    migrate_project,
    resolve,
    resolve_task_platform,
    write_project_config,
)

GLOBAL_CONFIG_PATH = Path(
    os.environ.get("BOOKTRAN_CONFIG", "~/.config/booktran/config.toml")).expanduser()


def load_config(project_dir: Path | None = None, overrides: dict | None = None) -> AppConfig:
    """解析配置（全局 + 项目差异 + override）。

    读取 GLOBAL_CONFIG_PATH 的**当前值**，因此测试可以 monkeypatch 它。
    """
    return resolve(project_dir, overrides,
                   global_config_path=GLOBAL_CONFIG_PATH,
                   keys_path=GLOBAL_CONFIG_PATH.parent / "keys.toml")


def keys_path() -> Path:
    return GLOBAL_CONFIG_PATH.parent / "keys.toml"


def profiles_path() -> Path:
    return GLOBAL_CONFIG_PATH.parent / "profiles.toml"


# ------------------------------------------------------------ 项目清单（旧格式）

def save_project_manifest(project_dir: Path, source: Path, cfg: AppConfig | None = None) -> None:
    """写 project.json（项目标识）。

    cfg 为 None → 只写标识（v0.5 默认：**不存配置快照**）；
    传 cfg → 额外写一份脱敏快照（仅用于迁移旧项目，读取时不再信任）。
    """
    manifest: dict = {
        "version": 2,
        "source": str(source),
        "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    if cfg is not None:
        manifest["config"] = cfg.to_dict()
    _atomic_write_json(Path(project_dir) / "project.json", manifest)


def load_project_manifest(project_dir: Path) -> dict:
    return json.loads((Path(project_dir) / "project.json").read_text("utf-8"))


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "AppConfig", "LLMConfig", "TranslateConfig", "Profile", "RoleSpec",
    "RouteRule", "Budget", "caps_of",
    "GLOBAL_CONFIG_PATH", "load_config", "keys_path", "profiles_path",
    "apply_platform", "apply_overrides", "resolve_task_platform", "resolve",
    "save_project_manifest", "load_project_manifest",
    "write_project_config", "migrate_project", "PROJECT_CONFIG_NAME",
    "load_profile_file", "merge_into", "merge_roles", "merge_profile",
    "parse_profiles", "parse_legacy", "read_toml", "dump_toml", "toml_value",
    "resolve_api_key", "resolve_base_url", "key_source", "load_keys_file",
    "describe_key_source",
]
