"""Engine parameters (ruleset.md §1 + Appendix A).

These are the SHIPPED GENERIC DEFAULTS — locked values from the spec plus reasoned
starting-point defaults. They are NOT personal advice. Per-user overrides live in the
database and are entered through the UI; this module only supplies the fallback shape.

`base_unit` and `tier_weight` have NO safe default (they depend on your budget and your
chosen names), so `Params` requires them explicitly. The engine fails closed if they are
missing — it never sizes a buy off a guessed budget.
"""

from __future__ import annotations

from dataclasses import dataclass

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

    # --- Watchlist Opportunity (unheld names) ---
    watch_opportunity_min_score: float = 8.0              # [DEFAULT] min opportunity_score to fire
    watch_starter_mult: float = 1.0                       # [DEFAULT] flat starter size, ×base_for(tier)

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
    # One core order per run, rotating between core lines, instead of one order per line
    # every run. Same dollars into the core; pays the venue fee minimum once, not N times.
    core_rotate: bool = True                              # [DEFAULT]

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

    # --- Transaction costs (Rule 8) — IBKR Tiered, USD, GST 18% on commission ---
    # Fee-schedule figures, not preferences: change them only when IBKR's sheet changes.
    commissions_on: bool = True                           # [DEFAULT] show/charge est. costs
    gst_rate: float = 0.18                                # [LOCKED] Indian GST on commission
    lse_commission_pct: float = 0.0005                    # [LOCKED] 0.05% of trade value
    lse_commission_min: float = 1.70                      # [LOCKED] USD floor per order
    lse_exchange_min: float = 0.14                        # [LOCKED] ~GBP 0.10 ETF min, in USD
    lse_clearing: float = 0.08                            # [LOCKED] ~GBP 0.06, in USD
    us_commission_per_share: float = 0.0035               # [LOCKED] USD/share
    us_commission_min: float = 0.35                       # [LOCKED] USD floor per order
    us_commission_max_pct: float = 0.01                   # [LOCKED] tiered max, 1% of value
    us_clearing_per_share: float = 0.00020                # [LOCKED] USD/share
    # Which IBKR plan this account is on. Individual (usually older) fills can override it.
    commission_plan: str = "tiered"                       # [DEFAULT] "tiered" | "fixed"
    # FIXED plan: one all-in commission — exchange and clearing are bundled in, not added.
    fixed_us_per_share: float = 0.005                     # [LOCKED] USD/share
    fixed_us_min: float = 1.00                            # [LOCKED] USD floor per order
    fixed_us_max_pct: float = 0.01                        # [LOCKED] 1% of trade value
    fixed_lse_pct: float = 0.0005                         # [DEFAULT] 0.05% of trade value
    # 0 = NOT CONFIGURED. IBKR's LSE fixed-plan minimum was not verified when this shipped;
    # costing a fixed-plan LSE order fails loudly rather than guessing (see estimate_order_cost).
    fixed_lse_min: float = 0.0                            # [DEFAULT] set from your statement
    # Per-order floor: an order this small is not worth placing at IBKR's fee minimums.
    # 0 disables. An order within `min_order_roundup_max`× of the floor is rounded UP to
    # it; anything smaller is deferred (or shown flagged — see _min_order in pipeline).
    min_order_dollars: float = 0.0                        # [DEFAULT] off; broker-specific
    min_order_roundup_max: float = 1.25                   # [DEFAULT] never inflate >25%
    # Advisory line: flag an order whose cost exceeds this share of the order value.
    cost_warn_ratio: float = 0.005                        # [DEFAULT] 0.5% per order
    # Hard gate: suppress a BUY whose cost exceeds this share of the order. 0 disables.
    # Enabling it can permanently starve a leg whose BASE_i sits under the break-even
    # size (nothing accumulates the skipped dollars), so it ships OFF.
    cost_block_ratio: float = 0.0                         # [DEFAULT] off

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
