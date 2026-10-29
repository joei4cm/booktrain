"""持久化：快照(state.json) + 追加事件日志(events.jsonl)。

写路径：运行中每完成一个 segment 追加一条事件；正常退出/每 N 条做快照压缩。
读路径：加载快照 → 重放事件 → 立即压缩。崩溃最坏丢最后一条 append（有 flush）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Segment, SourceDoc, Stats, Status

SNAPSHOT = "state.json"
EVENTS = "events.jsonl"
COMPACT_EVERY = 500  # 事件条数达到阈值时做一次快照压缩


class Store:
    def __init__(self, project_dir: Path) -> None:
        self.dir = project_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events_since_snapshot = 0
        self._fh = None
        self.pending_compaction = False

    # ---------- 事件 ----------
    def append_event(self, seg: Segment, lang: str) -> None:
        if self._fh is None:
            self._fh = open(self.dir / EVENTS, "a", encoding="utf-8")
        t = seg.track(lang, create=False)
        self._fh.write(json.dumps({
            "sid": seg.sid, "lang": lang,
            "status": t.status.value,
            "target": t.target, "error": t.error, "model": t.model,
        }, ensure_ascii=False) + "\n")
        self._fh.flush()
        self._events_since_snapshot += 1
        if self._events_since_snapshot >= COMPACT_EVERY:
            self.pending_compaction = True

    # ---------- 快照 ----------
    def save_snapshot(self, docs: list[SourceDoc], stats: Stats) -> None:
        payload = {
            "version": 2,
            "stats": stats.to_dict(),
            "docs": [
                {
                    "relpath": d.relpath, "fmt": d.fmt, "meta": d.meta,
                    "segments": [s.to_dict() for s in d.segments],
                } for d in docs
            ],
        }
        path = self.dir / SNAPSHOT
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        # 快照后清空事件日志
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        (self.dir / EVENTS).write_text("", encoding="utf-8")
        self._events_since_snapshot = 0

    def load(self, default_lang: str = "") -> tuple[list[SourceDoc], Stats]:
        """快照 + 事件重放，返回 (docs, stats)。无快照返回空。

        default_lang: 旧版单语言快照迁移时使用的目标语言键。
        """
        snap_path = self.dir / SNAPSHOT
        if not snap_path.exists():
            return [], Stats()
        data = json.loads(snap_path.read_text("utf-8"))
        docs: list[SourceDoc] = []
        by_sid: dict[str, Segment] = {}
        for d in data.get("docs", []):
            doc = SourceDoc(relpath=d["relpath"], fmt=d["fmt"],
                            meta=d.get("meta") or {})
            for s in d.get("segments", []):
                seg = Segment.from_dict(s)
                if default_lang:
                    seg.migrate_legacy_track(default_lang)
                doc.segments.append(seg)
                by_sid[seg.sid] = seg
            docs.append(doc)
        # 重放事件
        events_path = self.dir / EVENTS
        replayed = 0
        if events_path.exists():
            for line in events_path.read_text("utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半截写入，丢弃
                seg = by_sid.get(ev.get("sid"))
                if seg is None:
                    continue
                lang = ev.get("lang") or default_lang or ""
                t = seg.track(lang)
                t.status = Status(ev.get("status", t.status.value))
                t.target = ev.get("target")
                t.error = ev.get("error")
                t.model = ev.get("model")
                replayed += 1
        stats = Stats.from_dict(data.get("stats"))
        return docs, stats

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
