"""核心数据模型：Segment 的多语言 track 状态机是一切的基础（对照 AiNiee 的 CacheItem）。"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PENDING = "pending"          # 待翻译
    TRANSLATED = "translated"    # 已翻译（终态，进入导出）
    EXCLUDED = "excluded"        # 过滤掉（纯符号/数字等，不翻译；对所有语言统一）


class StageState(str, Enum):
    """阶段状态（阶段化流水线：识别 → 翻译 → 校对 → 排版）。"""

    PENDING = "pending"      # 还没做
    RUNNING = "running"      # 正在做
    DONE = "done"            # 做完了
    FAILED = "failed"        # 失败（带原因，可重跑）
    SKIPPED = "skipped"      # 不需要做
    STALE = "stale"          # 上游变了，结果作废（数据保留）


@dataclass
class Track:
    """一个目标语言的翻译结果。段落 × 目标语言 = 一个 track。

    v0.6：加了**阶段维度**——"已译"不等于"成品"。每个阶段独立记录状态；
    上游改动会把下游标记为 stale（数据保留，但不再算数）。
    """

    target: str = ""
    status: Status = Status.PENDING
    error: str | None = None     # 最近一次失败原因（不阻断 resume，仅报告用）
    model: str | None = None
    stages: dict[str, str] = field(default_factory=dict)   # stage -> StageState
    revisions: list[dict] = field(default_factory=list)     # 校对改写历史（可回退）

    @property
    def final_text(self) -> str:
        return self.target if (self.status == Status.TRANSLATED and self.target) else ""

    def stage(self, name: str, create: bool = True) -> str:
        """取（可选创建）某阶段的状态。"""
        st = self.stages.get(name)
        if st is None and create:
            st = StageState.PENDING.value
            self.stages[name] = st
        return st or StageState.PENDING.value

    def set_stage(self, name: str, state: StageState | str) -> None:
        self.stages[name] = state.value if isinstance(state, StageState) else str(state)

    def invalidate_downstream(self, after: str, order: list[str]) -> list[str]:
        """把某阶段之后的下游阶段标记为 stale（保留数据）。"""
        if after not in order:
            return []
        idx = order.index(after)
        touched = []
        for st in order[idx + 1:]:
            if self.stages.get(st) in (StageState.DONE.value, StageState.RUNNING.value):
                self.stages[st] = StageState.STALE.value
                touched.append(st)
        return touched

    def to_dict(self) -> dict:
        return {
            "target": self.target, "status": self.status.value,
            "error": self.error, "model": self.model,
            "stages": dict(self.stages),
            "revisions": list(self.revisions)[-5:],   # 只留最近 5 版，避免状态文件膨胀
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Track:
        d = d or {}
        return cls(
            target=d.get("target") or "",
            status=Status(d.get("status", "pending")),
            error=d.get("error"),
            model=d.get("model"),
            stages=dict(d.get("stages") or {}),
            revisions=list(d.get("revisions") or []),
        )


@dataclass
class Segment:
    sid: str
    source: str
    status: Status = Status.PENDING   # EXCLUDED = 全局过滤；其余由各 track 决定
    meta: dict[str, Any] = field(default_factory=dict)
    tracks: dict[str, Track] = field(default_factory=dict)  # 目标语言 -> Track
    # ---- IR 字段（v0.7：与格式无关的段落结构，单一存储）----
    kind: str = "para"                 # para|heading|list|quote|code|caption|footnote
    level: int = 0                    # 标题层级
    spans: list[dict] = field(default_factory=list)   # 行内标记（ir.Span 的 dict 形式）
    anchors: list[dict] = field(default_factory=list)  # 源版面锚点（page/bbox/xpath）
    style: dict[str, Any] = field(default_factory=dict)  # 排版样式（样式表落点）
    src_lang: str = ""                # 段级源语言（many-to-many 的关键）
    # meta 约定：
    #   txt:  {"line_break": int}             段后空行数
    #   epub: {"original_html": str, "file": str, "tag": str}
    #   pdf:  {"page": int, "bbox": [x0,y0,x1,y1]}

    def track(self, lang: str, create: bool = True) -> Track:
        """取（可选创建）某语言的翻译 track。"""
        t = self.tracks.get(lang)
        if t is None:
            if not create:
                return Track()
            t = Track()
            self.tracks[lang] = t
        return t

    def ensure_tracks(self, langs: list[str]) -> None:
        for lang in langs:
            if lang not in self.tracks:
                self.tracks[lang] = Track()

    def translated_text(self, lang: str = "") -> str:
        """某语言的成品文本（未译返回空串）；lang 为空时取任一已译 track。"""
        if lang:
            t = self.tracks.get(lang)
            return t.final_text if t else ""
        for t in self.tracks.values():
            if t.final_text:
                return t.final_text
        return ""

    def text_for(self, lang: str = "") -> str:
        """导出用：成品文本，未译回落原文。"""
        if self.status == Status.EXCLUDED:
            return self.source
        return self.translated_text(lang) or self.source

    def is_done(self, lang: str = "") -> bool:
        """该语言是否已译；lang 为空时看任一 track。"""
        if lang:
            t = self.tracks.get(lang)
            return t is not None and t.status == Status.TRANSLATED
        return any(t.status == Status.TRANSLATED for t in self.tracks.values())

    @property
    def overall_status(self) -> Status:
        """段级派生态：EXCLUDED 不变；任一 track 已译 → TRANSLATED；否则 PENDING。"""
        if self.status == Status.EXCLUDED:
            return Status.EXCLUDED
        return Status.TRANSLATED if self.is_done() else Status.PENDING

    @property
    def final_text(self) -> str:
        """向后兼容：取第一个已翻译 track 的文本。"""
        for t in self.tracks.values():
            if t.status == Status.TRANSLATED and t.target:
                return t.target
        return self.source

    @classmethod
    def from_dict(cls, s: dict) -> Segment:
        """兼容 v0.x 单语言快照（直接带 target/status 字段）与多语言快照。"""
        tracks_raw = s.get("tracks")
        if isinstance(tracks_raw, dict):
            tracks = {lang: Track.from_dict(td) for lang, td in tracks_raw.items()}
        else:
            # 旧格式：单语言，语言键未知 → 挂到 ""（加载方负责迁移）
            legacy = Track.from_dict({
                "target": s.get("target"), "status": s.get("status", "pending"),
                "error": s.get("error"), "model": s.get("model"),
            })
            tracks = {"" : legacy} if (legacy.target or legacy.status != Status.PENDING) else {}
        return cls(
            sid=s["sid"], source=s["source"],
            status=Status(s.get("status", "pending")),
            meta=s.get("meta") or {}, tracks=tracks,
            kind=s.get("kind") or "para",
            level=int(s.get("level") or 0),
            spans=list(s.get("spans") or []),
            anchors=list(s.get("anchors") or []),
            style=dict(s.get("style") or {}),
            src_lang=s.get("src_lang") or "",
        )

    def to_dict(self) -> dict:
        return {
            "sid": self.sid, "source": self.source,
            "status": self.overall_status.value, "meta": self.meta,
            "tracks": {lang: t.to_dict() for lang, t in self.tracks.items()},
            "kind": self.kind, "level": self.level,
            "spans": self.spans, "anchors": self.anchors,
            "style": self.style, "src_lang": self.src_lang,
        }

    def migrate_legacy_track(self, lang: str) -> None:
        """把旧快照挂在 '' 下的单语言结果迁移到指定语言键。"""
        if "" in self.tracks and lang not in self.tracks:
            self.tracks[lang] = self.tracks.pop("")
        elif "" in self.tracks:
            self.tracks.pop("")


@dataclass
class SourceDoc:
    relpath: str        # 相对输入根目录的路径
    fmt: str            # "txt" | "epub" | "pdf"
    segments: list[Segment] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)  # 文档级元数据：source_lang 等

    def iter_active(self) -> Iterator[Segment]:
        for seg in self.segments:
            if seg.status != Status.EXCLUDED:
                yield seg


@dataclass
class Stats:
    requests: int = 0
    failed_chunks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retried_requests: int = 0
    cost: float = 0.0             # 累计费用（美元）；未配置价格的档案不计入
    unpriced_requests: int = 0    # 未配置价格的请求数（费用未知，不猜）

    def add_usage(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt or 0
        self.completion_tokens += completion or 0

    def add_cost(self, cost: float | None) -> None:
        if cost is None:
            self.unpriced_requests += 1
        else:
            self.cost = round(self.cost + float(cost), 6)

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "failed_chunks": self.failed_chunks,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retried_requests": self.retried_requests,
            "cost": self.cost,
            "unpriced_requests": self.unpriced_requests,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> Stats:
        s = cls()
        if d:
            for k, v in d.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        return s
