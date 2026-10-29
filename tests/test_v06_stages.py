"""v0.6：阶段状态维度 / 校对阶段 / 质检报告 / SSE 事件流。"""
from __future__ import annotations

import asyncio
import json

from booktran.engine import Engine, Job, Plan
from booktran.glossary import Glossary
from booktran.llm.base import FakeProvider
from booktran.model import Segment, SourceDoc, StageState, Status
from booktran.pipeline import make_router, run_pipeline
from booktran.quality.qa import check_segment
from booktran.quality.report import Issue, QualityReport
from booktran.store import Store


def _seg(sid: str, src: str, target: str = "", lang: str = "chinese_simplified") -> Segment:
    s = Segment(sid=sid, source=src)
    t = s.track(lang)
    t.target = target
    t.status = Status.TRANSLATED if target else Status.PENDING
    return s


# ---------------- 阶段状态维度 ----------------

def test_track_stage_dimension():
    t = Segment(sid="x", source="a").track("english")
    assert t.stage("translate") == StageState.PENDING.value
    t.set_stage("translate", StageState.DONE)
    t.set_stage("proofread", StageState.DONE)
    # 上游改动 → 下游 stale（数据保留）
    touched = t.invalidate_downstream("translate", ["translate", "proofread", "typeset"])
    assert touched == ["proofread"]
    assert t.stages["proofread"] == StageState.STALE.value
    assert t.stages["translate"] == StageState.DONE.value


def test_track_stages_persist(tmp_path):
    seg = _seg("a#0", "原文", "译文")
    seg.track("chinese_simplified").set_stage("translate", StageState.DONE)
    docs = [SourceDoc(relpath="a.txt", fmt="txt", segments=[seg])]
    store = Store(tmp_path)
    store.save_snapshot(docs, __import__("booktran.model", fromlist=["Stats"]).Stats())
    data = json.loads((tmp_path / "state.json").read_text("utf-8"))
    st = data["docs"][0]["segments"][0]["tracks"]["chinese_simplified"]
    assert st["stages"]["translate"] == "done"


# ---------------- 质检规则 ----------------

def test_qa_detects_glossary_mismatch():
    glossary = Glossary(terms=[{"src": "魔导器", "dst": "魔导器", "note": ""}])
    seg = _seg("a#0", "他推开了那扇门，魔导器在手中嗡鸣。", "他推开了那扇门，导魔器在手中嗡鸣。")
    issues = check_segment(seg, "chinese_simplified", glossary)
    kinds = {i.kind for i in issues}
    assert "glossary" in kinds
    issue = next(i for i in issues if i.kind == "glossary")
    assert issue.suggestion == "魔导器"


def test_qa_detects_untranslated():
    src = "这一段文字足够长，应该能够触发疑似未翻译的检查规则。"
    seg = _seg("a#1", src, src)
    issues = check_segment(seg, "chinese_simplified", Glossary())
    assert any(i.kind == "untranslated" and i.severity == "error" for i in issues)


def test_qa_detects_placeholder_loss():
    seg = _seg("a#2", "你好 {{name}}，欢迎。", "你好，欢迎。")
    issues = check_segment(seg, "chinese_simplified", Glossary())
    assert any(i.kind == "placeholder" and i.severity == "error" for i in issues)


def test_qa_detects_numbering_residue():
    seg = _seg("a#3", "原文一句话。", "1. 译文一句话。")
    issues = check_segment(seg, "chinese_simplified", Glossary())
    assert any(i.kind == "numbering" for i in issues)


def test_qa_detects_empty_translation():
    seg = _seg("a#4", "原文", "")
    seg.track("chinese_simplified").status = Status.TRANSLATED
    issues = check_segment(seg, "chinese_simplified", Glossary())
    assert any(i.kind == "empty" and i.severity == "error" for i in issues)


def test_qa_detects_non_translate_missing():
    glossary = Glossary(non_translate=["DoorHandle"])
    seg = _seg("a#5", "推开了 DoorHandle。", "推开了门把手。")
    issues = check_segment(seg, "chinese_simplified", glossary)
    assert any(i.kind == "non_translate" for i in issues)


# ---------------- 报告 ----------------

def test_quality_report_save_load(tmp_path):
    report = QualityReport([Issue(sid="a#0", lang="zh", kind="glossary",
                                   severity="warn", message="术语不符")])
    j, m = report.save(tmp_path)
    assert j.exists() and m.exists()
    loaded = QualityReport.load(tmp_path)
    assert len(loaded.issues) == 1
    assert loaded.issues[0].kind == "glossary"
    assert "术语不符" in loaded.to_markdown()


def test_quality_report_summary():
    report = QualityReport([
        Issue(sid="a", lang="zh", kind="glossary", severity="warn"),
        Issue(sid="b", lang="zh", kind="empty", severity="error"),
        Issue(sid="c", lang="en", kind="glossary", severity="warn"),
    ])
    s = report.summary()
    assert s["total"] == 3
    assert s["by_severity"]["error"] == 1
    assert s["by_kind"]["glossary"] == 2
    assert s["by_lang"]["zh"] == 2


