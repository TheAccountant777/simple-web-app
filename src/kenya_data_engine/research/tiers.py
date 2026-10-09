"""Source tiers by domain suffix, and staleness by claim type."""

from datetime import date
from urllib.parse import urlsplit

DEFAULT_TIER = 4
_STALE_WHEN_UNDATED = {"price", "rate", "statistic"}


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def tier_for(url: str, tiers: dict[str, int]) -> int:
    """The longest matching domain suffix wins; unknown domains are tier 4."""
    host = host_of(url)
    best_len, best = -1, DEFAULT_TIER
    for suffix, tier in tiers.items():
        s = suffix.lower().lstrip(".")
        if (host == s or host.endswith("." + s)) and len(s) > best_len:
            best_len, best = len(s), tier
    return best


def is_stale(
    claim_type: str, reference: date | None, today: date, stale_days: dict[str, int]
) -> bool:
    if reference is None:
        return claim_type in _STALE_WHEN_UNDATED
    limit = stale_days.get(claim_type, stale_days.get("other", 365))
    return (today - reference).days > limit
