from decimal import Decimal

import pytest

from kenya_data_engine.tools.numbers import find_numbers, parse_number, same_number


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("Sh1.2bn", "1200000000", "KES"),
        ("KSh 180.66", "180.66", "KES"),
        ("Kshs 5,000", "5000", "KES"),
        ("KES 2 million", "2000000", "KES"),
        ("US$ 3.5 billion", "3500000000", "USD"),
        ("$12", "12", "USD"),
        ("8 per cent", "8", "pct"),
        ("8%", "8", "pct"),
        ("8.5 percent", "8.5", "pct"),
        ("1,234.5", "1234.5", "none"),
        ("(3.2)", "-3.2", "none"),
        ("-3.2", "-3.2", "none"),
        ("\u20133.2", "-3.2", "none"),
        ("-Sh5", "-5", "KES"),
        ("2.5 trillion", "2500000000000", "none"),
        ("12k", "12000", "none"),
        ("4 thousand", "4000", "none"),
        ("1,200 million shillings", "1200000000", "KES"),
        ("300 shs", "300", "KES"),
        ("  7  ", "7", "none"),
    ],
)
def test_parse_number(text, value, unit):
    n = parse_number(text)
    assert n is not None
    assert n.value == Decimal(value) and n.unit == unit


@pytest.mark.parametrize(
    "text", ["\u2013", "-", "n/a", "N/A", "..", "", "   ", "abc", "(3.2", "12 apples", "5-6"]
)
def test_parse_number_none(text):
    assert parse_number(text) is None


def test_scale_recorded():
    n = parse_number("Sh1.2bn")
    assert n is not None and n.scale == 9 and n.raw == "Sh1.2bn"


def test_find_numbers_keeps_full_tokens():
    assert [n.value for n in find_numbers("rose to 19.5% from 9.5%")] == [
        Decimal("19.5"),
        Decimal("9.5"),
    ]


def test_find_numbers_running_text():
    found = find_numbers(
        "Revenue was Sh1.2 billion (up 8 per cent) versus KSh 900 million; 1,234.5 units."
    )
    assert [(n.value, n.unit) for n in found] == [
        (Decimal("1200000000"), "KES"),
        (Decimal("8"), "pct"),
        (Decimal("900000000"), "KES"),
        (Decimal("1234.5"), "none"),
    ]
    assert find_numbers("no figures here") == []
    assert find_numbers("(2026)")[0].value == Decimal("2026")


def test_same_number():
    a, b = parse_number("Sh1.2 billion"), parse_number("1,200 million shillings")
    assert a and b and same_number(a, b)
    c, d = parse_number("9.5%"), parse_number("19.5%")
    assert c and d and not same_number(c, d)
    e = parse_number("9.5")
    assert c and e and not same_number(c, e)


def test_scale_attachment_rules():
    assert [(n.value, n.scale) for n in find_numbers("5 m tall")] == [(Decimal("5"), 0)]
    assert [n.value for n in find_numbers("5m and 2k")] == [Decimal("5000000"), Decimal("2000")]
    n = parse_number("Sh 1.2 bn")
    assert n is not None and n.value == Decimal("1200000000") and n.unit == "KES"
    assert parse_number("5 m") is None
    assert [n.value for n in find_numbers("3 b profit")] == [Decimal("3")]
