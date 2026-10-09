import pytest

from kenya_data_engine.config import load_config
from kenya_data_engine.research.budget import BudgetExceeded, Ledger, Phase


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_home, clock) -> Ledger:
    cfg = load_config(tmp_home).research
    return Ledger("standard", cfg.presets["standard"], cfg, clock=clock)


def test_caps_from_standard(ledger):
    g = ledger.snapshot().groups
    assert g["planner"].usd_cap == pytest.approx(0.0225)
    assert g["scouts"].usd_cap == pytest.approx(0.0825)
    assert g["claims"].usd_cap == pytest.approx(0.0375)
    assert g["challenge"].usd_cap == pytest.approx(0.0075)
    assert [g[k].credits_cap for k in ("planner", "scouts", "claims", "challenge")] == [6, 17, 0, 2]


def test_from_config(tmp_home):
    cfg = load_config(tmp_home).research
    assert Ledger.from_config(cfg).snapshot().preset == "standard"
    assert Ledger.from_config(cfg, "lean").snapshot().credits_total == 10


def test_from_config_unknown_preset(tmp_home):
    from kenya_data_engine.errors import ConfigError

    with pytest.raises(ConfigError):
        Ledger.from_config(load_config(tmp_home).research, "huge")


def test_reserve_settle_accounting(ledger):
    r = ledger.reserve("scouts", 0.01)
    before = ledger.remaining_usd("scouts")
    ledger.settle(r, 0.004)
    assert ledger.remaining_usd("scouts") == pytest.approx(before + 0.006)
    assert ledger.snapshot().usd_spent == pytest.approx(0.004)
    with pytest.raises(ValueError):
        ledger.settle(r, 0.0)


def test_parallel_reservations_never_overshoot(ledger):
    ledger.reserve("scouts", 0.03)
    ledger.reserve("scouts", 0.03)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("scouts", 0.03)
    g = ledger.snapshot().groups["scouts"]
    assert g.usd_spent + g.usd_reserved <= g.usd_cap


def test_protected_groups_untouched(ledger):
    ledger.reserve("scouts", 0.0825)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("scouts", 0.001)
    assert ledger.remaining_usd("claims") == pytest.approx(0.0375)


def test_close_reallocates_forward(ledger):
    ledger.settle(ledger.reserve("planner", 0.01), 0.01)
    ledger.close("planner")
    g = ledger.snapshot().groups
    assert g["scouts"].usd_cap == pytest.approx(0.0825 + 0.0125)
    assert g["scouts"].credits_cap == 17 + 6
    assert g["planner"].closed
    with pytest.raises(BudgetExceeded):
        ledger.reserve("planner", 0.001)
    ledger.close("planner")  # idempotent
    assert ledger.snapshot().groups["scouts"].credits_cap == 23


def test_close_skips_closed_groups_and_last_group_drops(ledger):
    ledger.close("scouts")
    ledger.close("claims")
    ledger.close("challenge")  # nothing after it: leftover is dropped
    g = ledger.snapshot().groups
    assert g["claims"].usd_cap == pytest.approx(0.0)
    assert g["challenge"].usd_cap == pytest.approx(0.0)
    assert g["challenge"].credits_cap == 0


def test_phases(ledger, clock):
    for frac, phase in ((0.59, Phase.RUNNING), (0.6, Phase.SOFT), (0.8, Phase.WRAP_UP)):
        clock.t = 1000.0 + 300 * frac
        assert ledger.phase() is phase
    clock.t = 1000.0 + 300
    assert ledger.phase() is Phase.EXPIRED
    with pytest.raises(BudgetExceeded):
        ledger.reserve("scouts", 0.001)
    assert ledger.snapshot().elapsed_s == pytest.approx(300)


def test_charge_credits_over_cap_raises(ledger):
    ledger.charge_credits("scouts", 17)
    with pytest.raises(BudgetExceeded):
        ledger.charge_credits("scouts", 1)
    with pytest.raises(BudgetExceeded):
        ledger.charge_credits("claims", 1)
    assert ledger.snapshot().credits_spent == 17


def test_unknown_group(ledger):
    with pytest.raises(ValueError):
        ledger.reserve("nope", 0.01)


def test_charge_credits_refused_when_expired(ledger, clock):
    clock.t += 300
    with pytest.raises(BudgetExceeded):
        ledger.charge_credits("scouts", 1)
