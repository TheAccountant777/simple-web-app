import pytest

from kenya_data_engine.data.csvsafe import safe_cell, write_csv


@pytest.mark.parametrize("cell", ["=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\tx", "\rx"])
def test_formula_triggers_are_prefixed(cell: str) -> None:
    assert safe_cell(cell) == "'" + cell


@pytest.mark.parametrize("cell", ["-3.2", "+5", "180.66", "-.5", "1e5", "Nairobi", "", "a=b", 7])
def test_numbers_and_plain_text_are_left_alone(cell: object) -> None:
    assert safe_cell(cell) == str(cell)


def test_write_csv_quotes_and_escapes() -> None:
    out = write_csv([['=HYPERLINK("x")', "-3.2", "a,b"]], ["period", "value", "note"])
    assert out == 'period,value,note\n"\'=HYPERLINK(""x"")",-3.2,"a,b"\n'
