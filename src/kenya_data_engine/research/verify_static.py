"""Static verification (spec 6.2 steps 2-5): grounding, numbers, entity and period, tier, staleness.

Nothing here calls an LLM. Every claim comes back as a `Claim` with a provisional status:
`refused` (a hard check failed), `speculation` (no quote and no figure) or `inference`.
`assign_status` in `verify.py` promotes inference to fact once entailment and conflicts are in.
"""

import re
from datetime import date

from kenya_data_engine.data.periods import Period, parse_period
from kenya_data_engine.data.stats import FigureBook
from kenya_data_engine.research.claims import (
    CHANGE_KINDS,
    DOWN_WORDS,
    PLACEHOLDER,
    UP_WORDS,
    render,
)
from kenya_data_engine.research.evidence import EvidenceBook
from kenya_data_engine.research.figures import FigurePack
from kenya_data_engine.research.models import CandidateClaim, Claim, FigureRef, TextEvidence
from kenya_data_engine.research.tiers import is_stale, tier_for
from kenya_data_engine.tools.grounding import normalize, quote_in_text, quote_window
from kenya_data_engine.tools.numbers import ParsedNumber, find_numbers, same_number

_PERIOD_REQUIRED = {"price", "rate", "statistic", "annual", "forecast"}
_SENSITIVE = re.compile(
    r"\b(charged with|arrested|fraud|corruption|theft|scandal|embezzle\w*)\b", re.IGNORECASE
)
_NUMBER_WORDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|thousand|million|billion|trillion)\b",
    re.IGNORECASE,
)
_YEAR_TOKEN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_MONTHS = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
]  # fmt: skip
_WINDOW = 300


def is_year(n: ParsedNumber) -> bool:
    """A bare four-digit year: no separators, no decimals."""
    return (
        n.unit == "none" and re.fullmatch(r"\d{4}", n.raw) is not None and 1900 <= n.value <= 2100
    )


def literal_numbers(template: str) -> list[ParsedNumber]:
    """Numbers the model typed: placeholders are removed first."""
    return find_numbers(PLACEHOLDER.sub(" ", template))


def numbers_check(template: str, quote: str | None, period: str | None) -> list[str]:
    """Reasons a literal number is not allowed: each must be in the quote or be a year in period."""
    parsed = parse_period(period) if period else None
    in_quote = find_numbers(quote) if quote else []
    problems: list[str] = []
    for n in literal_numbers(template):
        if any(same_number(n, q) for q in in_quote):
            continue
        if is_year(n):
            y = int(n.value)
            if (parsed and parsed.start.year <= y <= parsed.end.year) or (
                period and str(y) in period
            ):
                continue
        problems.append(f"number {n.raw!r} is not in the quote")
    quote_words = {w.lower() for w in _NUMBER_WORDS.findall(quote or "")}
    for w in dict.fromkeys(
        m.lower() for m in _NUMBER_WORDS.findall(PLACEHOLDER.sub(" ", template))
    ):
        if w not in quote_words:
            problems.append(f"number word {w!r} is not in the quote")
    return problems


