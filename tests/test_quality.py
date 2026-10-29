import json

from booktran.chunker import make_chunks
from booktran.model import Status
from booktran.quality import check_chunk, parse_translations


def test_parse_json_ok():
    text = json.dumps({"translations": [{"i": 1, "t": "甲"}, {"i": 2, "t": "乙"}]}, ensure_ascii=False)
    assert parse_translations(text, expect_count=2) == {1: "甲", 2: "乙"}


def test_parse_json_with_prefix_noise():
    text = "好的，以下是翻译：\n{\"translations\":[{\"i\":1,\"t\":\"甲\"}]}"
    assert parse_translations(text, expect_count=1) == {1: "甲"}


def test_parse_json_code_fence():
    text = "```json\n{\"translations\":[{\"i\":1,\"t\":\"甲\\n乙\"}]}\n```"
    assert parse_translations(text, expect_count=1) == {1: "甲\n乙"}


def test_parse_numbered_fallback():
    text = "1.甲\n2.乙\n3.丙"
    assert parse_translations(text, expect_count=3) == {1: "甲", 2: "乙", 3: "丙"}


def test_parse_numbered_multiline():
    text = "1.甲\n第二行\n2.乙"
    assert parse_translations(text, expect_count=2) == {1: "甲\n第二行", 2: "乙"}


def test_parse_rejects_out_of_range():
    text = json.dumps({"translations": [{"i": 99, "t": "幻听"}]}, ensure_ascii=False)
    assert parse_translations(text, expect_count=3) is None


def test_checks_missing_lines():
    errs = check_chunk(["a", "b"], {1: "甲"}, "chinese_simplified")
    assert any("缺少" in e for e in errs)


def test_checks_empty():
    errs = check_chunk(["a"], {1: "  "}, "chinese_simplified")
    assert any("为空" in e for e in errs)


def test_checks_placeholder_lost():
    errs = check_chunk(["保存到 {name} 里"], {1: "存入"}, "chinese_simplified")
    assert any("占位符" in e for e in errs)


def test_checks_placeholder_kept():
    assert check_chunk(["保存到 {name} 里"], {1: "保存到 {name} 中"}, "chinese_simplified") == []


def test_checks_untranslated_identical():
    from booktran.quality import warn_chunk
    # 短标题原样保留 → 合法（无警告）
    assert warn_chunk(["CHAPTER 2"], {1: "CHAPTER 2"}, "chinese_simplified") == []
    # 长句原样保留且无目标语言字符 → 软警告
    warns = warn_chunk(["The quick brown fox jumps over"], {1: "The quick brown fox jumps over"}, "chinese_simplified")
    assert any("相同" in w for w in warns)


def test_make_chunks_respects_budget():
    from conftest import make_doc
    doc = make_doc(10)
    chunks = make_chunks([[s for s in doc.segments if s.status == Status.PENDING]], budget_tokens=1)
    assert len(chunks) == 10  # 预算极小时每段一块


def test_make_chunks_context():
    from conftest import make_doc
    doc = make_doc(6)
    segs = doc.segments
    chunks = make_chunks([segs], budget_tokens=10**9, pre_context_chars=100)
    assert len(chunks) == 1
    assert chunks[0].context_src == []  # 第一块没有上文

    chunks2 = make_chunks([segs], budget_tokens=8, pre_context_chars=10**9)
    assert len(chunks2) > 1
    assert chunks2[1].context_src[0] == segs[0].source  # 上一块的段进入上文
