"""终端呈现层：颜色、符号、实时单行、结算块、统一错误格式。

规则（见 docs/ux/cli.md）：
- 遵守 NO_COLOR / TERM=dumb / isatty；`--no-color` 强制关闭。
- 进度用**单行原地覆盖**，不刷屏；非 TTY 退化为逐阶段输出。
- 符号统一：✓ 成功 / ✗ 失败 / ! 警告 / – 跳过；**不用 emoji**。
- 错误第一行必须是人话，并给出可执行的下一步。
"""
from __future__ import annotations

import os
import shutil
import sys
from typing import TextIO

# ---------------- 退出码 ----------------
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_AUTH = 4
EXIT_RATE = 5
EXIT_BUDGET = 6
EXIT_PARTIAL = 7
EXIT_INTERRUPTED = 8

OK = "✓"
FAIL = "✗"
WARN = "!"
SKIP = "–"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class UserError(Exception):
    """面向用户的错误：一句话 + 怎么修 + （可选）原始细节。"""

    def __init__(self, summary: str, hint: str = "", detail: str = "",
                 exit_code: int = EXIT_ERROR) -> None:
        super().__init__(summary)
        self.summary = summary
        self.hint = hint
        self.detail = detail
        self.exit_code = exit_code


class Term:
    """终端渲染器。"""

    def __init__(self, stream: TextIO | None = None, *, color: bool | None = None,
                 err: TextIO | None = None) -> None:
        # 流在**调用时**解析（不绑定 import 时的 sys.stdout），
        # 否则测试的 capsys / 重定向都接不住。
        self._stream = stream
        self._err = err
        self.is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        if color is None:
            color = self.is_tty and os.environ.get("NO_COLOR") is None \
                and os.environ.get("TERM", "") != "dumb"
        self.color = bool(color)
        self._status = ""
        self._spin = 0

    # ---------------- 基础 ----------------
    def redirect(self, stream: TextIO) -> None:
        """把正常输出改道（JSON 模式下人类输出走 stderr，stdout 只留机器可读流）。"""
        self._stream = stream

    @property
    def stream(self) -> TextIO:
        return self._stream or sys.stdout

    @property
    def err(self) -> TextIO:
        return self._err or sys.stderr

    @property
    def width(self) -> int:
        env = os.environ.get("COLUMNS")
        if env and env.isdigit():
            return max(40, int(env))
        try:
            return max(40, shutil.get_terminal_size((100, 24)).columns)
        except OSError:
            return 100

    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
                 "blue": "34", "magenta": "35", "cyan": "36"}
        seq = ";".join(codes[s] for s in styles if s in codes)
        return f"\033[{seq}m{text}\033[0m" if seq else text

    def write(self, text: str = "") -> None:
        self.stream.write(text + "\n")
        self.stream.flush()

    # ---------------- 结构 ----------------
    def rule(self, title: str = "") -> None:
        """小节标题（不刷屏，靠留白分组）。"""
        self.clear_status()
        if not title:
            self.write()
            return
        self.write(self.paint(title, "bold"))

    def kv(self, key: str, value: str, *, width: int = 10) -> None:
        self.write(f"  {self.paint(key.ljust(width), 'dim')}{value}")

    def ok(self, text: str) -> None:
        self.write(f"  {self.paint(OK, 'green')} {text}")

    def fail(self, text: str) -> None:
        self.write(f"  {self.paint(FAIL, 'red')} {text}")

    def warn(self, text: str) -> None:
        self.write(f"  {self.paint(WARN, 'yellow')} {text}")

    def skip(self, text: str) -> None:
        self.write(f"  {self.paint(SKIP, 'dim')} {text}")

    # ---------------- 实时单行 ----------------
    def status(self, text: str) -> None:
        """实时覆盖单行（仅 TTY）。"""
        if not self.is_tty:
            return
        width = self.width
        line = text[: width - 1]
        pad = max(0, width - 1 - len(_visible_len(line)))
        self.stream.write("\r" + line + " " * pad)
        self.stream.flush()
        self._status = line

    def clear_status(self) -> None:
        if self._status and self.is_tty:
            self.stream.write("\r" + " " * (self.width - 1) + "\r")
            self.stream.flush()
        self._status = ""

    def tick(self) -> str:
        self._spin = (self._spin + 1) % len(SPINNER)
        return SPINNER[self._spin]

    # ---------------- 错误 ----------------
    def error(self, err: UserError | str) -> None:
        """错误写 stderr（stdout 留给正常输出，便于管道）。"""
        self.clear_status()
        if isinstance(err, str):
            err = UserError(err)
        self.err.write(f"{self.paint(FAIL, 'red')} {err.summary}\n")
        if err.hint:
            self.err.write(f"  {err.hint}\n")
        if err.detail:
            self.err.write(f"  {self.paint('细节: ' + _one_line(err.detail, 200), 'dim')}\n")
        self.err.flush()


def _visible_len(text: str) -> int:
    """粗略忽略 ANSI 序列的长度。"""
    out, i = 0, 0
    while i < len(text):
        if text[i] == "\033":
            j = text.find("m", i)
            i = len(text) if j < 0 else j + 1
            continue
        out += 1
        i += 1
    return out


def _one_line(text: str, limit: int) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


# ---------------- 格式化 ----------------

def fmt_cost(value: float | None) -> str:
    """金额：未知显示 —，小额显示 <$0.01。"""
    if value is None:
        return "—"
    if 0 < value < 0.01:
        return "<$0.01"
    return f"${value:,.2f}"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "剩余 ~?"
    return f"剩余 ~{fmt_duration(seconds)}"
