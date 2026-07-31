"""Config service: assemble engine Params/Flags/Macro from DB; seed generic defaults.

Ships ONLY generic Appendix-A defaults. `base_unit` and `tier_weight` are NOT seeded —
the engine fails closed until the user sets them via the setup wizard (ARCHITECTURE §4a).
"""

from __future__ import annotations

from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradr.db.models import FlagRow, MacroRow, ParamRow, UniverseRow
from tradr.engine import Flags, Macro, Params

# Generic, non-personal seed defaults (Appendix A). NOT advice. base_unit/tier_weight excluded.
DEFAULT_PARAMS: dict[str, tuple[object, str]] = {
    "snooze_days": (5, "DEFAULT"),
    "bands_stock": ([-0.20, -0.30, -0.40, -0.50], "LOCKED"),
    "bands_etf": ([-0.10, -0.20, -0.30, -0.40], "LOCKED"),
    "arm_stock": (-0.10, "LOCKED"),
    "arm_etf": (-0.05, "LOCKED"),
    "dd_cap_mult": (3.0, "LOCKED"),
    "dd_first_band_mult": (2.0, "DEFAULT"),
    "watch_opportunity_min_score": (8.0, "DEFAULT"),
    "watch_starter_mult": (1.0, "DEFAULT"),
    "trim_bands": ([1.20, 1.70, 2.20], "DEFAULT"),
    "trim_pct": (0.20, "DEFAULT"),
    "trim_arm_buffer": (0.20, "DEFAULT"),
    "trim_velocity_pct": (0.30, "DEFAULT"),
    "trim_velocity_window": (60, "DEFAULT"),
    "min_weight": (0.03, "DEFAULT"),
    "froth_threshold": (0.15, "DEFAULT"),
    "momentum_brake_ret": (0.20, "LOCKED"),
    "brake_release_pct": (0.10, "DEFAULT"),
    "reserve_target": (0.20, "LOCKED"),
    "cash_ceiling": (0.05, "DEFAULT"),
    "auto_bank_5050": (True, "DEFAULT"),
    "max_holdings": (20, "LOCKED"),
    # Transaction costs (Rule 8) — IBKR Tiered fee schedule, USD, GST 18% on commission.
    "commissions_on": (True, "DEFAULT"),
    "gst_rate": (0.18, "LOCKED"),
    "lse_commission_pct": (0.0005, "LOCKED"),
    "lse_commission_min": (1.70, "LOCKED"),
    "lse_exchange_min": (0.14, "LOCKED"),
    "lse_clearing": (0.08, "LOCKED"),
    "us_commission_per_share": (0.0035, "LOCKED"),
    "us_commission_min": (0.35, "LOCKED"),
    "us_commission_max_pct": (0.01, "LOCKED"),
    "us_clearing_per_share": (0.00020, "LOCKED"),
    "commission_plan": ("tiered", "DEFAULT"),
    "fixed_us_per_share": (0.005, "LOCKED"),
    "fixed_us_min": (1.00, "LOCKED"),
    "fixed_us_max_pct": (0.01, "LOCKED"),
    "fixed_lse_pct": (0.0005, "DEFAULT"),
    "fixed_lse_min": (0.0, "DEFAULT"),   # 0 = unverified; set it from your statement
    # Off by default — a per-order floor depends on your broker AND your contribution size.
    "min_order_dollars": (0.0, "DEFAULT"),
    "min_order_roundup_max": (1.25, "DEFAULT"),
    "cost_warn_ratio": (0.005, "DEFAULT"),
    "cost_block_ratio": (0.0, "DEFAULT"),
    "core_rotate": (True, "DEFAULT"),
}

DEFAULT_FLAGS = {"emergency_fund_ok": False, "idle_cash": 0.0}

_TUPLE_KEYS = {"bands_stock", "bands_etf", "trim_bands", "core_tiers"}


def seed_defaults(s: Session) -> None:
    for key, (value, status) in DEFAULT_PARAMS.items():
        if s.get(ParamRow, key) is None:
            s.add(ParamRow(key=key, value=value, status=status))
    for key, value in DEFAULT_FLAGS.items():
        if s.get(FlagRow, key) is None:
            s.add(FlagRow(key=key, value=value))
    s.flush()


def set_param(s: Session, key: str, value: object) -> None:
    row = s.get(ParamRow, key)
    if row is None:
        s.add(ParamRow(key=key, value=value, status="USER"))
    else:
        row.value = value
        if row.status != "LOCKED":
            row.status = "USER"
    s.flush()


def get_all_params(s: Session) -> list[ParamRow]:
    return list(s.scalars(select(ParamRow).order_by(ParamRow.key)).all())


def is_configured(s: Session) -> bool:
    """True once required user config (base_unit + tier weights) is present."""
    return s.get(ParamRow, "base_unit") is not None and s.get(ParamRow, "tier_weight") is not None


def build_params(s: Session) -> Params:
    """Assemble the immutable engine Params from DB rows. Raises if not configured."""
    if not is_configured(s):
        raise RuntimeError("engine not configured: set base_unit and tier_weight first")
    rows = {r.key: r.value for r in get_all_params(s)}
    valid = {f.name for f in fields(Params)}
    kwargs: dict[str, object] = {}
    for k, v in rows.items():
        if k not in valid:
            continue
        kwargs[k] = tuple(v) if k in _TUPLE_KEYS and isinstance(v, list) else v
    return Params(**kwargs)  # type: ignore[arg-type]


def build_flags(s: Session) -> Flags:
    rows = {r.key: r.value for r in s.scalars(select(FlagRow)).all()}
    return Flags(
        emergency_fund_ok=bool(rows.get("emergency_fund_ok", False)),
        idle_cash=float(rows.get("idle_cash", 0.0)),
    )


def latest_macro(s: Session) -> Macro:
    row = s.scalars(select(MacroRow).order_by(MacroRow.id.desc())).first()
    if row is None:
        return Macro()
    return Macro(sp_fwd_pe=row.sp_fwd_pe, sp_10yr_avg_fwd_pe=row.sp_10yr_avg_fwd_pe)


def core_tiers(s: Session) -> set[str]:
    row = s.get(ParamRow, "core_tiers")
    return set(row.value) if row and row.value else {"ETF"}


def avoid_set(s: Session) -> frozenset[str]:
    return frozenset(
        u.ticker for u in s.scalars(select(UniverseRow).where(UniverseRow.is_avoid.is_(True))).all()
    )
