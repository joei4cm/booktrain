"""网页端 API 测试（TestClient，fake provider 跑真实后台任务）。"""
import asyncio
import json
from pathlib import Path
from urllib.parse import quote

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from booktran.cli import main  # noqa: E402
from booktran.web import build_app  # noqa: E402


def use_fake(project_dir: Path) -> None:
    """让项目用假 provider：项目级 profiles.toml 定义档案，booktran.toml 引用它。

    v0.5 之后模型是**引用**：项目不再持有 provider 字段，档案可定义在项目或全局。
    """
    (project_dir / "profiles.toml").write_text(
        '[profile.fake]\nkind = "chat"\nprovider = "fake"\nmodel = "fake"\n', "utf-8")
    cfg_path = project_dir / "booktran.toml"
    text = cfg_path.read_text("utf-8") if cfg_path.exists() else ""
    if "[roles]" in text:
        text = text.replace("[roles]", '[roles]\ntranslate = "fake"', 1)
    else:
        text += '\n[roles]\ntranslate = "fake"\n'
    cfg_path.write_text(text, "utf-8")


@pytest.fixture
def projects_root(txt_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 隔离全局配置与密钥目录（测试不得写进真实 ~/.config）
    import booktran.config as config_mod
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", fake_home / "config.toml")
    root = tmp_path / "projects"
    root.mkdir()
    main(["init", str(txt_path), "--dir", str(root / "novel"), "--fake"])
    use_fake(root / "novel")
    return root


@pytest.fixture
def app(projects_root):
    return build_app(projects_root)


@pytest.fixture
def client(app):
    return TestClient(app)


def run_async(app, fn):
    """在测试自己的事件循环里跑应用。

    Starlette 的 TestClient 每次请求都会新建 anyio portal 并在请求结束时取消
    该作用域——请求内 create_task 出来的后台任务会被连带取消。后台作业相关
    的用例改用 ASGITransport（没有请求作用域），任务才能真正跑完。
    """
    async def _main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await fn(ac)
    return asyncio.run(_main())


def test_list_projects(client):
    r = client.get("/api/projects")
    assert r.status_code == 200
    projects = r.json()
    assert len(projects) == 1
    p = projects[0]
    assert p["id"] == "novel"
    assert p["progress"]["pending"] == 3
    assert p["task"]["state"] == "idle"


def test_segments_and_edit(client):
    r = client.get("/api/project/novel/segments?limit=10")
    data = r.json()
    items = data["items"]
    zh = "chinese_simplified"
    assert data["langs"] == [zh]
    assert len(items) == 5  # 含 2 个 excluded
    pend = [i for i in items if i["status"] == "pending"]
    sid = quote(pend[0]["sid"], safe="")  # sid 含 . 和 #，必须编码
    r = client.put(f"/api/project/novel/segment/{sid}",
                   json={"target": "人工译文。", "lang": zh})
    assert r.status_code == 200
    r = client.get("/api/project/novel/segments?limit=10")
    edited = [i for i in r.json()["items"] if i["sid"] == pend[0]["sid"]][0]
    assert edited["tracks"][zh]["target"] == "人工译文。"
    assert edited["tracks"][zh]["status"] == "translated"
    # 段级 status 反映「至少一个语言已译」
    assert edited["status"] == "translated"


def test_glossary_roundtrip(client):
    g = {"terms": [{"src": "第一段", "dst": "第一段译文", "note": ""}],
         "non_translate": [], "style": "测试风格", "notes": "", "system_prompt": ""}
    r = client.put("/api/project/novel/glossary", json=g)
    assert r.status_code == 200
    r = client.get("/api/project/novel/glossary")
    assert r.json()["terms"][0]["dst"] == "第一段译文"


def test_glossary_per_lang_and_background(client, tmp_path):
    """每语言译名 dst_<lang> 与翻译背景都要能存回 glossary.json。"""
    g = {"terms": [{"src": "Allen", "dst": "艾伦", "dst_english": "Allen", "note": "人物：主角"}],
         "non_translate": [], "style": "", "notes": "", "system_prompt": "",
         "background": {"title": "银色天空", "era": "异世界"}}
    r = client.put("/api/project/novel/glossary", json=g)
    assert r.status_code == 200
    r = client.get("/api/project/novel/glossary")
    data = r.json()
    assert data["terms"][0]["dst_english"] == "Allen"
    assert data["background"]["title"] == "银色天空"
    # 落盘后仍是合法 glossary.json（可被流水线读取）
    saved = json.loads((tmp_path / "projects" / "novel" / "glossary.json").read_text("utf-8"))
    assert saved["terms"][0]["dst_english"] == "Allen"
    assert saved["background"]["era"] == "异世界"


def test_translate_task_and_export(app, tmp_path):
    async def _flow(ac):
        r = await ac.post("/api/project/novel/translate", json={})
        assert r.status_code == 200 and r.json()["ok"]
        s = {}
        for _ in range(50):
            s = (await ac.get("/api/project/novel")).json()
            if s["task"]["state"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert s["task"]["state"] == "done", s["task"]
        assert s["progress"]["pending"] == 0
        r = await ac.post("/api/project/novel/export", json={"bilingual": True})
        files = r.json()["files"]
        assert len(files) == 2
        out_dir = tmp_path / "projects" / "novel" / "output"
        assert all((out_dir / f).exists() for f in files)
        r = await ac.get("/api/project/novel/events")
        assert len(r.json()["events"]) > 0
        return s
    s = run_async(app, _flow)
    assert s["cost"]["requests"] >= 1


def test_unknown_project_404(client):
    assert client.get("/api/project/nope").status_code == 404
    assert client.get("/api/project/nope/segments").status_code == 404


# ---------- 上传建项目 ----------

def test_upload_txt(tmp_path, txt_path):
    projects_root = tmp_path / "up"
    projects_root.mkdir()
    c = TestClient(build_app(projects_root))
    with open(txt_path, "rb") as f:
        r = c.post("/api/projects",
                   files={"file": ("novel.txt", f, "text/plain")},
                   data={"target_langs": "chinese_simplified,english", "fake": "true"})
    assert r.status_code == 200
    p = r.json()
    assert p["target_langs"] == ["chinese_simplified", "english"]
    assert p["progress"]["total"] == 3  # 3 段可翻译
    # 源文件已存进项目目录
    src = projects_root / p["id"] / "source" / "novel.txt"
    assert src.exists()
    # 列表里也能看到
    r = c.get("/api/projects")
    assert any(x["id"] == p["id"] for x in r.json())


def test_upload_bad_extension(tmp_path):
    projects_root = tmp_path / "up"
    projects_root.mkdir()
    c = TestClient(build_app(projects_root))
    r = c.post("/api/projects",
               files={"file": ("doc.docx", b"fake", "application/octet-stream")},
               data={"target_langs": "chinese_simplified"})
    assert r.status_code == 400


def test_delete_project(client, tmp_path):
    r = client.delete("/api/project/novel")
    assert r.status_code == 200
    assert not (tmp_path / "projects" / "novel").exists()
    assert client.get("/api/project/novel").status_code == 404


# ---------- 设置 ----------

def test_settings_roundtrip(client, tmp_path):
    r = client.get("/api/project/novel/settings")
    assert r.status_code == 200
    s = r.json()
    assert s["llm"]["provider"] == "anthropic"
    assert s["translate"]["target_langs"] == ["chinese_simplified"]
    assert "api_key" not in s["llm"]  # 绝不回传明文密钥

    r = client.put("/api/project/novel/settings", json={
        "target_langs": ["english", "japanese"],
        "chunk_budget_tokens": 2000,
        "rounds": 3,
        "zh_convert": "s2t",
        "role_profile": "fake",
    })
    assert r.status_code == 200
    s = r.json()
    assert s["translate"]["target_langs"] == ["english", "japanese"]
    assert s["translate"]["chunk_budget_tokens"] == 2000
    assert s["translate"]["zh_convert"] == "s2t"
    # 项目只写引用与差异（模型配置属全局档案）
    import tomllib
    pcfg = tomllib.loads((tmp_path / "projects" / "novel" / "booktran.toml").read_text("utf-8"))
    assert pcfg["roles"]["translate"] == "fake"
    assert pcfg["project"]["target_langs"] == ["english", "japanese"]


def test_settings_no_plaintext_key(client, tmp_path, monkeypatch):
    """密钥写加密存储；项目目录里一个字节都不许有明文。"""
    import booktran.config.secrets as secrets_mod
    # 隔离加密存储到测试目录
    fake_dir = tmp_path / "secrets"
    fake_dir.mkdir()
    monkeypatch.setattr(secrets_mod, "_KEYS_DIR", fake_dir)
    monkeypatch.setattr(secrets_mod, "_ENC_PATH", fake_dir / "keys.enc")
    monkeypatch.setattr(secrets_mod, "_MASTER_PATH", fake_dir / ".master_key")

    r = client.put("/api/project/novel/settings",
                   json={"api_key": "sk-test-123", "provider": "anthropic"})
    assert r.status_code == 200
    proj = tmp_path / "projects" / "novel"
    for f in proj.rglob("*"):
        if f.is_file():
            assert "sk-test-123" not in f.read_text("utf-8", errors="ignore"), f
    # 加密存储里有（密文，不是明文）
    enc_file = fake_dir / "keys.enc"
    assert enc_file.exists()
    assert b"sk-test-123" not in enc_file.read_bytes()  # 密文不含明文
    # 解密后能取到
    from booktran.config.secrets import get_key
    assert get_key("anthropic") == "sk-test-123"
    # GET settings 不回传明文
    s = client.get("/api/project/novel/settings").json()
    assert s["llm"]["has_api_key"] is True


# ---------- 术语分析 ----------

def test_analyze_fake(app, tmp_path):
    async def _flow(ac):
        await ac.post("/api/project/novel/reload")
        r = await ac.post("/api/project/novel/analyze", json={})
        assert r.status_code == 200 and r.json()["ok"]
        s = {}
        for _ in range(50):
            s = (await ac.get("/api/project/novel")).json()
            if s["task"]["state"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert s["task"]["state"] == "done", s["task"]
    run_async(app, _flow)
    assert (tmp_path / "projects" / "novel" / "glossary.json").exists()


# ---------- 导出下载 ----------

def test_export_and_download(app, tmp_path):
    async def _flow(ac):
        await ac.post("/api/project/novel/reload")
        await ac.post("/api/project/novel/translate", json={})
        for _ in range(50):
            s = (await ac.get("/api/project/novel")).json()
            if s["task"]["state"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        r = await ac.post("/api/project/novel/export", json={"bilingual": True})
        files = r.json()["files"]
        assert len(files) == 2
        # 列表
        r = await ac.get("/api/project/novel/files")
        assert r.status_code == 200
        assert len(r.json()) == 2
        # 下载
        r = await ac.get(f"/api/project/novel/file/{quote(files[0])}")
        assert r.status_code == 200
        assert len(r.content) > 0
        # 路径穿越
        r = await ac.get("/api/project/novel/file/../../project.json")
        assert r.status_code == 404
        return files
    run_async(app, _flow)
