"""Check that a quoted passage really appears in a source text."""

import re
import unicodedata

from rapidfuzz import fuzz

_TRANSLATE = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
}
MIN_FUZZY_LEN = 20
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*%?")


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_TRANSLATE).casefold()
    return re.sub(r"\s+", " ", s).strip()


def quote_in_text(quote: str, text: str, threshold: float = 0.9) -> bool:
    q = normalize(quote)
    if not q:
        return False
    t = normalize(text)
    if q in t:
        return True
    if any(n not in t for n in _NUMBER.findall(q)):
        return False
    if len(q) < MIN_FUZZY_LEN or len(t) < len(q):
        return False
    return bool(fuzz.partial_ratio(q, t) >= threshold * 100)
