"""端到端：init → translate(fake) → export，覆盖断点续翻与状态机。"""
import asyncio
import json

from booktran.cli import main


def _run(*argv):
    main(list(argv))


def test_full_flow_txt(txt_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "proj"

    _run("init", str(txt_path), "--dir", str(proj), "--target-lang", "chinese_simplified")
    assert (proj / "project.json").exists()
    # v0.5：项目清单**不再存配置快照**，配置在 booktran.toml（引用 + 差异）
    manifest = json.loads((proj / "project.json").read_text())
    assert "config" not in manifest
    import tomllib
    pcfg = tomllib.loads((proj / "booktran.toml").read_text("utf-8"))
    assert pcfg["project"]["target_langs"] == ["chinese_simplified"]

    _run("translate", "--project", str(proj), "--fake")
    state = json.loads((proj / "state.json").read_text("utf-8"))
    segs = state["docs"][0]["segments"]
    zh = "chinese_simplified"
    assert all(s["tracks"][zh]["status"] == "translated"
               for s in segs if s["status"] != "excluded"), segs

    _run("export", "--project", str(proj))
    out = proj / "output" / "novel_zh.txt"
    text = out.read_text("utf-8")
    assert "【译】第一段。" in text
    # 过滤行原样保留
    assert "..." in text and "123" in text


def test_resume_after_partial(txt_path, tmp_path, monkeypatch):
    """快照里已有部分 translated → translate 只处理 pending。"""
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "proj"
    _run("init", str(txt_path), "--dir", str(proj))

    # 手工把第一段标记为已翻译（模拟中断，旧格式字段）
    state = json.loads((proj / "state.json").read_text("utf-8"))
    zh = "chinese_simplified"
    state["docs"][0]["segments"][0]["status"] = "translated"
    state["docs"][0]["segments"][0].setdefault("tracks", {})[zh] = {
        "target": "人工译文。", "status": "translated", "error": None, "model": "manual"}
    (proj / "state.json").write_text(json.dumps(state, ensure_ascii=False), "utf-8")

    _run("translate", "--project", str(proj), "--fake")
    state2 = json.loads((proj / "state.json").read_text("utf-8"))
    segs = state2["docs"][0]["segments"]
    assert segs[0]["tracks"][zh]["target"] == "人工译文。"  # 不被覆盖
    assert all(s["tracks"][zh]["status"] == "translated"
               for s in segs if s["status"] != "excluded")

    _run("export", "--project", str(proj))
    out = proj / "output" / "novel_zh.txt"
    assert "人工译文。" in out.read_text("utf-8")


def test_epub_flow(epub_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "proj"
    _run("init", str(epub_path), "--dir", str(proj))
    _run("translate", "--project", str(proj), "--fake")
    _run("export", "--project", str(proj), "--bilingual")
    out = proj / "output" / "book_zh.epub"
    bi = proj / "output" / "book_zh_bilingual.epub"
    assert out.exists() and bi.exists()

    from booktran.adapters.epub import EpubReader
    doc = EpubReader().read(out, "book_zh.epub")
    assert all(s.source.startswith("【译】") for s in doc.segments)


def test_pdf_flow_and_custom_prompt(pdf_path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "proj"
    _run("init", str(pdf_path), "--dir", str(proj))
    state = json.loads((proj / "state.json").read_text("utf-8"))
    assert state["docs"][0]["fmt"] == "pdf"

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("自定义：把{source_language}译成{target_language}。", encoding="utf-8")
    _run("translate", "--project", str(proj), "--fake", "--system-prompt-file", str(prompt_file))

    _run("export", "--project", str(proj), "--format", "txt", "--bilingual")
    txt = proj / "output" / "book_zh.txt"
    bi = proj / "output" / "book_zh_bilingual.txt"
    assert txt.exists() and bi.exists()  # 扩展名是 .txt 而非 .pdf

    _run("export", "--project", str(proj), "--format", "epub")
    epub = proj / "output" / "book_zh.epub"
    assert epub.exists()
    import zipfile
    with zipfile.ZipFile(epub) as zf:
        assert "content.opf" in zf.namelist()


def test_custom_prompt_reaches_model(txt_path, tmp_path, monkeypatch):
    """--system-prompt 透传到 build_system（用 fake provider 捕获 user 内容不行，直接查配置链路）。"""
    from booktran.config import AppConfig
    from booktran.glossary import Glossary
    from booktran.pipeline import _resolve_system_prompt
    cfg = AppConfig()
    cfg.tr.system_prompt = "命令行提示词"
    assert _resolve_system_prompt(cfg, Glossary(system_prompt="glossary 提示词")) == "命令行提示词"
    cfg2 = AppConfig()
    assert _resolve_system_prompt(cfg2, Glossary(system_prompt="glossary 提示词")) == "glossary 提示词"
    assert _resolve_system_prompt(cfg2, Glossary()) == ""


def test_failure_then_retry_round(txt_path, tmp_path, monkeypatch):
    """失败块：状态保持 pending，error 记录；下一轮重试成功。"""
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "proj"
    _run("init", str(txt_path), "--dir", str(proj))

    import booktran.pipeline as pipeline
    from booktran.llm.base import FakeProvider

    failing = FakeProvider(fail_first=1)  # 第一次调用失败
    pipeline.make_provider = None  # 防误用

    # 直接调 run_pipeline，注入 failing provider
    from booktran.config import AppConfig
    from booktran.glossary import Glossary
    from booktran.model import Stats
    from booktran.store import Store

    cfg = AppConfig()
    store = Store(proj)
    docs, stats = store.load()
    stats = Stats()

    async def run():
        return await pipeline.run_pipeline(docs, failing, cfg, store, stats, Glossary())

    asyncio.run(run())
    zh = cfg.tr.target_lang
    # 最终全部成功（失败块下一轮重试成功；排除段不算）
    assert all(
        (s.status.value == "excluded") or s.is_done(zh)
        for d in docs for s in d.segments
    )
    assert any(s.track(zh).error is None and s.is_done(zh) for d in docs for s in d.segments)
