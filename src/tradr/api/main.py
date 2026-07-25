"""FastAPI backend. Bound to 127.0.0.1 (see docker-compose). Single backend for the
React UI and the Telegram bot. Personal data is entered here — never shipped in code."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradr.api import schemas as sc
from tradr.db import SessionLocal, init_db, session_scope
from tradr.db.models import (
    FlagRow,
    Lot,
    MacroRow,
    MarketSnapshotRow,
    ReserveEntry,
    SignalRow,
    UniverseRow,
)
from tradr.runner import run_once
from tradr.services import config_service as cfg
from tradr.services import ledger as ledger_svc
from tradr.services import signals as sig_svc

app = FastAPI(title="tradr", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    with session_scope() as s:
        cfg.seed_defaults(s)


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# --- health / setup ----------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/setup/status")
def setup_status(s: Session = Depends(get_db)) -> dict:
    return {"configured": cfg.is_configured(s)}


@app.post("/api/setup")
def setup(body: sc.SetupIn, s: Session = Depends(get_db)) -> dict:
    cfg.set_param(s, "base_unit", body.base_unit)
    cfg.set_param(s, "tier_weight", body.tier_weight)
    row = s.get(FlagRow, "emergency_fund_ok")
    if row is None:
        s.add(FlagRow(key="emergency_fund_ok", value=body.emergency_fund_ok))
    else:
        row.value = body.emergency_fund_ok
    return {"configured": True}


# --- params / flags / macro --------------------------------------------------

@app.get("/api/params")
def list_params(s: Session = Depends(get_db)) -> list[sc.ParamOut]:
    return [sc.ParamOut(key=r.key, value=r.value, status=r.status) for r in cfg.get_all_params(s)]


@app.put("/api/params/{key}")
def update_param(key: str, body: sc.ParamIn, s: Session = Depends(get_db)) -> dict:
    existing = next((r for r in cfg.get_all_params(s) if r.key == key), None)
    if existing and existing.status == "LOCKED":
        raise HTTPException(400, f"{key} is LOCKED (ruleset non-negotiable)")
    cfg.set_param(s, key, body.value)
    return {"key": key, "value": body.value}


@app.get("/api/flags")
def list_flags(s: Session = Depends(get_db)) -> dict:
    return {r.key: r.value for r in s.scalars(select(FlagRow)).all()}


@app.put("/api/flags/{key}")
def update_flag(key: str, body: sc.FlagIn, s: Session = Depends(get_db)) -> dict:
    row = s.get(FlagRow, key)
    if row is None:
        s.add(FlagRow(key=key, value=body.value))
    else:
        row.value = body.value
    return {"key": key, "value": body.value}


@app.get("/api/macro")
def get_macro(s: Session = Depends(get_db)) -> dict:
    row = s.scalars(select(MacroRow).order_by(MacroRow.id.desc())).first()
    return {"sp_fwd_pe": row.sp_fwd_pe if row else None,
            "sp_10yr_avg_fwd_pe": row.sp_10yr_avg_fwd_pe if row else None}


@app.post("/api/macro")
def set_macro(body: sc.MacroIn, s: Session = Depends(get_db)) -> dict:
    s.add(MacroRow(sp_fwd_pe=body.sp_fwd_pe, sp_10yr_avg_fwd_pe=body.sp_10yr_avg_fwd_pe))
    return body.model_dump()


# --- universe (subscriptions / tiers / avoid-list) ---------------------------

@app.get("/api/universe")
def list_universe(s: Session = Depends(get_db)) -> list[dict]:
    return [
        {"ticker": u.ticker, "tier": u.tier, "asset_class": u.asset_class,
         "is_avoid": u.is_avoid, "subscribed": u.subscribed, "notify": u.notify}
        for u in s.scalars(select(UniverseRow).order_by(UniverseRow.ticker)).all()
    ]


@app.post("/api/universe")
def upsert_universe(body: sc.UniverseIn, s: Session = Depends(get_db)) -> dict:
    t = body.ticker.upper()
    row = s.get(UniverseRow, t) or UniverseRow(ticker=t)
    row.tier, row.asset_class = body.tier, body.asset_class
    row.is_avoid, row.subscribed, row.notify = body.is_avoid, body.subscribed, body.notify
    s.merge(row)
    return body.model_dump() | {"ticker": t}


@app.delete("/api/universe/{ticker}")
def delete_universe(ticker: str, s: Session = Depends(get_db)) -> dict:
    row = s.get(UniverseRow, ticker.upper())
    if row:
        s.delete(row)
    return {"deleted": ticker.upper()}


# --- trades / reserve --------------------------------------------------------

@app.get("/api/trades")
def list_trades(s: Session = Depends(get_db)) -> list[dict]:
    lots = s.scalars(select(Lot).order_by(Lot.trade_date.desc(), Lot.id.desc())).all()
    return [
        {"id": l.id, "ticker": l.ticker, "side": l.side, "qty": l.qty, "price": l.price,
         "trade_date": l.trade_date.isoformat(), "voided": l.voided,
         "source_signal_id": l.source_signal_id}
        for l in lots
    ]


@app.post("/api/trades")
def add_trade(body: sc.TradeIn, s: Session = Depends(get_db)) -> dict:
    lot = ledger_svc.record_trade(
        s, ticker=body.ticker, side=body.side, qty=body.qty, price=body.price,
        trade_date=body.trade_date or date.today())
    return {"id": lot.id}


@app.post("/api/trades/{lot_id}/void")
def void_trade(lot_id: int, s: Session = Depends(get_db)) -> dict:
    try:
        ledger_svc.void_lot(s, lot_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"voided": lot_id}


@app.get("/api/reserve")
def get_reserve(s: Session = Depends(get_db)) -> dict:
    entries = s.scalars(select(ReserveEntry).order_by(ReserveEntry.id.desc())).all()
    return {
        "balance": ledger_svc.reserve_balance(s),
        "entries": [{"id": e.id, "kind": e.kind, "amount": e.amount, "note": e.note,
                     "ts": e.ts.isoformat() if e.ts else None} for e in entries],
    }


@app.post("/api/reserve")
def add_reserve(body: sc.ReserveIn, s: Session = Depends(get_db)) -> dict:
    e = ledger_svc.add_reserve(s, kind=body.kind, amount=body.amount, note=body.note)
    return {"id": e.id, "balance": ledger_svc.reserve_balance(s)}


# --- holdings / portfolio ----------------------------------------------------

def _latest_prices(s: Session) -> dict[str, float]:
    prices: dict[str, float] = {}
    for row in s.scalars(select(MarketSnapshotRow).order_by(MarketSnapshotRow.id)).all():
        prices[row.ticker] = row.price  # later rows overwrite → latest wins
    return prices


@app.get("/api/holdings")
def holdings(s: Session = Depends(get_db)) -> list[sc.HoldingOut]:
    prices = _latest_prices(s)
    positions = ledger_svc.derive_positions(s)
    total = sum((prices.get(p.ticker, 0.0) * p.shares) for p in positions) or 0.0
    out = []
    for p in positions:
        price = prices.get(p.ticker)
        value = price * p.shares if price else None
        gain = (price / p.avg_basis - 1) if price and p.avg_basis else None
        weight = (value / total) if (value and total) else None
        out.append(sc.HoldingOut(
            ticker=p.ticker, tier=p.tier, asset_class=p.asset_class, shares=p.shares,
            avg_basis=p.avg_basis, price=price, value=value, gain_pct=gain, weight=weight))
    return out


@app.get("/api/portfolio/summary")
def portfolio_summary(s: Session = Depends(get_db)) -> dict:
    prices = _latest_prices(s)
    positions = ledger_svc.derive_positions(s)
    invested = sum((prices.get(p.ticker, 0.0) * p.shares) for p in positions)
    cost = sum(p.avg_basis * p.shares for p in positions)
    reserve = ledger_svc.reserve_balance(s)
    core = cfg.core_tiers(s)
    etf_val = sum(prices.get(p.ticker, 0.0) * p.shares for p in positions if p.tier in core)
    return {
        "invested_equity": invested,
        "total_cost": cost,
        "unrealized_gain": invested - cost,
        "unrealized_gain_pct": (invested / cost - 1) if cost else None,
        "reserve_balance": reserve,
        "core_weight": (etf_val / invested) if invested else None,
        "holdings_count": sum(1 for p in positions if p.asset_class != "ETF"),
        "allocation": [
            {"ticker": p.ticker, "value": prices.get(p.ticker, 0.0) * p.shares, "tier": p.tier}
            for p in positions
        ],
    }


@app.get("/api/history/{ticker}")
def price_history(ticker: str, s: Session = Depends(get_db)) -> list[dict]:
    rows = s.scalars(
        select(MarketSnapshotRow).where(MarketSnapshotRow.ticker == ticker.upper())
        .order_by(MarketSnapshotRow.as_of)
    ).all()
    return [{"as_of": r.as_of.isoformat(), "price": r.price, "high_52w": r.high_52w} for r in rows]


# --- signals -----------------------------------------------------------------

@app.get("/api/signals")
def list_signals(state: str | None = None, s: Session = Depends(get_db)) -> list[dict]:
    q = select(SignalRow).order_by(SignalRow.created_at.desc())
    if state:
        q = q.where(SignalRow.state == state)
    return [
        {"id": r.id, "ticker": r.ticker, "rule": r.rule, "action": r.action, "cause": r.cause,
         "size_shares": r.size_shares, "size_dollars": r.size_dollars, "band": r.band,
         "warnings": r.warnings, "notes": r.notes, "metrics": r.metrics or {}, "state": r.state,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in s.scalars(q).all()
    ]


@app.post("/api/signals/{signal_id}/confirm")
def confirm(signal_id: int, body: sc.ConfirmIn, s: Session = Depends(get_db)) -> dict:
    try:
        sig = sig_svc.confirm_signal(s, signal_id, fill_qty=body.fill_qty,
                                     fill_price=body.fill_price, trade_date=body.trade_date)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"id": sig.id, "state": sig.state}


@app.post("/api/signals/{signal_id}/skip")
def skip(signal_id: int, s: Session = Depends(get_db)) -> dict:
    try:
        sig = sig_svc.skip_signal(s, signal_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"id": sig.id, "state": sig.state, "snooze_until": sig.snooze_until}


@app.post("/api/run")
def run_now(force: bool = False, s: Session = Depends(get_db)) -> dict:
    return run_once(force=force, notify=True)
