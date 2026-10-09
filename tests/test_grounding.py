from kenya_data_engine.tools.grounding import normalize, quote_in_text


def test_normalize():
    assert normalize("  \u201cHi\u201d\u2014 \u2018x\u2019\n\tA\u2013B ") == "\"hi\"- 'x' a-b"


def test_grounding_tolerates_typography():
    assert quote_in_text("inflation rose to “4.4%” — KNBS", 'Inflation rose to "4.4%" - KNBS said')


def test_grounding_rejects_short_fuzzy():
    assert not quote_in_text("rate cut", "rates were held")


def test_grounding_rejects_fabricated_long_quote():
    assert not quote_in_text(
        "the central bank cut the rate to 7 percent",
        "the central bank held the rate at 9 percent citing inflation",
    )


def test_grounding_fuzzy_accepts_near_match_long_quote():
    assert quote_in_text(
        "the central bank held the rate at 9 percent, citing inflation",
        "Today the central bank held the rate at 9 percent citing inflation pressures.",
    )


def test_empty_quote_false():
    assert not quote_in_text("", "anything")
    assert not quote_in_text("   ", "anything")


def test_grounding_rejects_single_digit_difference():
    text = "The central bank held the rate at 9.5 percent citing inflation pressures today."
    quote = "The central bank held the rate at 9.6 percent citing inflation pressures today."
    assert len(quote) >= 70
    assert not quote_in_text(quote, text)


def test_grounding_rejects_quote_longer_than_text():
    text = "inflation rose to 4.4 percent in september"
    quote = text + " and the shilling collapsed against the dollar"
    assert not quote_in_text(quote, text)


def test_grounding_numbers_are_token_bounded():
    text = "the rate rose to 19.5 percent in the latest monthly survey of banks"
    assert not quote_in_text("rate rose to 9.5 percent in the latest monthly survey", text)
    assert not quote_in_text("rate rose to 9 percent in the latest monthly survey", text)
    assert quote_in_text("rate rose to 19.5 percent in the latest monthly survey", text)
