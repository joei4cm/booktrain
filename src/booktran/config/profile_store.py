"""全局配置文件的读写（profiles.toml / config.toml）。

网页端改档案时写这里，项目只存引用。
"""
from __future__ import annotations

from pathlib import Path

from .models import Budget, Profile, RoleSpec
from .profiles import dump_toml, read_toml


class ProfileStore:
    """全局 profiles.toml 的读写封装。"""

    def __init__(self, profiles_path: Path, keys_path: Path | None = None) -> None:
        self.path = Path(profiles_path)
        self.keys_path = keys_path

    # ---------- profiles ----------
    def load_profiles(self) -> dict[str, Profile]:
        from .profiles import parse_profiles
        data = read_toml(self.path)
        profiles, _roles, _budget = parse_profiles(data)
        return profiles

    def save_profile(self, profile: Profile) -> None:
        data = read_toml(self.path)
        profiles = data.setdefault("profile", {})
        entry = profile.to_dict()
        entry.pop("id", None)
        profiles[profile.id] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dump_toml(data), encoding="utf-8")

    def delete_profile(self, pid: str) -> bool:
        data = read_toml(self.path)
        profiles = data.get("profile", {})
        if pid not in profiles:
            return False
        del profiles[pid]
        # 同步清理引用该档案的角色
        roles = data.get("roles", {})
        for key in list(roles.keys()):
            if roles[key] == pid:
                del roles[key]
        self.path.write_text(dump_toml(data), encoding="utf-8")
        return True

    # ---------- roles ----------
    def load_roles(self) -> dict[str, RoleSpec]:
        from .profiles import parse_profiles
        data = read_toml(self.path)
        _profiles, roles, _budget = parse_profiles(data)
        return roles

    def save_role(self, name: str, spec: RoleSpec) -> None:
        data = read_toml(self.path)
        roles = data.setdefault("roles", {})
        roles[name] = spec.profile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dump_toml(data), encoding="utf-8")

    def save_roles(self, roles: dict[str, str]) -> None:
        data = read_toml(self.path)
        data["roles"] = roles
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dump_toml(data), encoding="utf-8")

    # ---------- budget ----------
    def load_budget(self) -> Budget:
        from .profiles import parse_profiles
        data = read_toml(self.path)
        _p, _r, budget = parse_profiles(data)
        return budget

    def save_budget(self, budget: Budget) -> None:
        data = read_toml(self.path)
        data["budget"] = budget.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(dump_toml(data), encoding="utf-8")
