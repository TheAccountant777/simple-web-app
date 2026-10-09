"""Parse Kenyan-style figures ("Sh1.2bn", "(3.2)", "8 per cent") into exact decimals.

Extends the whole-token idea of `grounding._has_number`: a number is never read out of the
middle of a longer token. Code, not an LLM, computes every figure.
"""

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Unit = Literal["KES", "USD", "pct", "none"]

_CURRENCY = {
    "us$": "USD",
    "$": "USD",
    "kes": "KES",
    "kshs": "KES",
    "ksh": "KES",
    "shs": "KES",
    "sh": "KES",
    "shillings": "KES",
    "shilling": "KES",
    "bob": "KES",
}
_SCALE = {
    "thousand": 3,
    "k": 3,
    "million": 6,
    "m": 6,
    "mn": 6,
    "billion": 9,
    "bn": 9,
    "b": 9,
    "trillion": 12,
    "tn": 12,
}
_MISSING = {"", "-", "\u2013", "\u2014", "n/a", "na", "..", "...", "nil", "n.a."}
_DASH = "-\u2013\u2212"

_NUMBER = re.compile(
    rf"""
    (?<![\w.,])
    (?P<open>\()?
    (?P<neg1>[{_DASH}])?
    (?:(?P<cur>US\$|\$|KES|Kshs|KSh|Shs|Sh)(?![A-Za-z])\s*)?
    (?P<neg2>(?<![\w.])[{_DASH}])?
    (?P<num>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    (?:\s*(?P<scale>thousand|million|billion|trillion|mn|bn|tn|k|m|b)(?![A-Za-z]))?
    (?:\s*(?P<suf>%|per\s*cent|percent|shillings?|shs|bob)(?![A-Za-z]))?
    (?P<close>\))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ParsedNumber(BaseModel):
    value: Decimal
    raw: str
    unit: Unit
    scale: int  # 10**scale already applied to `value`


def _build(m: re.Match[str], *, parens: bool) -> ParsedNumber | None:
    if bool(m["open"]) != bool(m["close"]) and parens:
        return None
    negative = bool(m["neg1"] or m["neg2"]) or (parens and bool(m["open"] and m["close"]))
    scale = _SCALE[m["scale"].lower()] if m["scale"] else 0
    value = Decimal(m["num"].replace(",", "")) * (Decimal(10) ** scale)
    unit: Unit = "none"
    if m["cur"]:
        unit = _CURRENCY[m["cur"].lower()]  # type: ignore[assignment]
    suffix = re.sub(r"\s+", "", m["suf"].lower()) if m["suf"] else ""
    if suffix in ("%", "percent"):
        unit = "pct"
    elif suffix:
        unit = "KES"
    raw = m[0].strip() if parens else m[0].strip("() \t")
    return ParsedNumber(value=-value if negative else value, raw=raw, unit=unit, scale=scale)


def parse_number(text: str) -> ParsedNumber | None:
    """Parse one cell/value; None for placeholders ("-", "n/a", "..") and non-numbers."""
    s = text.strip()
    if s.lower() in _MISSING:
        return None
    m = _NUMBER.fullmatch(s)
    return _build(m, parens=True) if m else None


def find_numbers(text: str) -> list[ParsedNumber]:
    """Every number in running text, in order. Parentheses are not read as negatives here."""
    found = (_build(m, parens=False) for m in _NUMBER.finditer(text))
    return [n for n in found if n is not None]


def same_number(a: ParsedNumber, b: ParsedNumber) -> bool:
    """Equal value (after scaling) and equal unit."""
    return a.value == b.value and a.unit == b.unit
