"""段级语言检测（many-to-many 的前提）。

为什么不用整本书一个值：混排的书（作者附注、英文书名、对话里的外语）会整本判错，
译文提示词里的 `{source_language}` 也会跟着错。

策略：
1. **脚本检测**（Unicode 区段）——中日韩、阿拉伯、希伯来、天城文、泰文、西里尔等一眼可分
2. **拉丁语系启发式**——功能词表打分（英语/法语/德语/西语/意大利语/葡语/荷兰语/波兰语/土耳其语/印尼语/越南语）
3. **可选统计模型**——装了 `py3langid` / `lingua-language-detector` 就优先用（可识别更多语言）
"""
from __future__ import annotations

import re
from collections import Counter

# ---------- 脚本（Unicode 区段） ----------
_SCRIPT_PATTERNS: dict[str, re.Pattern] = {
    "hiragana": re.compile(r"[぀-ゟ]"),
    "katakana": re.compile(r"[゠-ヿ]"),
    "hangul": re.compile(r"[가-힯ᄀ-ᇿ]"),
    "han": re.compile(r"[一-鿿㐀-䶿]"),
    "arabic": re.compile(r"[؀-ۿݐ-ݿ]"),
    "hebrew": re.compile(r"[֐-׿]"),
    "devanagari": re.compile(r"[ऀ-ॿ]"),
    "thai": re.compile(r"[฀-๿]"),
    "greek": re.compile(r"[Ͱ-Ͽ]"),
    "cyrillic": re.compile(r"[Ѐ-ӿ]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}

# 简繁判定用的特征字（只出现在简体 / 只出现在繁体）
_SIMP_ONLY = set("们这国说时会学门车马鸟长东书买卖见贝汉义乐习")
_TRAD_ONLY = set("們這國說時會學門車馬鳥長東書買賣見貝漢義樂習")

# 拉丁语系功能词（按语言）
_STOPWORDS: dict[str, tuple[str, ...]] = {
    "english": ("the", "and", "of", "to", "in", "is", "that", "it", "for", "was",
                "with", "as", "on", "at", "by", "from", "this", "are", "be", "not"),
    "french": ("le", "la", "les", "des", "du", "et", "est", "que", "qui", "dans",
               "pour", "une", "un", "sur", "avec", "ne", "pas", "plus", "son", "ses"),
    "german": ("der", "die", "das", "und", "ist", "von", "zu", "den", "mit", "sich",
               "auf", "für", "nicht", "ein", "eine", "dem", "des", "auch", "aber"),
    "spanish": ("el", "la", "los", "las", "de", "que", "y", "en", "un", "una",
                "por", "con", "para", "es", "no", "se", "su", "como", "más", "pero"),
    "italian": ("il", "che", "di", "per", "con", "non", "una", "sono", "del", "della",
                "gli", "nel", "alla", "più", "anche", "come", "questo", "ma"),
    "portuguese": ("de", "que", "não", "uma", "para", "com", "por", "dos", "das",
                   "como", "mais", "seu", "sua", "ele", "ela", "ao", "pelo"),
    "dutch": ("het", "een", "van", "dat", "op", "te", "met", "voor", "niet", "zijn",
              "aan", "maar", "ook", "door", "over", "deze", "worden", "naar"),
    "polish": ("nie", "się", "jest", "że", "tego", "oraz", "ale", "przez", "dla",
               "jego", "który", "być", "tym", "które", "która", "są"),
    "turkish": ("ve", "bir", "bu", "için", "ile", "olarak", "daha", "olan", "çok",
                "gibi", "kadar", "sonra", "veya", "ancak"),
    "indonesian": ("yang", "dan", "di", "untuk", "dengan", "ini", "itu", "tidak",
                   "dari", "dalam", "akan", "pada", "adalah", "oleh"),
    "vietnamese": ("và", "của", "là", "các", "có", "được", "trong", "cho", "không",
                   "người", "một", "này", "với", "những"),
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# 脚本 → 语言（一一对应的脚本直接给答案）
_SCRIPT_LANG = {
    "hiragana": "japanese", "katakana": "japanese", "hangul": "korean",
    "arabic": "arabic", "hebrew": "hebrew", "devanagari": "hindi",
    "thai": "thai", "greek": "greek", "cyrillic": "russian",
}


def detect_scripts(text: str) -> Counter:
    """文本里各脚本的字符数。"""
    c: Counter = Counter()
    if not text:
        return c
    for name, pat in _SCRIPT_PATTERNS.items():
        n = len(pat.findall(text))
        if n:
            c[name] = n
    return c


def _chinese_variant(text: str) -> str:
    simp = sum(1 for ch in text if ch in _SIMP_ONLY)
    trad = sum(1 for ch in text if ch in _TRAD_ONLY)
    if trad > simp:
        return "chinese_traditional"
    return "chinese_simplified"


def _latin_language(text: str) -> tuple[str, float]:
    """拉丁语系：用功能词表打分。"""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return "english", 0.0
    total = len(words)
    scores: dict[str, float] = {}
    for lang, stops in _STOPWORDS.items():
        hits = 0
        for w in words:
            if w in stops:
                hits += 1
        if hits:
            scores[lang] = hits / total * 100      # 每百词命中数
    if not scores:
        return "english", 0.25
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (best_score - second) / max(1.0, best_score)
    density = min(1.0, best_score / 8.0)          # 命中密度
    confidence = round(min(0.95, 0.35 + 0.4 * margin + 0.3 * density), 3)
    return best, confidence


def _statistical(text: str) -> tuple[str, float] | None:
    """可选：py3langid / lingua。装了才用。"""
    try:
        from py3langid.langid import LanguageIdentifier  # type: ignore
        lid = LanguageIdentifier.from_pickled_model()
        lang, _score = lid.classify(text)
        return lang, 0.9
    except Exception:  # noqa: BLE001
        pass
    try:
        from lingua import LanguageDetectorBuilder  # type: ignore
        det = LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()
        best = det.detect_language_of(text)
        if best:
            return best.iso_code_639_1.name.lower(), 0.9
    except Exception:  # noqa: BLE001
        pass
    return None


def detect_language(text: str, default: str = "english") -> tuple[str, float]:
    """检测一段文本的语言 → (语言, 置信度 0~1)。"""
    text = (text or "").strip()
    if not text:
        return default, 0.0
    scripts = detect_scripts(text)
    total = sum(scripts.values())
    if not total:
        return default, 0.0

    # 非拉丁脚本占比高 → 直接判
    for name, lang in _SCRIPT_LANG.items():
        if scripts.get(name, 0) / total >= 0.15:
            if lang == "japanese":
                return "japanese", 0.95
            if lang == "russian":
                return "russian", 0.8
            return lang, 0.9
    if scripts.get("han", 0) / total >= 0.3:
        return _chinese_variant(text), 0.9

    # 拉丁为主
    stat = _statistical(text)
    if stat:
        return stat
    return _latin_language(text)


def detect_segment_languages(docs, *, default: str = "english",
                             min_chars: int = 12) -> dict[str, str]:
    """给文档里的每段打上源语言标签。

    短段（标题、章节名）用**同文档里长段的主流语言**兜底——而不是整本书的猜测：
    混排书里"第一章 启程"这种短标题应该跟正文同语言。
    返回 {sid: lang}。
    """
    out: dict[str, str] = {}
    for doc in docs:
        detected: dict[str, str] = {}
        for seg in doc.segments:
            text = (seg.source or "").strip()
            if len(text) < min_chars:
                continue
            lang, conf = detect_language(text)
            if conf >= 0.45:
                detected[seg.sid] = lang
        # 文档主流语言（按长段统计）
        counts = Counter(detected.values())
        dominant = counts.most_common(1)[0][0] if counts else (
            doc.meta.get("source_lang") or default)
        for seg in doc.segments:
            if seg.sid in detected:
                out[seg.sid] = detected[seg.sid]
            else:
                out[seg.sid] = dominant
    return out
