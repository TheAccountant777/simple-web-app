from datetime import date, timedelta

from kenya_data_engine.research.tiers import is_stale, tier_for

TIERS = {"knbs.or.ke": 1, "epra.go.ke": 1, "go.ke": 2, "cob.go.ke": 2, "reuters.com": 3}
STALE = {"price": 45, "rate": 45, "statistic": 75, "annual": 550, "other": 365}
TODAY = date(2026, 10, 9)


def test_tier_longest_suffix():
    assert tier_for("https://www.knbs.or.ke/x", TIERS) == 1
    assert tier_for("https://foo.go.ke/x", TIERS) == 2
    assert tier_for("https://cob.go.ke/x", TIERS) == 2
    assert tier_for("https://epra.go.ke/x", TIERS) == 1
    assert tier_for("https://example.com/x", TIERS) == 4
    assert tier_for("https://notknbs.or.ke/x", TIERS) == 4  # suffix must align on a dot


def test_is_stale_price_46_days():
    assert is_stale("price", TODAY - timedelta(days=46), TODAY, STALE)
    assert not is_stale("price", TODAY - timedelta(days=45), TODAY, STALE)


def test_is_stale_none_reference_by_type():
    for t in ("price", "rate", "statistic"):
        assert is_stale(t, None, TODAY, STALE)
    for t in ("annual", "legal_status", "event", "forecast", "other"):
        assert not is_stale(t, None, TODAY, STALE)
