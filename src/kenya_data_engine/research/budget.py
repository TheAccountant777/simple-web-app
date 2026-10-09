"""Budget ledger: dollars, search credits and seconds, split across research groups.

Every method is synchronous (no `await` inside), so each call is atomic under asyncio: two
concurrent agents can never both pass the same cap check.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

from kenya_data_engine.config import BudgetPreset, ResearchConfig
from kenya_data_engine.errors import BudgetExceeded, ConfigError

__all__ = [
    "GROUPS",
    "BudgetExceeded",
    "GroupState",
    "Ledger",
    "LedgerSnapshot",
    "Phase",
    "Reservation",
]

GROUPS = ("planner", "scouts", "claims", "challenge")  # order = reallocation order
_EPS = 1e-12


@dataclass(frozen=True)
class Reservation:
    id: int
    group: str
    usd: float


class Phase(StrEnum):
    RUNNING = "running"
    SOFT = "soft"
    WRAP_UP = "wrap_up"
    EXPIRED = "expired"


class GroupState(BaseModel):
    usd_cap: float
    usd_spent: float = 0.0
    usd_reserved: float = 0.0
    credits_cap: int
    credits_spent: int = 0
    closed: bool = False


class LedgerSnapshot(BaseModel):
    preset: str
    usd_total: float
    usd_spent: float
    credits_total: int
    credits_spent: int
    elapsed_s: float
    seconds_total: float
    phase: Phase
    groups: dict[str, GroupState]


class Ledger:
    def __init__(
        self,
        preset_name: str,
        preset: BudgetPreset,
        cfg: ResearchConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.preset_name = preset_name
        self.preset = preset
        self._clock = clock
        self._start = clock()
        credits = {
            g: math.floor(preset.search_credits * cfg.search_shares.get(g, 0.0) + 1e-9)
            for g in GROUPS
        }
        credits["scouts"] += preset.search_credits - sum(credits.values())  # floor remainder
        self._groups = {
            g: GroupState(usd_cap=preset.usd * cfg.shares.get(g, 0.0), credits_cap=credits[g])
            for g in GROUPS
        }
        self.protected = frozenset(cfg.protected)
        self._reservations: dict[int, Reservation] = {}
        self._next_id = 1

    @classmethod
    def from_config(cls, cfg: ResearchConfig, preset_name: str | None = None) -> "Ledger":
        name = preset_name or cfg.default_budget
        preset = cfg.presets.get(name)
        if preset is None:
            raise ConfigError(
                f"unknown budget preset {name!r}",
                hint=f"choose one of: {', '.join(sorted(cfg.presets))}",
            )
        return cls(name, preset, cfg)

    def _group(self, group: str) -> GroupState:
        try:
            return self._groups[group]
        except KeyError:
            raise ValueError(f"unknown budget group {group!r}; use one of {GROUPS}") from None

    def phase(self) -> Phase:
        frac = (self._clock() - self._start) / self.preset.seconds
        if frac < 0.6:
            return Phase.RUNNING
        if frac < 0.8:
            return Phase.SOFT
        if frac < 1.0:
            return Phase.WRAP_UP
        return Phase.EXPIRED

    def remaining_usd(self, group: str) -> float:
        g = self._group(group)
        return g.usd_cap - g.usd_spent - g.usd_reserved

    def reserve(self, group: str, usd: float) -> Reservation:
        g = self._group(group)
        if self.phase() is Phase.EXPIRED:
            raise BudgetExceeded(
                f"research time budget of {self.preset.seconds:.0f}s is used up",
                hint="use a deeper budget preset for more time",
            )
        if g.closed:
            raise BudgetExceeded(f"budget group {group!r} is closed", hint="no spend after close")
        if usd > self.remaining_usd(group) + _EPS:
            raise BudgetExceeded(
                f"{group} budget exhausted (${self.remaining_usd(group):.4f} left, "
                f"${usd:.4f} needed)",
                hint="use a deeper budget preset for more room",
            )
        g.usd_reserved += usd
        r = Reservation(self._next_id, group, usd)
        self._next_id += 1
        self._reservations[r.id] = r
        return r

    def settle(self, r: Reservation, actual_usd: float) -> None:
        if self._reservations.pop(r.id, None) is None:
            raise ValueError(f"reservation {r.id} is not outstanding")
        g = self._group(r.group)
        g.usd_reserved = max(0.0, g.usd_reserved - r.usd)
        g.usd_spent += actual_usd

    def charge_credits(self, group: str, credits: int) -> None:
        g = self._group(group)
        if self.phase() is Phase.EXPIRED:
            raise BudgetExceeded(
                f"research time budget of {self.preset.seconds:.0f}s is used up",
                hint="use a deeper budget preset for more time",
            )
        if g.closed or g.credits_spent + credits > g.credits_cap:
            raise BudgetExceeded(
                f"{group} search credits exhausted "
                f"({g.credits_cap - g.credits_spent} left, {credits} needed)",
                hint="use a deeper budget preset for more searches",
            )
        g.credits_spent += credits

    def close(self, group: str) -> None:
        """Retire a group; its unspent dollars and credits go to the next open group."""
        g = self._group(group)
        if g.closed:
            return
        left_usd = max(0.0, g.usd_cap - g.usd_spent - g.usd_reserved)
        left_credits = g.credits_cap - g.credits_spent
        g.closed = True
        g.usd_cap -= left_usd
        g.credits_cap -= left_credits
        for name in GROUPS[GROUPS.index(group) + 1 :]:
            nxt = self._groups[name]
            if not nxt.closed:
                nxt.usd_cap += left_usd
                nxt.credits_cap += left_credits
                return

    def snapshot(self) -> LedgerSnapshot:
        groups = {k: v.model_copy() for k, v in self._groups.items()}
        return LedgerSnapshot(
            preset=self.preset_name,
            usd_total=self.preset.usd,
            usd_spent=sum(g.usd_spent for g in groups.values()),
            credits_total=self.preset.search_credits,
            credits_spent=sum(g.credits_spent for g in groups.values()),
            elapsed_s=self._clock() - self._start,
            seconds_total=self.preset.seconds,
            phase=self.phase(),
            groups=groups,
        )
