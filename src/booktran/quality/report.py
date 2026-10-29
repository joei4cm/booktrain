"""成品质检报告：问题清单 + 汇总 + Markdown 输出。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
SEVERITY_LABELS = {"error": "错误", "warn": "警告", "info": "提示"}
KIND_LABELS = {
    "glossary": "术语不一致",
    "untranslated": "疑似未翻译",
    "placeholder": "占位符丢失",
    "length": "长度异常",
    "numbering": "编号残留",
    "non_translate": "禁翻表残留",
    "empty": "空译文",
    "llm": "模型建议",
}


@dataclass
class Issue:
    """一处质检问题。"""

    sid: str = ""
    lang: str = ""
    kind: str = ""
    severity: str = "warn"      # error | warn | info
    message: str = ""
    suggestion: str = ""
    source: str = ""            # 原文片段
    target: str = ""            # 译文片段
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Issue:
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


class QualityReport:
    """一次质检的问题清单。"""

    def __init__(self, issues: list[Issue] | None = None) -> None:
        self.issues: list[Issue] = list(issues or [])

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def extend(self, issues: list[Issue]) -> None:
        self.issues.extend(issues)

    # ---------------- 汇总 ----------------
    def summary(self) -> dict:
        by_kind: dict[str, int] = {}
        by_severity: dict[str, int] = {"error": 0, "warn": 0, "info": 0}
        by_lang: dict[str, int] = {}
        for i in self.issues:
            by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
            by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
            by_lang[i.lang] = by_lang.get(i.lang, 0) + 1
        return {
            "total": len(self.issues),
            "by_kind": by_kind,
            "by_severity": by_severity,
            "by_lang": by_lang,
        }

    def sorted_issues(self) -> list[Issue]:
        return sorted(self.issues,
                      key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.kind, i.sid))

    def to_dict(self) -> dict:
        return {"summary": self.summary(),
                "issues": [i.to_dict() for i in self.sorted_issues()]}

    def to_markdown(self) -> str:
        s = self.summary()
        lines = ["# 成品质检报告", ""]
        lines.append(f"- 问题总数：**{s['total']}**")
        lines.append(f"- 错误 {s['by_severity'].get('error', 0)} · "
                     f"警告 {s['by_severity'].get('warn', 0)} · "
                     f"提示 {s['by_severity'].get('info', 0)}")
        if s["by_kind"]:
            lines.append("")
            lines.append("| 类型 | 数量 |")
            lines.append("|---|---|")
            for kind, n in sorted(s["by_kind"].items(), key=lambda kv: -kv[1]):
                lines.append(f"| {KIND_LABELS.get(kind, kind)} | {n} |")
        if self.issues:
            lines.append("")
            lines.append("## 明细")
            lines.append("")
            lines.append("| 语言 | 段落 | 类型 | 说明 | 建议 |")
            lines.append("|---|---|---|---|---|")
            for i in self.sorted_issues():
                msg = i.message.replace("|", "\\|")
                sug = (i.suggestion or "").replace("|", "\\|")
                lines.append(f"| {i.lang} | `{i.sid}` | {KIND_LABELS.get(i.kind, i.kind)} "
                             f"| {msg} | {sug} |")
        return "\n".join(lines) + "\n"

    # ---------------- 落盘 ----------------
    def save(self, project_dir: Path) -> tuple[Path, Path]:
        d = Path(project_dir)
        d.mkdir(parents=True, exist_ok=True)
        j = d / "quality_report.json"
        m = d / "quality_report.md"
        j.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        m.write_text(self.to_markdown(), encoding="utf-8")
        return j, m

    @classmethod
    def load(cls, project_dir: Path) -> QualityReport:
        p = Path(project_dir) / "quality_report.json"
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls([Issue.from_dict(i) for i in (data.get("issues") or [])])
