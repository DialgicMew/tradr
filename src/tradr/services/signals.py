"""Signal service — bridges the pure engine and the database.

- build_snapshot: assemble the engine input from DB + market adapter.
- persist_result: store proposed band state + new PENDING signals (deduped).
- confirm_signal / skip_signal: the §2 lifecycle; DONE updates ledger, latches bands,
  moves the reserve. Nothing is ever assumed executed — only user confirmation advances.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradr.db.models import (
    EngineStateRow,
    MarketSnapshotRow,
    SignalRow,
    UniverseRow,
)
from tradr.engine import (
    BandState,
    BandStatus,
    Position,
    Snapshot,
)
from tradr.engine import (
    evaluate as engine_evaluate,
)
from tradr.market.adapter import MarketDataProvider
from tradr.services import config_service as cfg
from tradr.services import ledger as ledger_svc

# --- band-state (de)serialisation: JSON uses string keys ---------------------

def _load_band_state(row: EngineStateRow) -> BandState:
    return BandState(
        ticker=row.ticker,
        dd_status={float(k): BandStatus(v) for k, v in (row.dd_status or {}).items()},
        trim_status={float(k): BandStatus(v) for k, v in (row.trim_status or {}).items()},
        dd_deployed_mult=row.dd_deployed_mult,
        spike_peak=row.spike_peak,
    )


def _save_band_state(s: Session, bs: BandState) -> None:
    row = s.get(EngineStateRow, bs.ticker) or EngineStateRow(ticker=bs.ticker)
    row.dd_status = {str(k): v.value for k, v in bs.dd_status.items()}
    row.trim_status = {str(k): v.value for k, v in bs.trim_status.items()}
    row.dd_deployed_mult = bs.dd_deployed_mult
    row.spike_peak = bs.spike_peak
    s.merge(row)


# --- snapshot ---------------------------------------------------------------

def build_snapshot(s: Session, provider: MarketDataProvider, *, as_of: date,
                   is_dca_day: bool) -> Snapshot:
    positions = [
        Position(ticker=p.ticker, tier=p.tier, shares=p.shares,
                 avg_basis=p.avg_basis, asset_class=p.asset_class)
        for p in ledger_svc.derive_positions(s)
    ]
    held = {p.ticker for p in positions}
    universe = {u.ticker: u for u in s.scalars(select(UniverseRow)).all()}
    tickers = held | {t for t, u in universe.items() if u.subscribed and not u.is_avoid}

    market = {}
    for t in sorted(tickers):
        md = provider.get_stock(t)
        if md is None:
            continue
        market[t] = md
        s.add(MarketSnapshotRow(ticker=t, as_of=as_of, price=md.price, high_52w=md.high_52w,
                                ret_30d=md.ret_30d, ret_60d=md.ret_60d, fwd_pe=md.fwd_pe))

    band_state = {r.ticker: _load_band_state(r) for r in s.scalars(select(EngineStateRow)).all()}

    # Subscribed-but-unheld tickers → watchlist (lets the core be bootstrapped from 0 shares).
    watchlist = [
        Position(ticker=t, tier=u.tier, shares=0.0, avg_basis=0.0, asset_class=u.asset_class)
        for t, u in universe.items()
        if u.subscribed and not u.is_avoid and t not in held
    ]

    return Snapshot(
        as_of=as_of.isoformat(),
        is_dca_day=is_dca_day,
        positions=positions,
        market=market,
        band_state=band_state,
        reserve_balance=ledger_svc.reserve_balance(s),
        params=cfg.build_params(s),
        macro=cfg.latest_macro(s),
        flags=cfg.build_flags(s),
        avoid=cfg.avoid_set(s),
        watchlist=watchlist,
    )


# --- persist engine output ---------------------------------------------------

def _has_open_signal(s: Session, ticker: str, rule: str, band: float | None) -> bool:
    q = select(SignalRow).where(
        SignalRow.ticker == ticker, SignalRow.rule == rule,
        SignalRow.state == "PENDING",
    )
    for row in s.scalars(q).all():
        if row.band == band:
            return True
    return False


def persist_result(s: Session, result) -> list[SignalRow]:
    """Store proposed band state and any NEW signals. Returns newly-created rows."""
    for bs in result.band_state.values():
        _save_band_state(s, bs)

    created: list[SignalRow] = []
    for sig in result.signals:
        expired = any("EXPIRED" in n for n in sig.notes)
        if not expired and _has_open_signal(s, sig.ticker, sig.rule, sig.band):
            continue  # dedupe — a PENDING signal for this trigger already exists
        row = SignalRow(
            ticker=sig.ticker, rule=sig.rule, action=sig.action, cause=sig.cause,
            size_shares=sig.size_shares, size_dollars=sig.size_dollars, band=sig.band,
            warnings=list(sig.warnings), notes=list(sig.notes),
            metrics=dict(sig.metrics),
            state="EXPIRED" if expired else "PENDING",
        )
        s.add(row)
        s.flush()
        if not expired:
            created.append(row)
    return created


def run_and_persist(s: Session, provider: MarketDataProvider, *, as_of: date,
                    is_dca_day: bool) -> list[SignalRow]:
    snap = build_snapshot(s, provider, as_of=as_of, is_dca_day=is_dca_day)
    result = engine_evaluate(snap)
    return persist_result(s, result)


# --- lifecycle (§2) ----------------------------------------------------------

def _tier_of(s: Session, ticker: str) -> str:
    u = s.get(UniverseRow, ticker)
    return u.tier if u else "T1"


def _latch_band(s: Session, ticker: str, ladder: str, band: float | None,
                status: BandStatus) -> None:
    if band is None:
        return
    row = s.get(EngineStateRow, ticker)
    if row is None:
        return
    field = row.dd_status if ladder == "dd" else row.trim_status
    field = dict(field or {})
    field[str(band)] = status.value
    if ladder == "dd":
        row.dd_status = field
    else:
        row.trim_status = field
    s.flush()


def confirm_signal(s: Session, signal_id: int, *, fill_qty: float, fill_price: float,
                   trade_date: date | None = None) -> SignalRow:
    sig = s.get(SignalRow, signal_id)
    if sig is None:
        raise KeyError(f"signal {signal_id} not found")
    if sig.state != "PENDING":
        raise ValueError(f"signal {signal_id} is {sig.state}, not PENDING")
    td = trade_date or date.today()

    if sig.action in ("BUY", "DEPLOY", "REPLACE"):
        ledger_svc.record_trade(s, ticker=sig.ticker, side="buy", qty=fill_qty,
                                price=fill_price, trade_date=td, source_signal_id=sig.id)
        if sig.rule == "double_down":
            spend = fill_qty * fill_price
            ledger_svc.add_reserve(s, kind="dd_draw", amount=-spend,
                                   note=f"double-down {sig.ticker}")
            params = cfg.build_params(s)
            base_i = params.base_for(_tier_of(s, sig.ticker))
            row = s.get(EngineStateRow, sig.ticker)
            if row is not None and base_i:
                row.dd_deployed_mult += spend / base_i
            _latch_band(s, sig.ticker, "dd", sig.band, BandStatus.FIRED)
        elif sig.rule == "base_dca":
            params = cfg.build_params(s)
            if params.auto_bank_5050:
                base_i = params.base_for(_tier_of(s, sig.ticker))
                ledger_svc.add_reserve(s, kind="base_bank", amount=base_i / 2,
                                       note=f"50/50 bank {sig.ticker}")
    elif sig.action == "SELL":
        ledger_svc.record_trade(s, ticker=sig.ticker, side="sell", qty=fill_qty,
                                price=fill_price, trade_date=td, source_signal_id=sig.id)
        ledger_svc.add_reserve(s, kind="trim_recycle", amount=fill_qty * fill_price,
                               note=f"trim proceeds {sig.ticker}")
        _latch_band(s, sig.ticker, "trim", sig.band, BandStatus.FIRED)

    sig.state = "DONE"
    sig.fill_qty = fill_qty
    sig.fill_price = fill_price
    from datetime import datetime
    sig.resolved_at = datetime.now()
    s.flush()
    return sig


def skip_signal(s: Session, signal_id: int) -> SignalRow:
    sig = s.get(SignalRow, signal_id)
    if sig is None:
        raise KeyError(f"signal {signal_id} not found")
    if sig.state != "PENDING":
        raise ValueError(f"signal {signal_id} is {sig.state}, not PENDING")
    params = cfg.build_params(s)
    sig.state = "SKIPPED"
    sig.snooze_until = date.today() + timedelta(days=params.snooze_days)
    # Return the band to ARMED (SNOOZED = quiet-then-rearm, ruleset §2).
    ladder = "dd" if sig.rule == "double_down" else "trim" if sig.rule == "trim" else None
    if ladder:
        _latch_band(s, sig.ticker, ladder, sig.band, BandStatus.ARMED)
    s.flush()
    return sig
