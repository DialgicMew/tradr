"""Ledger: derived positions + flat-average cost basis + reserve balance."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

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


# --- transaction-cost log (Rule 8) -------------------------------------------

def test_fees_paid_sums_recorded_fills(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1), fee=0.41)
    L.record_trade(db, ticker="CSPX.L", side="buy", qty=1, price=800, trade_date=_d(2), fee=2.23)
    assert round(L.fees_paid(db), 2) == 2.64


def test_fees_paid_defaults_to_zero_for_manually_entered_trades(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    assert L.fees_paid(db) == 0.0


def test_fees_paid_can_be_windowed_by_date(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=100, trade_date=date(2025, 12, 31), fee=5.0)
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=100, trade_date=_d(2), fee=0.41)
    assert round(L.fees_paid(db, since=date(2026, 1, 1)), 2) == 0.41


def test_voided_trade_stops_counting_its_fee(db):
    lot = L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1), fee=0.41)
    L.void_lot(db, lot.id)
    assert L.fees_paid(db) == 0.0


def test_buy_fee_is_part_of_cost_basis(db):
    """1 share at $100 with $1 brokerage cost $101, so basis is $101 — not $100."""
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=100, trade_date=_d(1), fee=1.0)
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].avg_basis == 101.0


def test_untracked_old_fill_averages_against_a_fee_bearing_new_one(db):
    """The user's case: an old fee-less fill at $50 plus a new $100 + $1 fill averages
    (50 + 101) / 2 = 75.50. Nothing about the old trade is retro-inflated."""
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=50, trade_date=_d(1))  # fee 0
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=100, trade_date=_d(2), fee=1.0)
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 2
    assert pos["AAA"].avg_basis == 75.5


def test_sell_fee_does_not_touch_cost_basis(db):
    # A sell's brokerage reduces proceeds; it must not raise the basis of what remains.
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    L.record_trade(db, ticker="AAA", side="sell", qty=5, price=200, trade_date=_d(2), fee=5.0)
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].avg_basis == 100.0
    assert pos["AAA"].shares == 5


def test_voided_fee_bearing_buy_leaves_basis_untouched(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=1, price=50, trade_date=_d(1))
    bad = L.record_trade(db, ticker="AAA", side="buy", qty=1, price=100, trade_date=_d(2), fee=1.0)
    L.void_lot(db, bad.id)
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].avg_basis == 50.0


# --- manual reserve-funded trades ---------------------------------------------

def test_manual_buy_without_flag_leaves_reserve_untouched(db):
    L.add_reserve(db, kind="external", amount=1000)
    lot, delta = L.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=100,
                                       trade_date=_d(1))
    assert delta == 0.0
    assert L.reserve_balance(db) == 1000
    assert lot.fee == 0.0


def test_manual_buy_with_flag_draws_reserve_for_shares_and_fee(db):
    L.add_reserve(db, kind="external", amount=1000)
    lot, delta = L.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=100,
                                       trade_date=_d(1), fee=1.5, fund_from_reserve=True)
    assert delta == -101.5
    assert L.reserve_balance(db) == 1000 - 101.5

    from tradr.db.models import ReserveEntry
    kinds = {e.kind: e.amount for e in db.scalars(select(ReserveEntry)).all()
             if e.source_lot_id == lot.id}
    assert kinds == {"dd_draw": -100.0, "fee": -1.5}


def test_manual_sell_with_flag_recycles_proceeds_net_of_fee(db):
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=_d(1))
    lot, delta = L.record_manual_trade(db, ticker="AAA", side="sell", qty=5, price=200,
                                       trade_date=_d(2), fee=2.0, fund_from_reserve=True)
    assert delta == 998.0                      # +1000 gross, −2 fee
    assert L.reserve_balance(db) == 998.0


def test_manual_trade_reserve_draw_is_linked_to_the_lot_for_voiding(db):
    L.add_reserve(db, kind="external", amount=1000)
    lot, _ = L.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=100,
                                   trade_date=_d(1), fee=1.0, fund_from_reserve=True)
    entries = L.reserve_entries_for_lot(db, lot.id)
    assert {e.kind for e in entries} == {"dd_draw", "fee"}
