"""Check that a quoted passage really appears in a source text."""

import re
import unicodedata

_TRANSLATE = {
    0x2018: "'",
    0x2019: "'",
    0x201C: '"',
    0x201D: '"',
    0x2013: "-",
    0x2014: "-",
}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*%?")
_PUNCT = re.compile(r"(?<!\d)[^\w\s%]|[^\w\s%](?!\d)")


def _has_number(n: str, text: str) -> bool:
    """True if `n` appears in `text` as a whole number token, not inside a longer one."""
    pattern = r"(?<!\d)(?<!\d[.,])" + re.escape(n) + r"(?!\d)(?![.,]\d)"
    return re.search(pattern, text) is not None


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_TRANSLATE).casefold()
    return re.sub(r"\s+", " ", s).strip()


def _squash(s: str) -> str:
    """Normalise, then drop punctuation (keeping `.`/`,` inside numbers and `%`)."""
    return re.sub(r"\s+", " ", _PUNCT.sub(" ", normalize(s))).strip()


def quote_in_text(quote: str, text: str) -> bool:
    """Exact match after normalising case, whitespace, typography and punctuation."""
    q = _squash(quote)
    if not q:
        return False
    t = _squash(text)
    if any(not _has_number(n, t) for n in _NUMBER.findall(q)):
        return False
    return q in t