# ---------------- 引擎 ----------------

def test_engine_runs_stages_in_order(tmp_path):
    seg = _seg("a#0", "第一段原文内容。", "第一段译文内容。")
    docs = [SourceDoc(relpath="a.txt", fmt="txt", segments=[seg])]
    store = Store(tmp_path)
    from booktran.config import AppConfig
    from booktran.model import Stats
    from booktran.progress import Progress
    from booktran.ui import Term
    cfg = AppConfig()
    cfg.tr.target_langs = ["chinese_simplified"]
    job = Job(
        docs=docs, cfg=cfg, store=store, stats=Stats(),
        glossary=Glossary(terms=[{"src": "原文", "dst": "原文", "note": ""}]),
        router=make_router(cfg, FakeProvider()),
        progress=Progress(Term()),
        project_dir=tmp_path,
        plan=Plan(stages=["translate", "proofread"], proofread_mode="qa"),
    )
    result = asyncio.run(Engine().run_job(job))
    assert [s.stage for s in result.stages] == ["translate", "proofread"]
    assert result.ok
    # 校对阶段写出了报告
    assert (tmp_path / "quality_report.json").exists()
    # 阶段状态落进 track
    assert seg.track("chinese_simplified").stages.get("translate") == "done"
    assert seg.track("chinese_simplified").stages.get("proofread") == "done"


def test_run_pipeline_with_stages(tmp_path):
    from booktran.config import AppConfig
    from booktran.model import Stats
    seg = _seg("a#0", "第一段原文内容。", "第一段译文内容。")
    docs = [SourceDoc(relpath="a.txt", fmt="txt", segments=[seg])]
    store = Store(tmp_path)
    cfg = AppConfig()
    cfg.tr.target_langs = ["chinese_simplified"]
    stats = asyncio.run(run_pipeline(docs, FakeProvider(), cfg, store, Stats(),
                                     Glossary(), stages=["translate", "proofread"],
                                     project_dir=tmp_path))
    assert stats.requests == 0    # 已是译文，无需再翻
    assert (tmp_path / "quality_report.json").exists()


# ---------------- 网页端：SSE 与质检 ----------------

def _web_app(tmp_path, txt_path):
    """建一个带项目的网页端应用（fake provider）。"""
    import booktran.config as config_mod
    from booktran.cli import main
    from booktran.web import build_app
    home = tmp_path / "home"
    home.mkdir()
    config_mod.GLOBAL_CONFIG_PATH = home / "config.toml"
    root = tmp_path / "projects"
    root.mkdir()
    main(["init", str(txt_path), "--dir", str(root / "novel"), "--fake"])
    proj = root / "novel"
    (proj / "profiles.toml").write_text(
        '[profile.fake]\nkind = "chat"\nprovider = "fake"\nmodel = "fake"\n', "utf-8")
    cfgp = proj / "booktran.toml"
    text = cfgp.read_text("utf-8") if cfgp.exists() else ""
    if "[roles]" in text:
        text = text.replace("[roles]", '[roles]\ntranslate = "fake"', 1)
    else:
        text += '\n[roles]\ntranslate = "fake"\n'
    cfgp.write_text(text, "utf-8")
    return build_app(root), proj


def test_web_proofread_endpoint(tmp_path, txt_path):
    import asyncio

    import httpx
    app, proj = _web_app(tmp_path, txt_path)

    async def _flow(ac):
        r = await ac.post("/api/project/novel/proofread", json={"mode": "qa"})
        assert r.status_code == 200 and r.json()["ok"]
        for _ in range(50):
            s = (await ac.get("/api/project/novel")).json()
            if s["task"]["state"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        assert s["task"]["state"] == "done", s["task"]
        q = (await ac.get("/api/project/novel/quality")).json()
        assert "summary" in q and "issues" in q
        return q

    asyncio.run(_flow(httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")))
    assert (proj / "quality_report.json").exists()


def test_web_sse_stream(tmp_path, txt_path):
    import asyncio

    import httpx
    app, proj = _web_app(tmp_path, txt_path)

    async def _flow(ac):
        events = []
        async with ac.stream("GET", "/api/project/novel/stream?timeout=1") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
        return events

    events = asyncio.run(_flow(httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")))
    assert "state" in events


def test_web_quality_apply(tmp_path, txt_path):
    import asyncio

    import httpx
    app, proj = _web_app(tmp_path, txt_path)
    segs = json.loads((proj / "state.json").read_text("utf-8"))["docs"][0]["segments"]
    target = next((s for s in segs if s["status"] != "excluded"), None)
    assert target is not None

    async def _flow(ac):
        r = await ac.post("/api/project/novel/quality/apply",
                           json={"sid": target["sid"], "lang": "chinese_simplified",
                                 "target": "人工改写的译文。"})
        assert r.status_code == 200
        data = (await ac.get("/api/project/novel/segments?limit=50")).json()
        item = next(i for i in data["items"] if i["sid"] == target["sid"])
        return item

    item = asyncio.run(_flow(httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")))
    assert item["tracks"]["chinese_simplified"]["target"] == "人工改写的译文。"
