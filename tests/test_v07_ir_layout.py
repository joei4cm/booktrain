"""v0.7–v0.8：文档 IR / 段级语言检测 / 排版策略 / 书页预览。"""
from __future__ import annotations

import pytest

from booktran.ir import (
    BlockView,
    StyleSheet,
    apply_stylesheet,
    kind_of_tag,
    spans_from_html,
)
from booktran.langdetect import detect_language, detect_segment_languages
from booktran.layout import get_policy, parse_stylesheet, render_html, write_preview
from booktran.model import Segment, SourceDoc, Status


def _doc(n: int = 3, relpath: str = "book.txt") -> SourceDoc:
    segs = []
    for i in range(n):
        s = Segment(sid=f"{relpath}#{i}", source=f"这是第 {i} 段原文内容，用来测试排版渲染。")
        s.kind = "heading" if i == 0 else "para"
        s.level = 1 if i == 0 else 0
        t = s.track("chinese_simplified")
        t.target = f"这是第 {i} 段译文内容。"
        t.status = Status.TRANSLATED
        segs.append(s)
    return SourceDoc(relpath=relpath, fmt="txt", segments=segs)


# ---------------- IR ----------------

def test_kind_of_tag():
    assert kind_of_tag("h2") == ("heading", 2)
    assert kind_of_tag("p") == ("para", 0)
    assert kind_of_tag("blockquote") == ("quote", 0)
    assert kind_of_tag("unknown") == ("para", 0)


def test_spans_from_html():
    spans = spans_from_html("<p>普通文本 <b>粗体</b> <a href='/x'>链接</a></p>")
    assert spans
    assert any(s["bold"] for s in spans)
    assert any(s["href"] == "/x" for s in spans)
    assert any(s["text"] == "粗体" for s in spans)


def test_block_view_reads_segment():
    seg = Segment(sid="x#0", source="原文", kind="heading", level=2)
    seg.spans = [{"text": "原文", "bold": True}]
    v = BlockView.of(seg)
    assert v.bid == "x#0" and v.kind == "heading" and v.level == 2
    assert v.spans[0].bold is True


def test_stylesheet_from_source():
    doc = _doc(3)
    sheet = StyleSheet.from_source(doc.segments)
    assert 1 in sheet.headings           # 有标题就配标题样式
    assert sheet.headings[1]["font_size"]


def test_stylesheet_merge_model_overrides():
    base = StyleSheet.from_source(_doc(3).segments)
    model = StyleSheet.from_dict({"body": {"font_size": "20px"},
                                  "headings": {"1": {"font_size": "2em"}}})
    merged = base.merged_with(model)
    assert merged.body["font_size"] == "20px"
    assert merged.headings[1]["font_size"] == "2em"


def test_apply_stylesheet():
    doc = _doc(2)
    sheet = StyleSheet.from_source(doc.segments)
    apply_stylesheet(doc, sheet)
    assert doc.segments[0].style.get("font_size")
    assert doc.segments[1].style == {}     # 段落样式为空时不动


# ---------------- 语言检测 ----------------

@pytest.mark.parametrize("text,expect", [
    ("これは最初の段落です。物語が始まります。", "japanese"),
    ("这是简体中文的一段文字，用来测试语言检测。", "chinese_simplified"),
    ("這是繁體中文的一段文字，用來測試語言偵測。", "chinese_traditional"),
    ("이것은 한국어 문장입니다.", "korean"),
    ("The quick brown fox jumps over the lazy dog and it was not the first time.", "english"),
    ("Le renard brun rapide saute par-dessus le chien paresseux, et ce n'était pas la première fois.", "french"),
    ("Der schnelle braune Fuchs springt über den faulen Hund und es war nicht das erste Mal.", "german"),
    ("Это предложение на русском языке для проверки определения языка.", "russian"),
    ("هذه جملة باللغة العربية لاختبار اكتشاف اللغة.", "arabic"),
])
def test_detect_language(text, expect):
    lang, conf = detect_language(text)
    assert lang == expect
    assert 0 < conf <= 1


def test_detect_segment_languages_mixed():
    doc = SourceDoc(relpath="m.txt", fmt="txt", segments=[
        Segment(sid="m#0", source="第一章 启程"),                       # 短段
        Segment(sid="m#1", source="这是中文的第一段话，用来测试段级语言检测是否生效。"),
        Segment(sid="m#2", source="The next paragraph is written in English to test detection."),
        Segment(sid="m#3", source="これは日本語の段落です。"),
    ])
    out = detect_segment_languages([doc])
    assert out["m#1"] == "chinese_simplified"
    assert out["m#2"] == "english"
    assert out["m#3"] == "japanese"
    # 短段跟文档主流语言（中文），而不是整本猜测
    assert out["m#0"] == "chinese_simplified"


# ---------------- 排版策略 ----------------

def test_parse_stylesheet_tolerates_noise():
    raw = '```json\n{"body": {"font_size": "19px"}}\n```'
    sheet = parse_stylesheet(raw)
    assert sheet is not None and sheet.body["font_size"] == "19px"
    assert parse_stylesheet("完全不是 JSON") is None


def test_original_policy_plan_and_render():
    import asyncio
    doc = _doc(3)
    policy = get_policy("original")
    sheet = asyncio.run(policy.plan(doc, lang="chinese_simplified"))
    html = policy.render(doc, lang="chinese_simplified", sheet=sheet)
    assert "<h1>" in html
    assert "译文内容" in html


def test_render_html_shows_source_when_pending():
    doc = _doc(1)
    doc.segments[0].track("chinese_simplified").target = ""
    doc.segments[0].track("chinese_simplified").status = Status.PENDING
    html = render_html(doc, lang="chinese_simplified")
    assert "原文" in html
    assert "pending" in html


def test_write_preview_artifact(tmp_path):
    doc = _doc(2)
    path = write_preview(tmp_path, doc, lang="chinese_simplified")
    assert path.exists()
    assert path.parent.name == "preview"
    assert "译文内容" in path.read_text("utf-8")


def test_model_policy_falls_back_to_original():
    """模型不可用时，样式表回退到继承（不能因此排坏一本书）。"""
    import asyncio
    doc = _doc(3)
    policy = get_policy("model", router=None)
    sheet = asyncio.run(policy.plan(doc, lang="chinese_simplified"))
    assert sheet.body["line_height"] == "1.9"


def test_hybrid_policy_keeps_anchors():
    import asyncio
    doc = _doc(2)
    doc.segments[0].anchors = [{"page": 1, "bbox": [0, 0, 100, 20]}]
    policy = get_policy("hybrid", router=None)
    sheet = asyncio.run(policy.plan(doc, lang="chinese_simplified"))
    html = policy.render(doc, lang="chinese_simplified", sheet=sheet)
    assert "<article" in html


# ---------------- CLI / Web 预览 ----------------

def test_cli_preview_writes_artifact(tmp_path, txt_path):
    from booktran.cli import main
    proj = tmp_path / "proj"
    main(["init", str(txt_path), "--dir", str(proj), "--fake"])
    main(["run", "-p", str(proj), "--fake", "--yes"])
    main(["preview", "-p", str(proj)])
    files = list((proj / "artifacts" / "preview").glob("*.html"))
    assert files
    assert "译" in files[0].read_text("utf-8")


def test_web_preview_endpoint(tmp_path, txt_path):
    import asyncio

    import httpx

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
    app = build_app(root)

    async def _flow(ac):
        r = await ac.get("/api/project/novel/preview/chinese_simplified")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        return r.text

    html = asyncio.run(_flow(httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")))
    assert "<article" in html
