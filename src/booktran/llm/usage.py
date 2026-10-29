"""用量与费用记账：按 角色 / 阶段 / 模型 / 目标语言 落账。

费用 = 输入 token × price_in + 输出 token × price_out（单位 $/Mtok）。
**未配置价格的档案不猜价**：cost = None，界面显示 —，而不是 0。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Usage:
    """一次（或一批）模型调用的用量记录。"""

    role: str = "translate"
    profile: str = ""
    model: str = ""
    stage: str = ""
    lang: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    requests: int = 1
    retries: int = 0
    latency_ms: int = 0
    cost: float | None = None      # None = 该档案未配置价格
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Usage:
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


def price_of(profile, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0) -> float | None:
    """按档案价格表算钱；未配置价格返回 None。"""
    pin = getattr(profile, "price_in", None)
    pout = getattr(profile, "price_out", None)
    if pin is None and pout is None:
        return None
    pin = float(pin or 0.0)
    pout = float(pout or 0.0)
    pcr = getattr(profile, "price_cache_read", None)
    pcr = float(pcr) if pcr is not None else None
    cost = 0.0
    if cached_tokens and pcr is not None:
        cost += cached_tokens / 1e6 * pcr
        prompt_tokens = max(0, prompt_tokens - cached_tokens)
    cost += prompt_tokens / 1e6 * pin
    cost += completion_tokens / 1e6 * pout
    return round(cost, 8)


class UsageLog:
    """usage.jsonl 追加日志 + 聚合视图。"""

    FILENAME = "usage.jsonl"

    def __init__(self, project_dir: Path | None = None) -> None:
        self.dir = Path(project_dir) if project_dir else None
        self.path = (self.dir / self.FILENAME) if self.dir else None
        self._fh = None

    # ---------- 写 ----------
    def append(self, usage: Usage) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._fh is None:
            self._fh = open(self.path, "a", encoding="utf-8")
        self._fh.write(json.dumps(usage.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ---------- 读 ----------
    def entries(self) -> list[Usage]:
        if self.path is None or not self.path.exists():
            return []
        out: list[Usage] = []
        for line in self.path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Usage.from_dict(json.loads(line)))
            except (ValueError, TypeError):
                continue
        return out

    def aggregate(self) -> dict[str, Any]:
        """按 角色 / 阶段 / 模型 / 语言 四个维度聚合。"""
        entries = self.entries()

        def _bucket(key: str) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for u in entries:
                k = getattr(u, key) or "—"
                b = out.setdefault(k, {"requests": 0, "prompt_tokens": 0,
                                       "completion_tokens": 0, "cost": 0.0,
                                       "unpriced": 0})
                b["requests"] += u.requests
                b["prompt_tokens"] += u.prompt_tokens
                b["completion_tokens"] += u.completion_tokens
                if u.cost is None:
                    b["unpriced"] += u.requests
                else:
                    b["cost"] = round(b["cost"] + u.cost, 6)
            return out

        total_cost = 0.0
        unpriced = 0
        for u in entries:
            if u.cost is None:
                unpriced += u.requests
            else:
                total_cost = round(total_cost + u.cost, 6)
        return {
            "total": {
                "requests": sum(u.requests for u in entries),
                "prompt_tokens": sum(u.prompt_tokens for u in entries),
                "completion_tokens": sum(u.completion_tokens for u in entries),
                "cost": total_cost,
                "unpriced": unpriced,
            },
            "by_role": _bucket("role"),
            "by_stage": _bucket("stage"),
            "by_model": _bucket("model"),
            "by_lang": _bucket("lang"),
        }

    def total_cost(self) -> float:
        return float(self.aggregate()["total"]["cost"])
