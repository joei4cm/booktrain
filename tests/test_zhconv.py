"""简繁转换（OpenCC）与 analyze 第二阶段的 LLM 合并。"""
from __future__ import annotations

import json

import pytest

from booktran.glossary import merge_candidates
from booktran.model import SourceDoc, Status
from booktran.util import convert_zh


def test_convert_zh_s2t():
    pytest.importorskip("opencc")
    assert convert_zh("银色的天空", "s2t") == "銀色的天空"
    assert convert_zh("简体", "t2s") == "简体"


def test_convert_zh_noop():
    assert convert_zh("文本", "") == "文本"
    assert convert_zh("文本", "xxx") == "文本"
    assert convert_zh("", "s2t") == ""


class _MergeProvider:
    """返回合并结果的假 provider。"""

    def __init__(self, payload: dict, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    async def complete(self, system: str, user: str) -> object:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        from booktran.llm.base import LLMResult
        return LLMResult(text=json.dumps(self.payload, ensure_ascii=False),
                         prompt_tokens=1, completion_tokens=1)


def test_merge_candidates_ok():
    rows = [{"src": "アレン", "dst": "", "note": "", "hits": 5},
            {"src": "アレン・Retention", "dst": "", "note": "", "hits": 2}]
    provider = _MergeProvider({"terms": [{"src": "アレン", "note": "人物：主角"}]})
    out = _run(merge_candidates(provider, rows))
    assert out is not None
    assert out[0]["src"] == "アレン"
    assert out[0]["note"] == "人物：主角"
    assert out[0]["hits"] == 5      # 频次保留
    assert out[0]["dst"] == ""      # 译文仍留给人工填


def test_merge_candidates_failure_falls_back():
    rows = [{"src": "アレン", "dst": "", "note": "", "hits": 5}]
    provider = _MergeProvider({}, fail=True)
    assert _run(merge_candidates(provider, rows)) is None


def test_merge_candidates_bad_payload():
    rows = [{"src": "アレン", "dst": "", "note": "", "hits": 5}]
    provider = _MergeProvider({"terms": "not a list"})
    assert _run(merge_candidates(provider, rows)) is None


def test_analyze_project_uses_merge():
    """analyze 第二阶段调用 LLM 合并（返回合并后的术语表）。"""
    from booktran.glossary import analyze_project

    doc = SourceDoc(relpath="a.txt", fmt="txt")
    for i in range(3):
        doc.segments.append(_seg(f"アレンが走る{i}"))
    for i in range(2):
        doc.segments.append(_seg(f"銀色の空{i}"))

    class P:
        def __init__(self):
            self.calls = 0

        async def complete(self, system, user):
            self.calls += 1
            from booktran.llm.base import LLMResult
            if "术语表整理助手" in system:
                text = json.dumps({"terms": [{"src": "アレン", "note": "人物：主角"}]},
                                  ensure_ascii=False)
            else:
                text = json.dumps({"characters": [{"name": "アレン"}],
                                   "terms": [{"name": "銀色の空"}],
                                   "non_translate": []}, ensure_ascii=False)
            return LLMResult(text=text, prompt_tokens=1, completion_tokens=1)

    provider = P()
    g = _run(analyze_project([doc], provider, concurrency=2))
    assert provider.calls >= 2          # 抽取 + 合并
    assert [t["src"] for t in g.terms] == ["アレン"]


def _seg(text: str):
    from booktran.model import Segment
    return Segment(sid=f"a.txt#{text}", source=text, status=Status.PENDING)


def _run(coro):
    import asyncio
    return asyncio.run(coro)
