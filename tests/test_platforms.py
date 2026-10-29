"""接口档案（platforms.toml）/ 任务路由 / key 池 / 本地提示词预设。"""
from __future__ import annotations

import asyncio

import pytest

from booktran import config as config_mod
from booktran.config import (
    AppConfig,
    apply_platform,
    load_config,
    resolve_task_platform,
)
from booktran.llm import make_provider
from booktran.llm.base import KeyPool
from booktran.prompt.builder import build_system, build_user

PLATFORMS_TOML = """
active_platform = "claude"

[platforms.claude]
provider = "anthropic"
model = "claude-sonnet-4-5"
base_url = "https://api.anthropic.com"
api_key = "sk-ant-test"
concurrency = 8

[platforms.local]
provider = "openai"
model = "qwen2.5:7b"
base_url = "http://127.0.0.1:11434/v1"
api_key = "local"
prompt_preset = "local"

[routing]
translate = "claude"
analyze = "local"
"""


@pytest.fixture
def platform_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", cfg_path)
    (tmp_path / "platforms.toml").write_text(PLATFORMS_TOML, "utf-8")
    return tmp_path


def test_load_platforms_and_routing(platform_env):
    cfg = load_config(platform_env)
    assert set(cfg.platforms) == {"claude", "local"}
    assert cfg.routing["translate"] == "claude"
    assert cfg.active_platform == "claude"
    assert resolve_task_platform(cfg, "translate") == "claude"
    assert resolve_task_platform(cfg, "analyze") == "local"
    assert resolve_task_platform(cfg, "unknown") == "claude"  # 回落 active


def test_project_platforms_override_global(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", cfg_path)
    (tmp_path / "platforms.toml").write_text(PLATFORMS_TOML, "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "platforms.toml").write_text(
        '[platforms.claude]\nmodel = "claude-opus-5"\n', "utf-8")
    cfg = load_config(proj)
    assert cfg.platforms["claude"]["model"] == "claude-opus-5"
    assert cfg.platforms["claude"]["provider"] == "anthropic"  # 未覆盖字段保留
    assert cfg.routing["translate"] == "claude"


def test_apply_platform_merges(platform_env):
    cfg = load_config(platform_env)
    cfg.llm.concurrency = 2
    cfg.llm.max_output_tokens = 4096
    apply_platform(cfg, "local")
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "qwen2.5:7b"
    assert cfg.llm.prompt_preset == "local"
    assert cfg.llm.concurrency == 2          # 档案未定义 → 保持原值
    assert cfg.llm.max_output_tokens == 4096
    assert cfg.active_platform == "local"


def test_apply_unknown_platform_raises(platform_env):
    cfg = load_config(platform_env)
    with pytest.raises(SystemExit):
        apply_platform(cfg, "nope")


def test_key_pool_rotation():
    class Fake:
        def __init__(self, tag):
            self.tag = tag
            self.model = "m"
            self.seen = []

        async def complete(self, system, user):
            self.seen.append(user)
            from booktran.llm.base import LLMResult
            return LLMResult(text="{}", prompt_tokens=1, completion_tokens=1)

        async def aclose(self):
            pass

    a, b = Fake("a"), Fake("b")
    pool = KeyPool([a, b])
    asyncio.run(pool.complete("s", "u1"))
    asyncio.run(pool.complete("s", "u2"))
    asyncio.run(pool.complete("s", "u3"))
    assert a.seen == ["u1", "u3"]
    assert b.seen == ["u2"]
    assert pool.model == "m"


def test_make_provider_builds_key_pool():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_keys = ["k1", "k2"]
    cfg.llm.api_key = "k3"
    provider = make_provider(cfg)
    assert isinstance(provider, KeyPool)
    assert len(provider.providers) == 3
    # 所有 provider 共享同一个限流器 → 全局 RPM
    limiters = {id(p.limiter) for p in provider.providers}
    assert len(limiters) == 1
    asyncio.run(provider.aclose())


def test_make_provider_single_key():
    cfg = AppConfig()
    cfg.llm.provider = "anthropic"
    cfg.llm.api_key = "sk-ant-x"
    provider = make_provider(cfg)
    assert not isinstance(provider, KeyPool)
    asyncio.run(provider.aclose())


def test_local_prompt_preset():
    sys_default = build_system("japanese", "chinese_simplified")
    assert "JSON" in sys_default
    sys_local = build_system("japanese", "chinese_simplified", preset="local")
    assert "JSON" not in sys_local
    assert "序号.译文" in sys_local
    assert "日语" in sys_local and "简体中文" in sys_local
    user_local = build_user(["原文一", "原文二"], preset="local")
    assert "1.原文一" in user_local
    assert "只输出译文行" in user_local


def test_local_preset_keeps_glossary():
    rows = [{"src": "アレン", "dst": "艾伦", "note": ""}]
    s = build_system("japanese", "chinese_simplified", glossary_rows=rows, preset="local")
    assert "アレン|艾伦" in s


def test_manifest_masks_secrets(tmp_path):
    from booktran.config import load_project_manifest, save_project_manifest
    cfg = AppConfig()
    cfg.llm.api_key = "sk-ant-secret"
    cfg.llm.api_keys = ["a", "b"]
    save_project_manifest(tmp_path, tmp_path / "x.txt", cfg)
    data = load_project_manifest(tmp_path)
    assert data["config"]["llm"]["api_key"] == ""
    assert data["config"]["llm"]["api_keys"] == []


def test_cli_init_applies_platform(tmp_path, monkeypatch):
    """init --platform 指定接口档案，快照记录 active_platform 与模型。"""
    from booktran.cli import main
    src = tmp_path / "novel.txt"
    src.write_text("第一段。\n\n第二段。\n", "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "platforms.toml").write_text(PLATFORMS_TOML, "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", tmp_path / "config.toml")
    main(["init", str(src), "--dir", str(proj), "--platform", "local"])
    # v0.5：项目只写**引用**（roles.translate = "local"），模型档案仍在全局
    import tomllib
    pcfg = tomllib.loads((proj / "booktran.toml").read_text("utf-8"))
    assert pcfg["roles"]["translate"] == "local"
    # 项目目录里不落任何模型配置、任何密钥
    for name in ("project.json", "booktran.toml"):
        text = (proj / name).read_text("utf-8")
        assert "qwen2.5:7b" not in text
        assert "sk-ant-test" not in text


def test_cli_translate_uses_routing(tmp_path, monkeypatch):
    """routing.analyze 指向别的档案时，translate 仍走 routing.translate。"""
    from booktran.cli import main
    src = tmp_path / "novel.txt"
    src.write_text("第一段。\n\n第二段。\n", "utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "platforms.toml").write_text(PLATFORMS_TOML, "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", tmp_path / "config.toml")
    main(["init", str(src), "--dir", str(proj)])
    called = {}

    def fake_make_provider(cfg):
        called["provider"] = cfg.llm.provider
        called["model"] = cfg.llm.model
        called["platform"] = cfg.active_platform
        from booktran.llm.base import FakeProvider
        return FakeProvider()

    import booktran.llm as llm_mod
    monkeypatch.setattr(llm_mod, "make_provider", fake_make_provider)
    main(["translate", "--project", str(proj)])
    assert called["platform"] == "claude"
    assert called["provider"] == "anthropic"
    assert called["model"] == "claude-sonnet-4-5"
