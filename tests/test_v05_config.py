"""v0.5：配置引用化 / 角色路由 / 费用记账 / doctor / 成本闸。"""
from __future__ import annotations

import json

import pytest

import booktran.config as config_mod
from booktran.cli import main
from booktran.config import (
    Profile,
    load_config,
    resolve_task_platform,
    write_project_config,
)
from booktran.llm.router import ModelRouter, RouteContext
from booktran.llm.usage import Usage, UsageLog, price_of

PROFILES_TOML = """
[profile.claude]
kind = "chat"
provider = "anthropic"
model = "claude-sonnet-4-5"
key_env = "ANTHROPIC_API_KEY"
price_in = 3.0
price_out = 15.0
concurrency = 8

[profile.cheap]
kind = "chat"
provider = "openai"
model = "qwen2.5:7b"
price_in = 0.0
price_out = 0.0

[profile.vision]
kind = "vision"
provider = "openai"
model = "gemini-2.5-pro"
price_in = 1.25
price_out = 10.0

[roles]
translate = "claude"
ocr = "cheap"
vlm = "vision"

[roles.translate_cascade]
default = "claude"
fallback = "cheap"
rules = [{ match = { chars_gt = 400 }, use = "claude" }]

[budget]
max_cost = 5.0
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """隔离的全局配置目录。"""
    d = tmp_path / "home"
    d.mkdir()
    (d / "config.toml").write_text("", "utf-8")
    (d / "profiles.toml").write_text(PROFILES_TOML, "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", d / "config.toml")
    return d


# ---------------- 配置优先级与"不冻结" ----------------

def test_global_profiles_loaded(home):
    cfg = load_config(None)
    assert set(cfg.profiles) == {"claude", "cheap", "vision"}
    assert cfg.profiles["claude"].model == "claude-sonnet-4-5"
    assert cfg.profiles["claude"].price_in == 3.0
    assert cfg.roles["translate"].target == "claude"
    assert cfg.budget.max_cost == 5.0


def test_project_only_writes_differences(home, tmp_path):
    """项目文件只写引用与差异：改全局档案，所有项目立刻跟随。"""
    proj = tmp_path / "proj"
    cfg = load_config(None)
    cfg.tr.target_langs = ["chinese_simplified", "english"]
    write_project_config(proj, cfg, roles={"translate": "claude"})

    text = (proj / "booktran.toml").read_text("utf-8")
    assert "claude-sonnet-4-5" not in text      # 不复制模型配置
    assert "price_in" not in text               # 不复制价格
    assert 'translate = "claude"' in text       # 只写引用

    pcfg = load_config(proj)
    assert pcfg.tr.target_langs == ["chinese_simplified", "english"]
    assert pcfg.profiles["claude"].model == "claude-sonnet-4-5"
    assert pcfg.roles["translate"].target == "claude"


def test_global_change_propagates_to_existing_project(home, tmp_path):
    """**配置不冻结**：全局档案改了，既有项目下次运行即生效。"""
    proj = tmp_path / "proj"
    cfg = load_config(None)
    write_project_config(proj, cfg, roles={"translate": "claude"})
    assert load_config(proj).profiles["claude"].model == "claude-sonnet-4-5"

    # 改全局档案
    (home / "profiles.toml").write_text(
        PROFILES_TOML.replace('model = "claude-sonnet-4-5"', 'model = "claude-opus-4-1"'),
        "utf-8")
    assert load_config(proj).profiles["claude"].model == "claude-opus-4-1"


def test_project_role_overrides_global(home, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "booktran.toml").write_text(
        '[roles]\ntranslate = "cheap"\n', "utf-8")
    cfg = load_config(proj)
    assert cfg.roles["translate"].target == "cheap"
    assert resolve_task_platform(cfg, "translate") == "cheap"
    # 其他角色仍走全局
    assert resolve_task_platform(cfg, "ocr") == "cheap"


def test_legacy_platforms_toml_still_works(tmp_path, monkeypatch):
    """v0.4 的 platforms.toml 继续可用（兼容层）。"""
    d = tmp_path / "home"
    d.mkdir()
    (d / "config.toml").write_text("", "utf-8")
    (d / "platforms.toml").write_text(
        '[platforms.claude]\nprovider = "anthropic"\nmodel = "m1"\n\n'
        '[routing]\ntranslate = "claude"\n', "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", d / "config.toml")
    cfg = load_config(None)
    assert cfg.profiles["claude"].model == "m1"
    assert resolve_task_platform(cfg, "translate") == "claude"


def test_apply_platform_keeps_unset_fields(home):
    """档案未定义的字段 → 保持原值（字段级合并）。"""
    cfg = load_config(None)
    cfg.llm.concurrency = 2
    cfg.llm.max_output_tokens = 4096
    from booktran.config import apply_platform
    apply_platform(cfg, "cheap")
    assert cfg.llm.provider == "openai"
    assert cfg.llm.concurrency == 2
    assert cfg.llm.max_output_tokens == 4096


# ---------------- 角色路由与级联 ----------------

def test_router_picks_role_profile(home):
    cfg = load_config(None)
    router = ModelRouter(cfg)
    assert router.pick("translate").id == "claude"
    assert router.pick("ocr").id == "cheap"
    assert router.pick("vlm").id == "vision"


def test_router_cascade_rule(home):
    cfg = load_config(None)
    router = ModelRouter(cfg)
    # 长段 → 命中级联规则
    p = router.pick("translate", RouteContext(chars=800, lang="chinese_simplified"))
    assert p.id == "claude"
    # 规则未命中 → 级联默认
    p2 = router.pick("translate", RouteContext(chars=10))
    assert p2.id == "claude"


def test_router_fallback_on_failure(home):
    """主档案失败 → 按 fallback 降级重试。"""
    cfg = load_config(None)
    calls: list[str] = []

    class Boom:
        model = "boom"

        async def complete(self, system, user):
            calls.append("boom")
            raise RuntimeError("boom")

        async def aclose(self):
            pass

    class Ok:
        model = "ok"

        async def complete(self, system, user):
            calls.append("ok")
            from booktran.llm.base import LLMResult
            return LLMResult(text="ok", prompt_tokens=10, completion_tokens=5)

        async def aclose(self):
            pass

    def factory(profile):
        return Boom() if profile.id == "claude" else Ok()

    router = ModelRouter(cfg, factory=factory)
    import asyncio

    async def _go():
        res, prof = await router.run("translate", "s", "u")
        return res, prof

    res, prof = asyncio.run(_go())
    assert prof.id == "cheap"
    assert calls == ["boom", "ok"]
    assert res.text == "ok"


# ---------------- 费用记账 ----------------

def test_price_of_unpriced_is_none():
    p = Profile(id="x", price_in=None, price_out=None)
    assert price_of(p, 1000, 1000) is None


def test_price_of_computes():
    p = Profile(id="x", price_in=3.0, price_out=15.0)
    cost = price_of(p, 1_000_000, 1_000_000)
    assert cost == pytest.approx(18.0)


def test_usage_log_aggregates(tmp_path):
    log = UsageLog(tmp_path)
    log.append(Usage(role="translate", profile="claude", model="m", stage="translate",
                     lang="zh", prompt_tokens=1000, completion_tokens=100, cost=0.5))
    log.append(Usage(role="translate", profile="cheap", model="m2", stage="translate",
                     lang="zh", prompt_tokens=1000, completion_tokens=100, cost=0.1))
    log.append(Usage(role="analyze", profile="cheap", model="m2", stage="analyze",
                     lang="", prompt_tokens=10, completion_tokens=1, cost=None))
    agg = log.aggregate()
    assert agg["total"]["cost"] == pytest.approx(0.6)
    assert agg["total"]["unpriced"] == 1
    assert agg["by_role"]["translate"]["cost"] == pytest.approx(0.6)
    assert agg["by_stage"]["analyze"]["unpriced"] == 1


# ---------------- CLI：doctor / cost / profile / role ----------------

def test_doctor_reports_profiles(home, capsys):
    main(["doctor"])
    out = capsys.readouterr().out
    assert "模型档案" in out
    assert "claude" in out
    assert "角色路由" in out


def test_profile_add_and_list(home, capsys):
    main(["profile", "add", "newone", "--provider", "openai", "--model", "m-x",
          "--price-in", "1.5", "--price-out", "7.5"])
    out = capsys.readouterr().out
    assert "newone" in out
    main(["profile", "list"])
    out = capsys.readouterr().out
    assert "newone" in out and "m-x" in out
    cfg = load_config(None)
    assert cfg.profiles["newone"].price_in == 1.5


def test_role_set(home, capsys):
    main(["role", "set", "translate=cheap"])
    cfg = load_config(None)
    assert cfg.roles["translate"].target == "cheap"


def test_cost_command(tmp_path, capsys):
    proj = tmp_path / "proj"
    proj.mkdir()
    log = UsageLog(proj)
    log.append(Usage(role="translate", profile="claude", model="m", stage="translate",
                     lang="zh", prompt_tokens=1000, completion_tokens=100, cost=1.25))
    main(["cost", "-p", str(proj)])
    out = capsys.readouterr().out
    assert "$1.25" in out
    assert "按阶段" in out


def test_status_reports_cost(tmp_path, capsys, monkeypatch):
    d = tmp_path / "home"
    d.mkdir()
    (d / "config.toml").write_text("", "utf-8")
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", d / "config.toml")
    src = tmp_path / "a.txt"
    src.write_text("第一段。\n", "utf-8")
    proj = tmp_path / "proj"
    main(["init", str(src), "--dir", str(proj), "--fake"])
    UsageLog(proj).append(Usage(role="translate", profile="fake", model="fake",
                                 stage="translate", lang="chinese_simplified",
                                 prompt_tokens=10, completion_tokens=2, cost=0.02))
    main(["status", "-p", str(proj)])
    out = capsys.readouterr().out
    assert "$0.02" in out


# ---------------- 成本闸 ----------------

def test_dry_run_makes_no_request(home, tmp_path, capsys):
    """--dry-run 只做试算，不发起任何请求。"""
    src = tmp_path / "a.txt"
    src.write_text("第一段。\n\n第二段。\n", "utf-8")
    proj = tmp_path / "proj"
    main(["init", str(src), "--dir", str(proj), "--platform", "claude"])
    before = json.loads((proj / "state.json").read_text("utf-8"))
    main(["run", "-p", str(proj), "--dry-run"])
    out = capsys.readouterr().out
    assert "预计费用" in out
    after = json.loads((proj / "state.json").read_text("utf-8"))
    # 状态未变（没有任何段落被翻译）
    assert before["docs"] == after["docs"]
    assert not (proj / "usage.jsonl").exists()


def test_run_creates_usage_log(home, tmp_path):
    """跑完一次 → usage.jsonl 有记录，且项目目录里没有密钥。"""
    src = tmp_path / "a.txt"
    src.write_text("第一段。\n\n第二段。\n", "utf-8")
    proj = tmp_path / "proj"
    main(["init", str(src), "--dir", str(proj), "--fake"])
    main(["run", "-p", str(proj), "--fake", "--yes"])
    assert (proj / "usage.jsonl").exists()
    entries = [json.loads(line) for line in
               (proj / "usage.jsonl").read_text("utf-8").splitlines() if line.strip()]
    assert entries and entries[0]["role"] == "translate"
    # 密钥不落项目
    for f in proj.rglob("*"):
        if f.is_file():
            assert "sk-ant" not in f.read_text("utf-8", errors="ignore")


def test_project_manifest_has_no_config_snapshot(home, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("第一段。\n", "utf-8")
    proj = tmp_path / "proj"
    main(["init", str(src), "--dir", str(proj), "--fake"])
    manifest = json.loads((proj / "project.json").read_text("utf-8"))
    assert "config" not in manifest
    assert manifest["source"]
