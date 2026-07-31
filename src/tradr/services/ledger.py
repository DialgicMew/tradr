"""Ledger: derive positions + avg basis from append-only lots; manage the reserve.

Cost basis: flat-average (buys raise total cost & shares; sells reduce shares, leave
avg basis unchanged — realized P&L is out of scope, ruleset §Rule2 Tax). Everything is
recomputed from lots so there is never a hand-edited position to drift.

A buy's cost basis includes its brokerage (Rule 8): 1 share at $100 with a $1 fee cost
you $101, so that is what it contributes. Fills recorded before fees were tracked carry
fee=0 and so contribute price × qty exactly as before — nothing is retro-inflated. Sell
fees do NOT touch basis; they reduce proceeds (see signals.confirm_signal).
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
                 trade_date: date, source_signal_id: int | None = None,
                 fee: float = 0.0) -> Lot:
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if qty <= 0 or price <= 0:
        raise ValueError("qty and price must be positive")
    lot = Lot(ticker=ticker.upper(), side=side, qty=qty, price=price, fee=max(0.0, fee),
              trade_date=trade_date, source_signal_id=source_signal_id)
    s.add(lot)
    s.flush()
    return lot


def record_manual_trade(s: Session, *, ticker: str, side: str, qty: float, price: float,
                        trade_date: date, fee: float = 0.0,
                        fund_from_reserve: bool = False) -> tuple[Lot, float]:
    """Log a hand-entered fill with no signal behind it, optionally moving the reserve.

    `fund_from_reserve` is for a fill the user placed themselves outside the Alerts
    approve flow — a dip-buy funded from dry powder, or a sale whose proceeds should
    recycle back into it. Mirrors what `signals.confirm_signal` does for
    double_down/watch_opportunity and trim, minus the band/cap bookkeeping: a hand-entered
    fill isn't tied to a specific band, so there is nothing there to latch or decrement.
    Leave it False for an ordinary contribution — that must not touch the reserve.

    Movements are linked via `source_lot_id`, so voiding this lot later (`void_trade`)
    reverses them the same way it would a signal-confirmed fill.

    Returns (lot, reserve_delta) — the net change this call made to the reserve, 0.0
    when `fund_from_reserve` is False.
    """
    lot = record_trade(s, ticker=ticker, side=side, qty=qty, price=price,
                       trade_date=trade_date, fee=fee)
    if not fund_from_reserve:
        return lot, 0.0

    gross = qty * price
    delta = 0.0
    if side == "buy":
        add_reserve(s, kind="dd_draw", amount=-gross,
                   note=f"manual buy {ticker} (reserve-funded)", source_lot_id=lot.id)
        delta -= gross
    else:
        add_reserve(s, kind="trim_recycle", amount=gross,
                   note=f"manual sell {ticker} (reserve recycle)", source_lot_id=lot.id)
        delta += gross
    if fee:
        add_reserve(s, kind="fee", amount=-fee,
                   note=f"IBKR fees — manual {side} {ticker}", source_lot_id=lot.id)
        delta -= fee
    return lot, delta


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
            # Brokerage is part of what the shares cost you (Rule 8).
            total_cost[t] = tc + lot.qty * lot.price + (lot.fee or 0.0)
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


def fees_paid(s: Session, *, since: date | None = None) -> float:
    """Total estimated transaction cost across recorded fills (Rule 8).

    Voided lots are excluded — a removed trade never happened, so neither did its fee.
    Note this is the cost log, NOT a cash movement: whether a fee hit the reserve or came
    out of a monthly contribution is recorded in `reserve_entries`.
    """
    q = select(Lot).where(Lot.voided.is_(False))
    if since is not None:
        q = q.where(Lot.trade_date >= since)
    return float(sum((lot.fee or 0.0) for lot in s.scalars(q).all()))


def reserve_balance(s: Session) -> float:
    return float(sum(e.amount for e in s.scalars(select(ReserveEntry)).all()))


def add_reserve(s: Session, *, kind: str, amount: float, note: str = "",
                source_lot_id: int | None = None) -> ReserveEntry:
    entry = ReserveEntry(kind=kind, amount=amount, note=note, source_lot_id=source_lot_id)
    s.add(entry)
    s.flush()
    return entry


def reserve_entries_for_lot(s: Session, lot_id: int) -> list[ReserveEntry]:
    """Cash movements caused by one fill — what a void has to undo."""
    return list(s.scalars(
        select(ReserveEntry).where(ReserveEntry.source_lot_id == lot_id).order_by(ReserveEntry.id)
    ).all())
