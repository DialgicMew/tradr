"""Engine parameters (ruleset.md §1 + Appendix A).

These are the SHIPPED GENERIC DEFAULTS — locked values from the spec plus reasoned
starting-point defaults. They are NOT personal advice. Per-user overrides live in the
database and are entered through the UI; this module only supplies the fallback shape.

`base_unit` and `tier_weight` have NO safe default (they depend on your budget and your
chosen names), so `Params` requires them explicitly. The engine fails closed if they are
missing — it never sizes a buy off a guessed budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ladder / asset-class constants (ruleset Appendix A.1)
BANDS_STOCK: tuple[float, ...] = (-0.20, -0.30, -0.40, -0.50)   # [LOCKED]
BANDS_ETF: tuple[float, ...] = (-0.10, -0.20, -0.30, -0.40)     # [LOCKED]
TRIM_BANDS: tuple[float, ...] = (1.20, 1.70, 2.20)             # [DEFAULT] raised after backtest (App. B)


@dataclass(frozen=True)
class Params:
    """Immutable parameter set for one engine run.

    Status tags refer to ruleset Appendix A: [LOCKED] fixed during design,
    [DEFAULT] reasoned starting point the user may override.
    """

    # --- No default on purpose (user must set; engine fails closed otherwise) ---
    base_unit: float                       # BASE_UNIT — your painless monthly unit
    tier_weight: dict[str, float]          # e.g. {"T1": 2.0, "T2": 1.0, "ETF": 18.0}

    # --- Double-down (Rule 1) ---
    bands_stock: tuple[float, ...] = BANDS_STOCK          # [LOCKED]
    bands_etf: tuple[float, ...] = BANDS_ETF              # [LOCKED]
    arm_stock: float = -0.10                              # [LOCKED] ladder re-arm line
    arm_etf: float = -0.05                                # [LOCKED]
    dd_cap_mult: float = 3.0                              # [LOCKED] 3x base hard cap
    dd_first_band_mult: float = 2.0                       # [DEFAULT] first band ~2x base

    # --- Trim (Rule 2) ---
    trim_bands: tuple[float, ...] = TRIM_BANDS            # [DEFAULT] +120/+170/+220%
    trim_pct: float = 0.20                                # [DEFAULT] 20% of current shares
    trim_arm_buffer: float = 0.20                         # [DEFAULT] 20pp re-arm buffer
    trim_velocity_pct: float = 0.30                       # [DEFAULT] min 60d trailing gain to trim
    trim_velocity_window: int = 60                        # [DEFAULT] trading days (advisory metadata)
    min_weight: float = 0.03                              # [DEFAULT] never trim below 3% (App. B)

    # --- Core allocation (Rule 3) ---
    core_low: float = 0.40                                # steer INTO core below this
    core_high: float = 0.50                               # stop adding above this
    etf_tier_key: str = "ETF"                             # primary core tier (deploy target)
    core_tiers: tuple[str, ...] = ("ETF",)                # all tiers counted as the 40–50% core

    def is_core(self, tier: str) -> bool:
        return tier in self.core_tiers

    # --- Valuation / momentum (Rules 4 & 7) ---
    froth_threshold: float = 0.15                         # [DEFAULT] +15% over 10yr avg
    momentum_brake_ret: float = 0.20                      # [DEFAULT] 30d return > +20%
    brake_release_pct: float = 0.10                       # [DEFAULT] 10% off spike peak

    # --- Reserve / cash (Rules 3 & 6) ---
    reserve_target: float = 0.20                          # [LOCKED] 20% earmarked powder
    cash_ceiling: float = 0.05                            # [DEFAULT] idle-cash nudge line
    auto_bank_5050: bool = True                           # 50/50 auto-bank toggle

    # --- Universe / lifecycle ---
    max_holdings: int = 20                                # [LOCKED] individual names (ETF excl.)
    snooze_days: int = 5                                  # [DEFAULT] quiet after SKIP

    def bands_for(self, asset_class: str) -> tuple[float, ...]:
        return self.bands_etf if asset_class == "ETF" else self.bands_stock

    def arm_for(self, asset_class: str) -> float:
        return self.arm_etf if asset_class == "ETF" else self.arm_stock

    def base_for(self, tier: str) -> float:
        """BASE_i = BASE_UNIT × tier_weight (ruleset Rule 7)."""
        if tier not in self.tier_weight:
            raise KeyError(f"no tier_weight configured for tier {tier!r}")
        return self.base_unit * self.tier_weight[tier]


def demo_params() -> Params:
    """Generic, non-personal params for tests/backtests. NOT advice."""
    return Params(base_unit=1000.0, tier_weight={"T1": 2.0, "T2": 1.0, "ETF": 18.0})
