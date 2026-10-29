"""输入/输出格式适配器协议。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..model import SourceDoc


class Reader(ABC):
    @abstractmethod
    def read(self, path: Path, relpath: str) -> SourceDoc: ...


class Writer(ABC):
    @abstractmethod
    def write(
        self,
        doc: SourceDoc,
        source_path: Path,
        out_path: Path,
        *,
        bilingual: bool = False,
        source_first: bool = False,
        lang: str = "",
        target_lang_code: str = "zh",
    ) -> None: ...
