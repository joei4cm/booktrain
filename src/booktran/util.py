"""通用工具。"""
from __future__ import annotations

import re

# tiktoken 可选：没有时退化为字符估算（英文 ~4字符/token，CJK ~1.5字符/token）
_encoder = None
_encoder_tried = False


def count_tokens(text: str) -> int:
    global _encoder, _encoder_tried
    if not text:
        return 0
    if not _encoder_tried:
        _encoder_tried = True
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001
            _encoder = None
    if _encoder is not None:
        return len(_encoder.encode(text))
    ascii_count = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_count
    return max(1, int(ascii_count / 4 + non_ascii / 1.5))


# 纯符号/数字行：无任何字母、数字语义或 CJK 字符的行（对照 AiNiee TextFilter 思路）
_PUNCT_ONLY = re.compile(r"^[\W_\s]+$", re.UNICODE)
_NUMBER_ONLY = re.compile(r"^[\d\s.,:;()\-０-９、。]+$")


def is_trivial_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if _NUMBER_ONLY.match(t):
        return True
    if _PUNCT_ONLY.match(t):
        # 仍要放行含 CJK 的行（\W 不匹配假名/汉字？在 re.UNICODE 下 \w 匹配 CJK，所以这里安全）
        return not re.search(r"[぀-ヿ㐀-鿿\w]", t)
    return False


def detect_encoding(data: bytes) -> str:
    """轻量编码探测：BOM → utf-8 严格 → 常见 CJK 编码严格 → utf-8 replace。"""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return enc
    for enc in ("utf-8", "gb18030", "big5", "shift_jis", "euc-kr", "latin-1"):
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


# ---------- 简繁转换（OpenCC，可选依赖） ----------
_ZH_CONVERTERS: dict = {}
_ZH_WARNED = False


def convert_zh(text: str, mode: str) -> str:
    """简繁转换：mode = "s2t"（简→繁）| "t2s"（繁→简）。

    未安装 opencc 时原样返回并提示一次（pip install 'booktran[zh]'）。
    """
    global _ZH_WARNED
    if not mode or not text:
        return text
    key = mode.strip().lower()
    if key not in ("s2t", "t2s"):
        return text
    conv = _ZH_CONVERTERS.get(key)
    if conv is None:
        try:
            import opencc  # noqa: PLC0415
        except ImportError:
            if not _ZH_WARNED:
                _ZH_WARNED = True
                print("提示: 未安装 opencc，简繁转换已跳过（pip install 'booktran[zh]'）")
            return text
        conv = opencc.OpenCC(key)
        _ZH_CONVERTERS[key] = conv
    try:
        return conv.convert(text)
    except Exception:  # noqa: BLE001 — 转换失败不影响主流程
        return text
