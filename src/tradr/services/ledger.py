"""Ledger: derive positions + avg basis from append-only lots; manage the reserve.

Cost basis: flat-average (buys raise total cost & shares; sells reduce shares, leave
avg basis unchanged — realized P&L is out of scope, ruleset §Rule2 Tax). Everything is
recomputed from lots so there is never a hand-edited position to drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradr.db.models import Lot, ReserveEntry, UniverseRow


@dataclass
class DerivedPosition:
    ticker: str
    tier: str
    asset_class: str
    shares: float
    avg_basis: float


def record_trade(s: Session, *, ticker: str, side: str, qty: float, price: float,
                 trade_date: date, source_signal_id: int | None = None) -> Lot:
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if qty <= 0 or price <= 0:
        raise ValueError("qty and price must be positive")
    lot = Lot(ticker=ticker.upper(), side=side, qty=qty, price=price,
              trade_date=trade_date, source_signal_id=source_signal_id)
    s.add(lot)
    s.flush()
    return lot


def void_lot(s: Session, lot_id: int) -> None:
    """'Remove a buying' = mark voided (audit-preserving), never hard-delete."""
    lot = s.get(Lot, lot_id)
    if lot is None:
        raise KeyError(f"lot {lot_id} not found")
    lot.voided = True
    s.flush()


def _universe_map(s: Session) -> dict[str, UniverseRow]:
    return {u.ticker: u for u in s.scalars(select(UniverseRow)).all()}


def derive_positions(s: Session) -> list[DerivedPosition]:
    uni = _universe_map(s)
    lots = s.scalars(
        select(Lot).where(Lot.voided.is_(False)).order_by(Lot.trade_date, Lot.id)
    ).all()

    shares: dict[str, float] = {}
    total_cost: dict[str, float] = {}
    for lot in lots:
        t = lot.ticker
        sh = shares.get(t, 0.0)
        tc = total_cost.get(t, 0.0)
        avg = (tc / sh) if sh > 0 else 0.0
        if lot.side == "buy":
            shares[t] = sh + lot.qty
            total_cost[t] = tc + lot.qty * lot.price
        else:  # sell — reduce shares at current avg; basis unchanged
            new_sh = max(0.0, sh - lot.qty)
            shares[t] = new_sh
            total_cost[t] = avg * new_sh
    out = []
    for t, sh in shares.items():
        if sh <= 1e-9:
            continue
        u = uni.get(t)
        out.append(DerivedPosition(
            ticker=t,
            tier=(u.tier if u else "T1"),
            asset_class=(u.asset_class if u else "STOCK"),
            shares=round(sh, 6),
            avg_basis=round(total_cost[t] / sh, 6),
        ))
    return sorted(out, key=lambda p: p.ticker)


def reserve_balance(s: Session) -> float:
    return float(sum(e.amount for e in s.scalars(select(ReserveEntry)).all()))


def add_reserve(s: Session, *, kind: str, amount: float, note: str = "") -> ReserveEntry:
    entry = ReserveEntry(kind=kind, amount=amount, note=note)
    s.add(entry)
    s.flush()
    return entry
