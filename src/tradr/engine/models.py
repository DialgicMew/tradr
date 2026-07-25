"""Pure data models for the Engine Core (ruleset.md §0, §2).

Everything here is plain, immutable-ish data. The engine consumes a `Snapshot` and
returns an `EngineResult`. No I/O, no clock, no DB — time and prices are passed IN so the
same inputs always yield the same outputs (deterministic + backtestable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BandStatus(str, Enum):
    ARMED = "ARMED"      # eligible to fire
    PENDING = "PENDING"  # a signal is out, awaiting user confirm (do not re-fire)
    FIRED = "FIRED"      # latched (confirmed DONE); won't re-fire until re-armed


class Ladder(str, Enum):
    DD = "double_down"
    TRIM = "trim"


# --- Inputs ------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    ticker: str
    tier: str                 # "T1" | "T2" | "ETF"
    shares: float
    avg_basis: float          # average cost per share
    asset_class: str = "STOCK"  # "STOCK" | "ETF" (drives which DD ladder applies)


@dataclass(frozen=True)
class MarketData:
    ticker: str
    price: float
    high_52w: float
    ret_30d: float            # trailing 30-day return, computed by the ADAPTER (not engine)
    ret_60d: float = 0.0      # trailing 60-day gain — trim velocity gate (Rule 2)
    fwd_pe: float | None = None


@dataclass(frozen=True)
class Macro:
    sp_fwd_pe: float | None = None
    sp_10yr_avg_fwd_pe: float | None = None


@dataclass
class BandState:
    """Per-ticker ladder state (ruleset §0 — engine owns this state).

    `dd_status` / `trim_status` map a band threshold -> BandStatus.
    `dd_deployed_mult` tracks cumulative double-down (in units of BASE_i) this crash
    cycle, for the 3x cap. `spike_peak` supports the momentum brake release.
    """

    ticker: str
    dd_status: dict[float, BandStatus] = field(default_factory=dict)
    trim_status: dict[float, BandStatus] = field(default_factory=dict)
    dd_deployed_mult: float = 0.0
    spike_peak: float | None = None


@dataclass(frozen=True)
class Flags:
    emergency_fund_ok: bool = False
    idle_cash: float = 0.0    # uncommitted cash the user has reported (Rule 6)


@dataclass(frozen=True)
class Snapshot:
    """The complete, immutable input to one engine run."""

    as_of: str                        # ISO date string (passed in, never read from a clock)
    is_dca_day: bool                  # True only on the scheduled monthly DCA date (Rule 7)
    positions: list[Position]
    market: dict[str, MarketData]     # keyed by ticker
    band_state: dict[str, BandState]  # keyed by ticker
    reserve_balance: float
    params: object                  # tradr.engine.params.Params (avoid import cycle)
    macro: Macro = field(default_factory=Macro)
    flags: Flags = field(default_factory=Flags)
    avoid: frozenset[str] = frozenset()
    watchlist: list[Position] = field(default_factory=list)  # not-yet-owned buy candidates


# --- Outputs -----------------------------------------------------------------


@dataclass
class Signal:
    """One advisory notification (ruleset §0: {cause, action, size_hint, asset, band})."""

    ticker: str
    rule: str                 # "double_down" | "trim" | "base_dca" | "cash_floor" | "replace"
    action: str               # "BUY" | "SELL" | "DEPLOY" | "REPLACE"
    cause: str                # human-readable trigger
    size_shares: float = 0.0
    size_dollars: float = 0.0
    band: float | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)  # structured numbers for human-readable UI


@dataclass
class EngineResult:
    signals: list[Signal] = field(default_factory=list)
    # Proposed state after this run (Signal Service commits it on user confirm).
    band_state: dict[str, BandState] = field(default_factory=dict)
    reserve_balance: float = 0.0
    diagnostics: list[str] = field(default_factory=list)
