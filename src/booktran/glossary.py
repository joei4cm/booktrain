"""术语表：加载/匹配/注入，以及两阶段全文分析。"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .model import SourceDoc, Status
from .prompt.builder import ANALYZE_SYSTEM, build_analyze_user
from .util import count_tokens


class Glossary:
    def __init__(self, terms: list[dict] | None = None, non_translate: list[str] | None = None,
                 style: str = "", notes: str = "", system_prompt: str = "",
                 background: dict | None = None) -> None:
        self.terms = terms or []
        self.non_translate = non_translate or []
        self.style = style
        self.notes = notes
        self.system_prompt = system_prompt  # 本书专属整体提示词（优先级高于配置，低于命令行）
        self.background = background or {}  # 作品背景（题材/时代/人物/命名规则…）

    # ---------- 加载/保存 ----------
    @classmethod
    def load(cls, path: Path) -> Glossary:
        from .prompt.builder import normalize_background
        if not path.exists():
            return cls()
        data = json.loads(path.read_text("utf-8"))
        return cls(
            terms=data.get("terms", []),
            non_translate=data.get("non_translate", []),
            style=data.get("style", ""),
            notes=data.get("notes", ""),
            system_prompt=data.get("system_prompt", ""),
            background=normalize_background(data.get("background")),
        )

    def save(self, path: Path) -> None:
        from .prompt.builder import normalize_background
        path.write_text(json.dumps({
            "terms": self.terms, "non_translate": self.non_translate,
            "style": self.style, "notes": self.notes,
            "system_prompt": self.system_prompt,
            "background": normalize_background(self.background),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_empty(self) -> bool:
        return not (self.terms or self.non_translate or self.style or self.notes
                    or self.background)

    # ---------- 匹配注入 ----------
    def matched_rows(self, text: str, lang: str = "") -> list[dict]:
        """返回在本块文本中实际出现的术语行（只注入命中的，控制 token）。

        lang 非空且行里有该语言的 dst_<lang> 时，用它覆盖 dst —— many-to-many
        下同一术语可对不同目标语言给不同译名（如英文保留原文名）。
        """
        rows = []
        for row in self.terms:
            src = row.get("src", "")
            if not src:
                continue
            if re.search(r"[A-Za-z]", src):
                pat = re.compile(re.escape(src), re.IGNORECASE)
            else:
                pat = re.compile(re.escape(src))
            if pat.search(text):
                if lang and row.get(f"dst_{lang}"):
                    row = dict(row, dst=row[f"dst_{lang}"])
                rows.append(row)
        return rows

    def matched_non_translate(self, text: str) -> list[str]:
        return [n for n in self.non_translate if n and n in text]


# ---------- 两阶段全文分析 ----------

def _analysis_chunks(docs: list[SourceDoc], budget: int = 8000) -> list[list[str]]:
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_tok = 0
    for doc in docs:
        for seg in doc.segments:
            if seg.status == Status.EXCLUDED:
                continue
            t = count_tokens(seg.source)
            if cur and cur_tok + t > budget:
                chunks.append(cur)
                cur, cur_tok = [], 0
            cur.append(seg.source)
            cur_tok += t
    if cur:
        chunks.append(cur)
    return chunks


async def analyze_project(
    docs: list[SourceDoc],
    provider,
    concurrency: int = 4,
    llm_merge: bool = True,
) -> Glossary:
    """第一阶段：分块并发提取候选；第二阶段：LLM 裁决合并（可退回本地聚合）。

    AiNiee 的第二阶段是再次调用 LLM 裁决合并；这里同样用 LLM 做合并/去重/分类，
    失败时退回确定性聚合（频次+去重），结果仍是人工可编辑的 glossary.json。
    """
    import asyncio

    chunks = _analysis_chunks(docs)
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def run(lines: list[str]) -> None:
        async with sem:
            resp = await provider.complete(ANALYZE_SYSTEM, build_analyze_user(lines))
            from .quality.extract import _extract_json_object
            obj = _extract_json_object(resp.text)
            if obj:
                results.append(obj)

    await asyncio.gather(*(run(c) for c in chunks))

    char_counter: Counter = Counter()
    term_counter: Counter = Counter()
    nt_counter: Counter = Counter()
    char_notes: dict[str, str] = {}
    term_notes: dict[str, str] = {}
    for obj in results:
        for c in obj.get("characters", []):
            name = (c.get("name") or "").strip()
            if name:
                char_counter[name] += 1
                if c.get("note"):
                    char_notes.setdefault(name, c["note"])
        for t in obj.get("terms", []):
            name = (t.get("name") or "").strip()
            if name:
                term_counter[name] += 1
                if t.get("note"):
                    term_notes.setdefault(name, t["note"])
        for n in obj.get("non_translate", []):
            if isinstance(n, str) and n.strip():
                nt_counter[n.strip()] += 1

    # 全文出现频次交叉验证：候选必须在原文中真实出现 >=2 次才算稳定术语
    full_text = "\n".join(
        seg.source for doc in docs for seg in doc.segments if seg.status != Status.EXCLUDED
    )

    def stable(counter: Counter, notes: dict, min_hits: int = 2) -> list[dict]:
        rows = []
        for name, _cnt in counter.most_common():
            occurrences = full_text.count(name)
            if occurrences < min_hits:
                continue
            rows.append({"src": name, "dst": "", "note": notes.get(name, ""), "hits": occurrences})
        return rows

    rows = stable(char_counter, char_notes) + stable(term_counter, term_notes)
    if llm_merge and rows:
        merged = await merge_candidates(provider, rows)
        if merged:
            rows = merged
    return Glossary(
        terms=rows,
        non_translate=[n for n, c in nt_counter.most_common() if full_text.count(n) >= 2][:50],
    )


# ---------- 第二阶段：LLM 裁决合并 ----------

MERGE_SYSTEM = """你是术语表整理助手。下面是从一本书中提取的候选专有名词（同一实体的不同写法可能重复出现）。

请：
1. 合并同一实体的不同写法，保留最完整/最常用的写法作为 src
2. 为每条标注类别与说明：人物/地名/组织/物品/术语，note 写明身份或含义（如"人物：主角"）
3. 剔除明显不是专有名词的条目（如普通词组）
4. 不要编造原文中没有的名字

只输出 JSON，不要输出其他内容：
{"terms":[{"src":"...","note":"人物：主角"}]}"""


async def merge_candidates(provider, rows: list[dict], max_items: int = 400) -> list[dict] | None:
    """让 LLM 合并/去重/分类候选术语。失败或返回异常时返回 None（调用方退回本地聚合）。"""
    items = [{"name": r["src"], "hits": r.get("hits", 0), "note": r.get("note", "")}
             for r in rows[:max_items]]
    if not items:
        return None
    user = ("### 候选术语\n"
            + "\n".join(f"- {it['name']}（出现 {it['hits']} 次）{it['note']}" for it in items)
            + "\n\n只输出 JSON。")
    try:
        resp = await provider.complete(MERGE_SYSTEM, user)
    except Exception:  # noqa: BLE001 — 合并失败不影响主流程
        return None
    from .quality.extract import _extract_json_object
    obj = _extract_json_object(resp.text)
    if not isinstance(obj, dict):
        return None
    terms = obj.get("terms")
    if not isinstance(terms, list):
        return None
    hits_map = {r["src"]: r.get("hits", 0) for r in rows}
    out: list[dict] = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        name = (t.get("src") or t.get("name") or "").strip()
        if not name:
            continue
        out.append({"src": name, "dst": "", "note": (t.get("note") or "").strip(),
                    "hits": hits_map.get(name, 0)})
    return out or None