def _contains(haystack: str, needle: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def entity_check(cand: CandidateClaim, refs: list[FigureRef], ev_text: str | None) -> str | None:
    entity = (cand.entity or "").strip()
    if not entity:
        return "no entity"
    want = normalize(entity)
    if cand.quote and _contains(normalize(cand.quote), want):
        return None
    if ev_text and _contains(normalize(ev_text), want):
        return None
    if any(normalize(r.entity) == want for r in refs):
        return None
    return f"entity {entity!r} is not in the evidence or the figures"


def digits_check(cand: CandidateClaim) -> str | None:
    """Entity and metric are labels; digits other than a year in them are refused."""
    for name, value in (("entity", cand.entity), ("metric", cand.metric)):
        if value and re.search(r"\d", _YEAR_TOKEN.sub(" ", value)):
            return f"{name} {value!r} contains digits"
    return None


def _overlap(a: Period, b: Period) -> bool:
    return a.start <= b.end and b.start <= a.end


def _near_quote(p: Period, window: str) -> bool:
    """The claim's year, and its month when the period is one, are near the quote."""
    if not any(re.search(rf"(?<!\d){y}(?!\d)", window) for y in {p.start.year, p.end.year}):
        return False
    if p.type != "month":
        return True
    m, y = p.start.month, p.start.year
    name = _MONTHS[m - 1]
    return (
        re.search(rf"\b{name}\b|\b{name[:3]}\b", window) is not None
        or re.search(rf"(?<!\d){y}[-/]0?{m}(?!\d)|(?<!\d)0?{m}[-/]{y}(?!\d)", window) is not None
    )


def period_check(
    cand: CandidateClaim, refs: list[FigureRef], quote: str | None, text: str | None
) -> str | None:
    if not (cand.period or "").strip():
        if cand.claim_type in _PERIOD_REQUIRED:
            return "no period"
        return None
    p = parse_period(cand.period or "")
    if p is None:
        return f"period {cand.period!r} not understood"
    if refs:  # figures: the period must overlap every figure's
        for r in refs:
            fp = parse_period(r.period_label)
            if fp is None or not _overlap(p, fp):
                return (
                    f"period {cand.period!r} does not overlap figure {r.label} ({r.period_label})"
                )
        return None
    window = quote_window(quote, text, _WINDOW) if quote and text else None
    if window is not None and _near_quote(p, window):
        return None
    if cand.claim_type == "annual" and text and _near_quote(p, normalize(text)):
        return None
    return f"period {cand.period!r} is not stated in or near the quote"


def figure_checks(
    cand: CandidateClaim, refs: list[FigureRef], shown: str, fbook: FigureBook
) -> list[str]:
    """Code checks for claims built on figures: entity in the text, direction against the sign."""
    problems: list[str] = []
    low = shown.lower()
    for r in refs:
        if not _contains(low, r.entity.lower()):
            problems.append(f"figure {r.label} is for {r.entity!r}, which the claim does not name")
    up, down = UP_WORDS.search(shown), DOWN_WORDS.search(shown)
    values = {f.id: f.value for f in fbook.figures}
    for r in refs:
        v = values.get(r.figure_id)
        if r.kind not in CHANGE_KINDS or v is None or not (up or down):
            continue
        if (up and v <= 0) or (down and v >= 0):
            problems.append(f"direction word contradicts the sign of figure {r.label}")
    return problems


def _reference_date(ev: TextEvidence | None, refs: list[FigureRef]) -> date | None:
    if ev is not None:
        return ev.published
    dates: list[date] = []
    for r in refs:
        p = parse_period(r.period_label)
        d = r.published or (p.end if p else None)
        if d is not None:
            dates.append(d)
    return min(dates) if dates else None


def _one(
    i: int,
    cand: CandidateClaim,
    book: EvidenceBook,
    figures: FigurePack,
    fbook: FigureBook,
    tiers: dict[str, int],
    stale_days: dict[str, int],
    today: date,
) -> Claim:
    reasons: list[str] = []
    quote = (cand.quote or "").strip() or None
    by_label = {r.label: r for r in figures.refs}
    labels = [label.strip().upper() for label in cand.figures]
    refs = [by_label[label] for label in labels if label in by_label]
    for label in labels:
        if label not in by_label:
            reasons.append(f"unknown figure label {label}")
    for m in PLACEHOLDER.finditer(cand.text_template):
        label = f"F{int(m[1])}"
        if label not in by_label and f"unknown figure label {label}" not in reasons:
            reasons.append(f"unknown figure label {label}")
        if label in by_label and label not in labels:
            labels.append(label)
            refs.append(by_label[label])
    text = render(cand.text_template, figures, fbook)
    ev = book.get(cand.evidence) if cand.evidence else None
    ev_text = book.text(ev) if ev is not None else None
    hard = bool(reasons) or text is None
    if text is None and not reasons:
        reasons.append("template cannot be rendered")
    grounded = False
    if quote is not None:
        if ev is None or ev_text is None:
            reasons.append(f"unknown evidence label {cand.evidence!r}")
        elif quote_in_text(quote, ev_text):
            grounded = True
        else:
            reasons.append("quote not found in the evidence text")
    elif not refs and not reasons:
        reasons.append("no quote and no figure")
    speculation = quote is None and not refs and not hard
    numbers = numbers_check(cand.text_template, quote, cand.period) if not speculation else []
    reasons.extend(numbers)
    ent = entity_check(cand, refs, ev_text) if not speculation else None
    per = (
        period_check(cand, refs, quote if grounded else None, ev_text) if not speculation else None
    )
    extra = [] if speculation or text is None else figure_checks(cand, refs, text, fbook)
    digits = digits_check(cand)
    if digits:
        extra.append(digits)
    reasons.extend(r for r in (ent, per, *extra) if r)
    legal_bad = cand.claim_type == "legal_status" and (
        cand.legal_stage is None or cand.legal_date is None
    )
    if legal_bad:
        reasons.append("legal_status claim needs a legal stage and a date")
    shown = text if text is not None else cand.text_template
    sensitive = cand.names_person or cand.alleges_wrongdoing or _SENSITIVE.search(shown) is not None
    tier_list = ([tier_for(ev.final_url, tiers)] if ev is not None else []) + [r.tier for r in refs]
    tier = max(tier_list) if tier_list else 4
    if sensitive and tier != 1:
        reasons.append("sensitive claim needs a tier 1 source")
    reference = _reference_date(ev if quote is not None else None, refs)
    stale = is_stale(cand.claim_type, reference, today, stale_days)
    generic = any(r.generic for r in refs)
    if stale:
        reasons.append("stale for its claim type")
    if generic:
        reasons.append("generic extraction")
    grounded_ok = grounded or (quote is None and bool(refs) and text is not None)
    failed = (
        not grounded_ok
        or bool(numbers)
        or ent is not None
        or per is not None
        or bool(extra)
        or legal_bad
        or (sensitive and tier != 1)
        or text is None
    )
    status = "speculation" if speculation else "refused" if failed else "inference"
    return Claim(
        id=f"C{i}",
        text=shown,
        text_template=cand.text_template,
        status=status,
        reasons=reasons,
        evidence_id=ev.id if ev is not None and quote is not None else None,
        figure_ids=[r.figure_id for r in refs],
        quote=quote,
        quote_grounded=grounded,
        numbers_ok=not numbers and not speculation,
        entity_period_ok=ent is None and per is None and not extra and not speculation,
        tier=tier,
        claim_type=cand.claim_type,
        entity=cand.entity,
        metric=cand.metric,
        period=cand.period,
        legal_stage=cand.legal_stage,
        legal_date=cand.legal_date,
        sensitive=sensitive,
        stale=stale,
        generic=generic,
        published=reference,
    )


def verify_static(
    cands: list[CandidateClaim],
    book: EvidenceBook,
    figures: FigurePack,
    fbook: FigureBook,
    *,
    tiers: dict[str, int],
    stale_days: dict[str, int],
    today: date,
) -> list[Claim]:
    """Ids are C1.. in candidate order, refused claims included (so the record is complete)."""
    return [
        _one(i, c, book, figures, fbook, tiers, stale_days, today)
        for i, c in enumerate(cands, start=1)
    ]
