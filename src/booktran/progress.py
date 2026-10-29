"""进度：TTY 实时覆盖单行；非 TTY 按阶段输出；事件回调供 web / SSE 使用。"""
from __future__ import annotations

import asyncio
import time

from .ui import Term, fmt_cost, fmt_eta


class Progress:
    """进度与事件广播。"""

    def __init__(self, term: Term | None = None) -> None:
        self.term = term or Term()
        self.listeners: list = []          # 额外监听器（web / SSE），签名 fn(event: dict)
        self.total = 0
        self.done = 0
        self.failed = 0
        self.cost: float | None = 0.0
        self.lang = ""
        self.stage = ""
        self.started_at = time.monotonic()
        self._rate_window: list[tuple[float, int]] = []
        self._seq = 0

    def add_listener(self, fn) -> None:
        self.listeners.append(fn)

    async def _emit(self, event: dict) -> None:
        self._seq += 1
        event.setdefault("seq", self._seq)
        event.setdefault("at", time.time())
        for fn in self.listeners:
            try:
                result = fn(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 — 监听器异常不影响主流程
                pass

    # ---------------- 状态更新 ----------------
    def set_total(self, n: int) -> None:
        self.total = n
        self.render()

    def set_lang(self, lang: str) -> None:
        self.lang = lang
        self.render()

    def add_cost(self, cost: float | None) -> None:
        if cost is None:
            if self.cost is not None:
                self.cost = None      # 出现未知费用 → 整体变未知
        elif self.cost is not None:
            self.cost = round(self.cost + cost, 6)

    def _rate_per_min(self) -> float:
        now = time.monotonic()
        self._rate_window = [(t, c) for t, c in self._rate_window if now - t < 60]
        if len(self._rate_window) < 2:
            return 0.0
        (t0, c0), (t1, c1) = self._rate_window[0], self._rate_window[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return (c1 - c0) / dt * 60

    def render(self) -> None:
        if not self.term.is_tty:
            return
        pct = (self.done / self.total * 100) if self.total else 0
        bar_w = 16
        filled = int(bar_w * pct / 100)
        bar = "█" * filled + "░" * (bar_w - filled)
        rate = self._rate_per_min()
        eta = None
        if rate > 0 and self.total:
            eta = (self.total - self.done) / rate * 60
        parts = [
            f"{self.term.tick()} {self.stage or '处理'}",
            self.lang,
            f"{self.done}/{self.total}",
            f"▕{bar}▏ {pct:.0f}%",
            fmt_cost(self.cost),
        ]
        if rate:
            parts.append(f"{rate:.0f} 段/min")
        parts.append(fmt_eta(eta))
        self.term.status("  ".join(p for p in parts if p))

    # ---------------- 事件 ----------------
    async def stage_start(self, stage: str, *, total: int = 0, lang: str = "",
                          round: int = 0, rounds: int = 0) -> None:
        self.stage = stage
        self.lang = lang
        if total:
            self.total = total
        label = f"第 {round}/{rounds} 轮 · {stage}" if rounds else stage
        if self.term.is_tty:
            self.term.clear_status()
            self.term.write(f"  {self.term.paint(label, 'bold')}"
                            + (f"  {lang}" if lang else ""))
        else:
            self.term.write(f"[{label}] 开始")
        await self._emit({"type": "stage", "stage": stage, "round": round,
                          "rounds": rounds, "total": total, "lang": lang})

    async def stage_end(self, stage: str, *, ok: bool = True, detail: str = "",
                        done: int = 0, failed: int = 0) -> None:
        await self._emit({"type": "stage_end", "stage": stage, "ok": ok,
                          "detail": detail, "done": done, "failed": failed})

    async def chunk_done(self, chunk_label: str, ok: bool, detail: str) -> None:
        if ok:
            self.done += 1
        else:
            self.failed += 1
        self.render()
        await self._emit({"type": "chunk", "label": chunk_label, "ok": ok, "detail": detail})

    async def issue(self, issue: dict) -> None:
        await self._emit({"type": "issue", **issue})

    async def finished(self) -> None:
        self.term.clear_status()
        await self._emit({"type": "done", "done": self.done, "failed": self.failed,
                          "cost": self.cost})
