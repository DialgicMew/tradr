"""Ledger: derived positions + flat-average cost basis + reserve balance."""

from __future__ import annotations

from datetime import date

from tradr.db.models import UniverseRow
from tradr.services import ledger as L


def _d(day: int) -> date:
    return date(2026, 1, day)


def test_avg_basis_flat_average_on_buys(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=120, trade_date=_d(2))
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 20
    assert pos["AAA"].avg_basis == 110  # (1000 + 1200) / 20


def test_sell_reduces_shares_basis_unchanged(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=20, price=110, trade_date=_d(1))
    L.record_trade(db, ticker="AAA", side="sell", qty=5, price=200, trade_date=_d(2))
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 15
    assert pos["AAA"].avg_basis == 110  # flat average — sells don't change basis


def test_full_exit_drops_position(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    L.record_trade(db, ticker="AAA", side="sell", qty=10, price=150, trade_date=_d(2))
    assert L.derive_positions(db) == []


def test_voided_lot_excluded(db):
    lot = L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    L.void_lot(db, lot.id)
    assert L.derive_positions(db) == []


def test_tier_and_asset_class_from_universe(db):
    db.add(UniverseRow(ticker="SPY", tier="ETF", asset_class="ETF"))
    L.record_trade(db, ticker="SPY", side="buy", qty=10, price=400, trade_date=_d(1))
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["SPY"].tier == "ETF" and pos["SPY"].asset_class == "ETF"


def test_reserve_balance_sums_entries(db):
    L.add_reserve(db, kind="external", amount=5000)
    L.add_reserve(db, kind="dd_draw", amount=-2000)
    assert L.reserve_balance(db) == 3000
