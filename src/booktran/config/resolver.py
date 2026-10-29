"""配置解析：五级优先级合并（引用式，不冻结）。

    内置默认
      → ~/.config/booktran/config.toml      [llm] [translate]
      → ~/.config/booktran/profiles.toml     [profile.*] [roles] [budget]
      → ~/.config/booktran/platforms.toml    （v0.4 兼容）
      → <项目>/booktran.toml                 [project] [roles] [translate] [budget]（只写差异）
      → <项目>/platforms.toml                （v0.4 兼容）
      → CLI / UI 的一次性 override

**关键性质**：项目文件里没写的字段一律从全局档案现取。改一行全局档案，
所有项目下次运行立刻生效——这就是"配置不冻结"。
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .keyring import key_source, load_keys_file, resolve_api_key, resolve_base_url
from .models import AppConfig, Budget, LLMConfig, Profile, RoleSpec, TranslateConfig
from .profiles import load_profile_file, merge_into, merge_roles, read_toml

PROJECT_CONFIG_NAME = "booktran.toml"


def _apply_config_toml(cfg: AppConfig, path: Path | None) -> None:
    """读 config.toml 的 [llm] / [translate] 段（也支持扁平写法）。"""
    if path is None:
        return
    data = read_toml(path)
    if not data:
        return
    if "llm" in data or "translate" in data:
        tmp = AppConfig.from_dict({"llm": data.get("llm", {}),
                                   "translate": data.get("translate", {})})
        cfg.llm, cfg.tr = tmp.llm, tmp.tr
    else:
        tmp = AppConfig.from_dict(data)
        cfg.llm, cfg.tr = tmp.llm, tmp.tr


def _apply_project_file(cfg: AppConfig, project_dir: Path) -> None:
    """读项目 booktran.toml：只覆盖显式写出的字段。"""
    data = read_toml(project_dir / PROJECT_CONFIG_NAME)
    if not data:
        return
    proj = data.get("project") or {}
    if "source_lang" in proj:
        cfg.tr.source_lang = str(proj["source_lang"])
    if "target_langs" in proj:
        tl = proj["target_langs"]
        if isinstance(tl, str):
            tl = [x.strip() for x in tl.split(",") if x.strip()]
        cfg.tr.target_langs = [str(x) for x in (tl or [])] or cfg.tr.target_langs
    if "layout" in proj:
        cfg.tr.layout = str(proj["layout"])
    if "domain" in proj:
        cfg.tr.domain = str(proj["domain"])

    tr = data.get("translate") or {}
    tmp = TranslateConfig.from_dict(tr)
    for f in fields(TranslateConfig):
        if f.name in tr:
            setattr(cfg.tr, f.name, getattr(tmp, f.name))

    for name, spec in (data.get("roles") or {}).items():
        if isinstance(spec, str):
            cfg.roles[name] = RoleSpec(profile=spec)
        elif isinstance(spec, dict):
            cfg.roles[name] = RoleSpec.from_dict(spec)

    if "budget" in data:
        cfg.budget = Budget.from_dict(data["budget"])


def _inject_keys(cfg: AppConfig, keys_path: Path | None) -> None:
    """给每个档案注入密钥（env → keys.toml），并补 base_url 兜底。"""
    keys = load_keys_file(keys_path)
    for p in cfg.profiles.values():
        if not p.api_key:
            p.api_key = resolve_api_key(p, keys=keys)
        if not p.base_url:
            p.base_url = resolve_base_url(p)


def resolve(
    project_dir: Path | None = None,
    overrides: dict | None = None,
    *,
    global_config_path: Path | None = None,
    keys_path: Path | None = None,
) -> AppConfig:
    """解析出最终配置。"""
    cfg = AppConfig()
    gpath = Path(global_config_path) if global_config_path else None
    gdir = gpath.parent if gpath else Path.home() / ".config" / "booktran"

    # L2 全局 config.toml
    _apply_config_toml(cfg, gpath)

    # L3 全局 profiles.toml（新）
    p, r, b = load_profile_file(gdir / "profiles.toml")
    merge_into(cfg.profiles, p)
    merge_roles(cfg.roles, r)
    if b.max_cost or b.max_tokens:
        cfg.budget = b

    # L3' 全局 platforms.toml（v0.4 兼容）
    legacy = read_toml(gdir / "platforms.toml")
    if legacy:
        p, r, b = load_profile_file(gdir / "platforms.toml", legacy=True)
        merge_into(cfg.profiles, p)
        merge_roles(cfg.roles, r)
        if legacy.get("active_platform"):
            cfg.active_platform = str(legacy["active_platform"])

    # L4 项目 booktran.toml（差异）
    if project_dir is not None:
        _apply_project_file(cfg, Path(project_dir))

    # L5 项目 platforms.toml / profiles.toml（v0.4 兼容）
        legacy = read_toml(Path(project_dir) / "platforms.toml")
        if legacy:
            p, r, b = load_profile_file(Path(project_dir) / "platforms.toml", legacy=True)
            merge_into(cfg.profiles, p)
            merge_roles(cfg.roles, r)
            if legacy.get("active_platform"):
                cfg.active_platform = str(legacy["active_platform"])
        p, r, b = load_profile_file(Path(project_dir) / "profiles.toml")
        if p:
            merge_into(cfg.profiles, p)
            merge_roles(cfg.roles, r)

    _inject_keys(cfg, keys_path if keys_path is not None else (gdir / "keys.toml"))

    # L6 一次性 override
    if overrides:
        apply_overrides(cfg, overrides)

    cfg.ensure_langs()
    cfg.sync_legacy_views()
    return cfg


def apply_overrides(cfg: AppConfig, overrides: dict) -> None:
    """CLI / UI 的一次性覆盖。"""
    llm_keys = ("provider", "model", "base_url", "api_key", "concurrency", "rpm",
                "max_output_tokens", "prompt_preset", "temperature", "timeout",
                "max_retries")
    for k in llm_keys:
        v = overrides.get(k)
        if v not in (None, "", 0, 0.0):
            setattr(cfg.llm, k, v)
    tr_keys = ("source_lang", "chunk_budget_tokens", "rounds", "system_prompt",
               "system_prompt_file", "zh_convert", "layout", "pre_context_chars",
               "min_budget_tokens")
    for k in tr_keys:
        v = overrides.get(k)
        if v not in (None, "", 0, 0.0):
            setattr(cfg.tr, k, v)
    if overrides.get("target_langs"):
        tl = overrides["target_langs"]
        if isinstance(tl, str):
            tl = [x.strip() for x in tl.split(",") if x.strip()]
        cfg.tr.target_langs = [str(x) for x in tl]
    elif overrides.get("target_lang"):
        cfg.tr.target_langs = [str(overrides["target_lang"])]
    if overrides.get("platform"):
        apply_platform(cfg, str(overrides["platform"]))
    if overrides.get("roles"):
        for name, pid in dict(overrides["roles"]).items():
            cfg.roles[name] = RoleSpec(profile=str(pid))


def apply_platform(cfg: AppConfig, name: str) -> None:
    """把某个档案合并进 cfg.llm（v0.4 API 兼容：档案未定义的字段保持原值）。"""
    profile = cfg.profiles.get(name)
    explicit: list[str] | None = None
    if profile is None and name in cfg.platforms:
        raw = dict(cfg.platforms[name] or {})
        profile = Profile.from_dict(name, raw)
        explicit = [k for k in raw if k in {
            "provider", "model", "base_url", "api_key", "api_keys", "api_key_env",
            "concurrency", "rpm", "max_output_tokens", "temperature", "timeout",
            "max_retries", "prompt_preset"}]
    if profile is None:
        known = ", ".join(sorted(cfg.profiles)) or "无"
        raise SystemExit(f"模型档案 '{name}' 不存在（已定义: {known}）")
    for f in fields(LLMConfig):
        if explicit is not None:
            if f.name not in explicit:
                continue
        elif not profile.is_explicit(f.name):
            continue
        v = getattr(profile, f.name, None)
        if v in (None, "", [], 0, 0.0):
            continue
        setattr(cfg.llm, f.name, v)
    if getattr(profile, "key_env", "") and profile.is_explicit("key_env"):
        cfg.llm.api_key_env = profile.key_env
    cfg.active_platform = name


def resolve_task_platform(cfg: AppConfig, task: str) -> str:
    """角色 → 档案 id；缺省回落 active_platform。"""
    spec = cfg.roles.get(task)
    if spec and spec.target:
        return spec.target
    return cfg.active_platform or ""


def describe_key_source(cfg: AppConfig, pid: str, *, keys_path: Path | None = None) -> str:
    """给 doctor 用：某档案的密钥来源说明。"""
    keys = load_keys_file(keys_path)
    p = cfg.profiles.get(pid)
    if p is None:
        return "档案不存在"
    return key_source(p, keys=keys)


# ------------------------------------------------------------------ 项目配置 IO

def write_project_config(project_dir: Path, cfg: AppConfig, *,
                         roles: dict[str, str] | None = None,
                         budget=None,
                         extra: dict | None = None) -> Path:
    """写项目 booktran.toml：**只写引用与差异**，不写模型配置。"""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / PROJECT_CONFIG_NAME
    lines: list[str] = []
    lines.append("# booktran 项目配置：只写差异。模型档案在 ~/.config/booktran/profiles.toml，")
    lines.append("# 改全局档案会立刻影响本项目（配置是引用，不是快照）。")
    lines.append("")
    lines.append("[project]")
    lines.append(f'source_lang = "{cfg.tr.source_lang}"')
    tl = ", ".join(f'"{x}"' for x in cfg.tr.target_langs)
    lines.append(f"target_langs = [{tl}]")
    lines.append(f'layout = "{cfg.tr.layout}"')
    if cfg.tr.domain:
        lines.append(f'domain = "{cfg.tr.domain}"')
    lines.append("")
    if roles:
        lines.append("[roles]")
        for name, pid in roles.items():
            lines.append(f'{name} = "{pid}"')
        lines.append("")
    lines.append("[translate]")
    lines.append(f"chunk_budget_tokens = {cfg.tr.chunk_budget_tokens}")
    lines.append(f"rounds = {cfg.tr.rounds}")
    if cfg.tr.zh_convert:
        lines.append(f'zh_convert = "{cfg.tr.zh_convert}"')
    if budget is not None and (getattr(budget, "max_cost", 0) or getattr(budget, "max_tokens", 0)):
        lines.append("")
        lines.append("[budget]")
        lines.append(f"max_cost = {getattr(budget, 'max_cost', 0)}")
        lines.append(f"max_tokens = {getattr(budget, 'max_tokens', 0)}")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def migrate_project(project_dir: Path) -> str:
    """旧项目（project.json 里存着配置快照）→ 迁移为 booktran.toml。

    返回迁移说明（没得迁返回空串）。旧快照保留在 project.json 里但**不再被读取**。
    """
    project_dir = Path(project_dir)
    manifest_path = project_dir / "project.json"
    target = project_dir / PROJECT_CONFIG_NAME
    if not manifest_path.exists() or target.exists():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    snap = manifest.get("config") or {}
    if not snap:
        return ""
    tr = TranslateConfig.from_dict(snap.get("translate"))
    roles = dict(snap.get("routing") or {})
    active = str(snap.get("active_platform") or "")
    if active and "translate" not in roles:
        roles["translate"] = active
    cfg = AppConfig(tr=tr)
    write_project_config(project_dir, cfg, roles=roles)
    return f"已把旧项目快照迁移为 {PROJECT_CONFIG_NAME}（模型档案仍需在 profiles.toml 中定义）"
