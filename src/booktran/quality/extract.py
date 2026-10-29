"""模型回复提取：JSON 协议优先，编号纯文本协议兜底（对照 AiNiee ResponseExtractor）。"""
from __future__ import annotations

import json
import re

JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)
NUMBERED = re.compile(r"^\s*(\d+)\s*[.、:：)\]]\s?", re.MULTILINE)
CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json_object(text: str) -> dict | None:
    text = text.strip()
    # 先剥掉可能的代码块包裹
    m = CODE_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = JSON_OBJ.search(text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def parse_translations(text: str, expect_count: int) -> dict[int, str] | None:
    """解析模型回复为 {编号: 译文}。失败返回 None。

    expect_count: 期望的段数，用于过滤 hallucination 出来的多余条目。
    """
    obj = _extract_json_object(text)
    if obj is not None:
        items = obj.get("translations")
        if isinstance(items, list):
            out: dict[int, str] = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                try:
                    i = int(it.get("i"))
                except (TypeError, ValueError):
                    continue
                t = it.get("t")
                if isinstance(t, str):
                    out[i] = t
            if out:
                # 只保留 1..expect_count 范围内的合法编号
                legal = {i: t for i, t in out.items() if 1 <= i <= expect_count}
                if legal:
                    return legal
        # 兼容直接给 {"1": "译文"} 的形式
        flat = {}
        for k, v in obj.items():
            if isinstance(v, str) and k.isdigit():
                flat[int(k)] = v
        if flat:
            return {i: t for i, t in flat.items() if 1 <= i <= expect_count}

    # 兜底：编号纯文本协议（本地小模型/协议失败时）
    numbered: dict[int, str] = {}
    lines = text.strip().splitlines()
    cur_i: int | None = None
    cur: list[str] = []
    for line in lines:
        m = re.match(r"^\s*(\d+)\s*[.、:：)\]]\s?(.*)$", line)
        if m:
            if cur_i is not None:
                numbered[cur_i] = "\n".join(cur).strip()
            cur_i = int(m.group(1))
            cur = [m.group(2)]
        elif cur_i is not None:
            cur.append(line)
    if cur_i is not None:
        numbered[cur_i] = "\n".join(cur).strip()
    legal = {i: t for i, t in numbered.items() if 1 <= i <= expect_count and t}
    return legal if legal else None
