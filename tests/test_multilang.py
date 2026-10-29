"""Many-to-many 多语言 + 翻译背景。"""
from __future__ import annotations

import asyncio
import json

from booktran.config import AppConfig, TranslateConfig
from booktran.glossary import Glossary
from booktran.llm.base import FakeProvider
from booktran.model import Segment, SourceDoc, Status
from booktran.pipeline import run_pipeline
from booktran.prompt.builder import build_system, normalize_background
from booktran.store import Store

# ---------- 配置 ----------

def test_target_langs_from_dict():
    tr = TranslateConfig.from_dict({"target_langs": ["english", "japanese"]})
    assert tr.target_langs == ["english", "japanese"]
    assert tr.target_lang == "english"


def test_legacy_target_lang_migrates():
    tr = TranslateConfig.from_dict({"target_lang": "korean"})
    assert tr.target_langs == ["korean"]


def test_ensure_langs():
    cfg = AppConfig()
    cfg.tr.target_langs = []
    cfg.ensure_langs()
    assert cfg.tr.target_langs == ["chinese_simplified"]


def test_lang_passthrough():
    from booktran.prompt.builder import lang_code, lang_display
    assert lang_display("italian") == "意大利语"
    assert lang_display("czech") == "czech"       # 未收录 → 原样
    assert lang_code("czech") == "czech"


# ---------- 提示词背景 ----------

def test_background_in_system_prompt():
    bg = {"era": "维多利亚时代", "characters": "侦探与助手", "naming": "人名保留英文"}
    s = build_system("english", "chinese_simplified", background=bg)
    assert "## 作品背景" in s
    assert "维多利亚时代" in s
    assert "侦探与助手" in s
    assert "人名保留英文" in s
    # 背景不带时提示词里没有该节
    s2 = build_system("english", "chinese_simplified")
    assert "作品背景" not in s2


def test_normalize_background():
    assert normalize_background(None) == {}
    assert normalize_background("纯文本") == {"notes": "纯文本"}
    out = normalize_background({"genre": "科幻", "unknown_field": "x"})
    assert out["genre"] == "科幻"
    assert "unknown_field" in out["notes"]


def test_glossary_background_roundtrip(tmp_path):
    g = Glossary(terms=[{"src": "アレン", "dst": "艾伦", "note": ""}],
                 background={"title": "银色天空", "era": "异世界"})
    p = tmp_path / "glossary.json"
    g.save(p)
    g2 = Glossary.load(p)
    assert g2.background["title"] == "银色天空"
    assert g2.background["era"] == "异世界"
    assert g2.terms[0]["dst"] == "艾伦"


# ---------- 多语言流水线 ----------

def _doc(n=3):
    return SourceDoc(relpath="a.txt", fmt="txt", segments=[
        Segment(sid=f"a.txt#{i}", source=f"第{i}段内容", meta={"line_break": 0})
        for i in range(n)])


def test_pipeline_multi_target(tmp_path):
    cfg = AppConfig()
    cfg.llm.provider = "fake"
    cfg.tr.target_langs = ["chinese_simplified", "english"]
    store = Store(tmp_path)
    docs = [_doc(3)]
    asyncio.run(run_pipeline(docs, FakeProvider(), cfg, store,
                                     __import__("booktran.model", fromlist=["Stats"]).Stats(),
                                     Glossary()))
    zh, en = "chinese_simplified", "english"
    for s in docs[0].segments:
        assert s.is_done(zh) and s.is_done(en)
        assert s.translated_text(zh).startswith("【译】")
        assert s.translated_text(en).startswith("【译】")
    # 快照里两条 track 都在
    state = json.loads((tmp_path / "state.json").read_text("utf-8"))
    t0 = state["docs"][0]["segments"][0]["tracks"]
    assert t0[zh]["status"] == "translated" and t0[en]["status"] == "translated"


def test_pipeline_resume_one_lang_only(tmp_path):
    """已译语言不再请求，只补缺的语言。"""
    cfg = AppConfig()
    cfg.llm.provider = "fake"
    cfg.tr.target_langs = ["chinese_simplified", "english"]
    store = Store(tmp_path)
    docs = [_doc(2)]
    zh = "chinese_simplified"
    # 先手动把 zh 标为已译
    for s in docs[0].segments:
        t = s.track(zh)
        t.target = "已有中文"
        t.status = Status.TRANSLATED

    class Counting(FakeProvider):
        pass

    p = Counting()
    from booktran.model import Stats
    asyncio.run(run_pipeline(docs, p, cfg, store, Stats(), Glossary()))
    for s in docs[0].segments:
        assert s.translated_text(zh) == "已有中文"      # 不被覆盖
        assert s.is_done("english")


def test_source_lang_per_doc():
    """每个文档独立检测源语言。"""
    from booktran.pipeline import _guess_source_lang
    ja = SourceDoc(relpath="ja.txt", fmt="txt", segments=[
        Segment(sid="ja.txt#0", source="これは日本語の文です。")])
    en = SourceDoc(relpath="en.txt", fmt="txt", segments=[
        Segment(sid="en.txt#0", source="This is an English sentence.")])
    assert _guess_source_lang([ja]) == "japanese"
    assert _guess_source_lang([en]) == "english"


def test_store_migrates_legacy_snapshot(tmp_path):
    """旧版单语言 state.json（无 tracks）加载后迁移到指定语言键。"""
    store = Store(tmp_path)
    snap = {"version": 1, "stats": {}, "docs": [{
        "relpath": "a.txt", "fmt": "txt",
        "segments": [{"sid": "a.txt#0", "source": "旧段", "target": "旧译",
                      "status": "translated", "error": None, "model": "m", "meta": {}}],
    }]}
    (tmp_path / "state.json").write_text(json.dumps(snap, ensure_ascii=False), "utf-8")
    docs, _ = store.load(default_lang="english")
    seg = docs[0].segments[0]
    assert seg.is_done("english")
    assert seg.translated_text("english") == "旧译"


def test_glossary_per_lang_dst():
    """dst_<lang> 覆盖：英文目标保留原文名，中文目标用译名。"""
    g = Glossary(terms=[{"src": "Allen", "dst": "艾伦", "dst_english": "Allen", "note": ""}])
    zh = g.matched_rows("Allen walked in.", lang="chinese_simplified")
    en = g.matched_rows("Allen walked in.", lang="english")
    assert zh[0]["dst"] == "艾伦"
    assert en[0]["dst"] == "Allen"
    # 不传 lang 时用默认 dst
    assert g.matched_rows("Allen walked in.")[0]["dst"] == "艾伦"
