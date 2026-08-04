"""End-to-end (DB): config → snapshot → engine → persist → confirm updates ledger."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from tests.conftest import StubProvider
from tradr.db.models import UniverseRow
from tradr.engine import MarketData, estimate_for_ticker
from tradr.services import config_service as cfg
from tradr.services import ledger as L
from tradr.services import signals as sig_svc


def _configure(db):
    cfg.seed_defaults(db)
    cfg.set_param(db, "base_unit", 1000.0)
    cfg.set_param(db, "tier_weight", {"T1": 2.0, "T2": 1.0, "ETF": 18.0})


def test_not_configured_blocks_build(db):
    cfg.seed_defaults(db)
    assert cfg.is_configured(db) is False


def test_double_down_signal_then_confirm_roundtrip(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)

    provider = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))

    dd = [r for r in new if r.rule == "buy"]
    assert len(dd) == 1
    assert dd[0].size_dollars == 4000        # first band 2× base (base_i = 1000*2)
    assert dd[0].band == -0.20

    # Confirm the fill: buy 50 @ 80.
    sig_svc.confirm_signal(db, dd[0].id, fill_qty=50, fill_price=80, trade_date=date(2026, 2, 2))

    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 150
    # 10000 − 4000 draw − the IBKR fees that draw incurs (Rule 8).
    fees = estimate_for_ticker("AAA", trade_value=50 * 80, shares=50, p=cfg.build_params(db)).total
    assert fees > 0
    # Basis includes the brokerage on the new fill — 93.34, not the fee-free 93.33.
    assert round(pos["AAA"].avg_basis, 6) == round((100 * 100 + 50 * 80 + fees) / 150, 6)
    assert round(pos["AAA"].avg_basis, 2) == 93.34
    assert L.reserve_balance(db) == 6000 - fees

    from tradr.db.models import EngineStateRow, SignalRow
    assert db.get(SignalRow, dd[0].id).state == "DONE"
    assert db.get(EngineStateRow, "AAA").dd_status["-0.2"] == "FIRED"
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == 2.0


def test_rerun_replaces_stale_pending(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider_a = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                                 ret_30d=0.0, ret_60d=0.0)})
    provider_b = StubProvider({"AAA": MarketData("AAA", price=65, high_52w=100,
                                                 ret_30d=0.0, ret_60d=0.0)})
    first = sig_svc.run_and_persist(db, provider_a, as_of=date(2026, 2, 2))
    second = sig_svc.run_and_persist(db, provider_b, as_of=date(2026, 2, 3))
    assert len(first) == 1
    assert len(second) == 1                        # re-derived fresh, not skipped as a dupe
    assert first[0].metrics["price"] == 80          # first run's alert carried day-1 price
    assert second[0].metrics["price"] == 65         # rerun refreshes it to day-2 price

    from tradr.db.models import SignalRow
    pending = db.query(SignalRow).filter_by(state="PENDING").all()
    assert len(pending) == 1                         # exactly one live alert for AAA, not two
    assert pending[0].metrics["price"] == 65          # the surviving alert is the fresh one


def test_trim_and_double_down_never_coexist_across_runs(db):
    # Day 1: big run-up crosses the trim band, user hasn't confirmed yet.
    # Day 2: price craters off its own high — double-down would independently
    # qualify — but trim is still an open, unconfirmed sell on the same name.
    # Both a stale trim card and a fresh dip-buy card for SNDK must never
    # coexist in the PENDING alert set at once.
    _configure(db)
    db.add(UniverseRow(ticker="SNDK", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="SNDK", side="buy", qty=100, price=500,
                   trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)

    day1 = StubProvider({"SNDK": MarketData("SNDK", price=1436.56, high_52w=1450,
                                            ret_30d=0.0, ret_60d=0.43)})
    first = sig_svc.run_and_persist(db, day1, as_of=date(2026, 2, 2))
    assert [r.rule for r in first] == ["trim"]

    day2 = StubProvider({"SNDK": MarketData("SNDK", price=1258.39, high_52w=2354.39,
                                            ret_30d=0.0, ret_60d=0.43)})
    second = sig_svc.run_and_persist(db, day2, as_of=date(2026, 2, 3))

    from tradr.db.models import SignalRow
    pending = db.query(SignalRow).filter_by(state="PENDING").all()
    rules = {r.rule for r in pending}
    assert rules == {"trim"}                      # double-down never surfaces alongside it
    assert len(pending) == 1
    assert pending[0].metrics["price"] == 1258.39  # and its price is this run's, not day 1's


def test_watch_opportunity_confirm_opens_position_and_draws_reserve(db):
    _configure(db)
    # AAA is subscribed but never bought — a watchlist name, not a holding.
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.add_reserve(db, kind="external", amount=10000)

    provider = StubProvider({"AAA": MarketData("AAA", price=65, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0, vol_20d=0.02)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    watch = [r for r in new if r.rule == "buy"]
    assert len(watch) == 1
    assert watch[0].size_dollars == 2000     # 1x base_for(T1) = 1000*2, flat starter

    sig_svc.confirm_signal(db, watch[0].id, fill_qty=30, fill_price=65, trade_date=date(2026, 2, 2))

    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 30            # position opened from 0
    # Drew from the shared reserve — the fill plus its fees (Rule 8).
    fees = estimate_for_ticker("AAA", trade_value=30 * 65, shares=30, p=cfg.build_params(db)).total
    assert L.reserve_balance(db) == 10000 - 30 * 65 - fees

    from tradr.db.models import EngineStateRow, SignalRow
    assert db.get(SignalRow, watch[0].id).state == "DONE"
    assert db.get(EngineStateRow, "AAA").dd_status["-0.3"] == "FIRED"
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == (30 * 65) / 2000


def test_skip_snoozes_and_rearms_band(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    sig = sig_svc.skip_signal(db, new[0].id)
    assert sig.state == "SKIPPED" and sig.snooze_until is not None

    from tradr.db.models import EngineStateRow
    assert db.get(EngineStateRow, "AAA").dd_status["-0.2"] == "ARMED"  # returned to armed


def test_confirm_records_fee_on_the_lot_and_as_its_own_reserve_line(db):
    """Rule 8: the reserve pays for shares AND fees on a dip-buy, as separate ledger
    lines, and the fill carries its cost so 'fees paid' is answerable."""
    from tradr.db.models import Lot, ReserveEntry

    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)

    provider = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    dd = next(r for r in new if r.rule == "buy")
    sig_svc.confirm_signal(db, dd.id, fill_qty=50, fill_price=80, trade_date=date(2026, 2, 2))

    fees = estimate_for_ticker("AAA", trade_value=50 * 80, shares=50, p=cfg.build_params(db)).total
    entries = {e.kind: e.amount for e in db.scalars(select(ReserveEntry)).all()}
    assert entries["dd_draw"] == -4000                 # shares only, no fees folded in
    assert entries["fee"] == -fees                     # fees are their own visible line
    assert L.reserve_balance(db) == 10000 - 4000 - fees

    lot = db.scalars(select(Lot).where(Lot.source_signal_id == dd.id)).one()
    assert lot.fee == fees
    assert L.fees_paid(db) == fees


def test_contribution_funded_buy_pays_its_fee_without_touching_the_reserve(db):
    """Core-build is funded by the monthly contribution, not dry powder — the fee comes
    out of the contribution (order already sized for it), so the reserve must not move."""
    from tradr.db.models import Lot, ReserveEntry

    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    db.add(UniverseRow(ticker="CSPX.L", tier="ETF", asset_class="ETF", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100, trade_date=date(2026, 1, 1))

    provider = StubProvider({
        "AAA": MarketData("AAA", price=100, high_52w=100, ret_30d=0.0, ret_60d=0.0),
        "CSPX.L": MarketData("CSPX.L", price=800, high_52w=800, ret_30d=0.0, ret_60d=0.0),
    })
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    core = next(r for r in new if r.rule == "core_build")
    budget = core.metrics["budget"]
    fee = core.metrics["cost"]["total"]
    # Shares bought are the contribution minus the fee, not the whole contribution.
    assert core.size_dollars < budget
    assert round(core.size_dollars + fee, 6) == round(budget, 6)

    before = L.reserve_balance(db)
    sig_svc.confirm_signal(db, core.id, fill_qty=core.size_dollars / 800, fill_price=800,
                           trade_date=date(2026, 2, 2))
    assert L.reserve_balance(db) == before
    assert not [e for e in db.scalars(select(ReserveEntry)).all() if e.kind == "fee"]
    lot = db.scalars(select(Lot).where(Lot.source_signal_id == core.id)).one()
    assert lot.fee > 0          # still logged, so it counts toward fees paid


def _confirmed_dip(db, *, price=80.0, qty=50.0, fill=80.0):
    """Run a dip-buy through to a confirmed fill; return (lot, signal)."""
    from tradr.db.models import Lot

    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=price, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    dd = next(r for r in new if r.rule == "buy")
    sig_svc.confirm_signal(db, dd.id, fill_qty=qty, fill_price=fill, trade_date=date(2026, 2, 2))
    lot = db.scalars(select(Lot).where(Lot.source_signal_id == dd.id)).one()
    return lot, dd


def test_void_restores_the_reserve_it_drew(db):
    from tradr.db.models import EngineStateRow

    lot, dd = _confirmed_dip(db)
    drawn = L.reserve_balance(db)
    assert drawn < 10000                      # the confirm drew shares + fees

    out = sig_svc.void_trade(db, lot.id)
    assert L.reserve_balance(db) == pytest.approx(10000)   # fully restored
    assert out["reserve_restored"] == pytest.approx(10000 - drawn)
    # Cap headroom given back and the band re-armed, so the name is dip-able again.
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == 0.0
    assert db.get(EngineStateRow, "AAA").dd_status[str(dd.band)] == "ARMED"


def test_void_reverses_with_compensating_entries_not_deletions(db):
    """The reserve ledger is append-only — the original draw must remain visible."""
    from tradr.db.models import ReserveEntry

    lot, _ = _confirmed_dip(db)
    before = {e.id for e in db.scalars(select(ReserveEntry)).all()}
    sig_svc.void_trade(db, lot.id)
    after = db.scalars(select(ReserveEntry)).all()
    assert before <= {e.id for e in after}     # nothing removed
    reversals = [e for e in after if e.kind == "void_reversal"]
    assert reversals and all(e.source_lot_id == lot.id for e in reversals)
    assert all(f"trade {lot.id} voided" in e.note for e in reversals)


def test_void_is_idempotent(db):
    lot, _ = _confirmed_dip(db)
    sig_svc.void_trade(db, lot.id)
    once = L.reserve_balance(db)
    sig_svc.void_trade(db, lot.id)             # double-click must not refund twice
    assert L.reserve_balance(db) == once


def test_void_drops_the_position_and_its_fee(db):
    lot, _ = _confirmed_dip(db)
    assert L.fees_paid(db) > 0
    sig_svc.void_trade(db, lot.id)
    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 100            # back to the pre-signal position
    assert L.fees_paid(db) == 0.0              # a trade that never happened had no fee


def test_void_of_a_manually_entered_trade_touches_no_reserve(db):
    # A hand-entered lot has no reserve movements behind it; voiding must be a no-op there.
    _configure(db)
    lot = L.record_trade(db, ticker="AAA", side="buy", qty=10, price=100,
                         trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=500)
    out = sig_svc.void_trade(db, lot.id)
    assert out["reserve_restored"] == 0.0
    assert L.reserve_balance(db) == 500


def test_void_of_a_missing_lot_raises(db):
    _configure(db)
    with pytest.raises(KeyError):
        sig_svc.void_trade(db, 9999)


def test_void_reverses_a_manual_reserve_funded_buy(db):
    """A hand-entered dip-buy (no signal behind it) still reverses cleanly: the reserve
    draw and fee undo, and since there is no linked signal the cap/band step is a no-op
    rather than touching state it was never responsible for."""
    _configure(db)
    L.add_reserve(db, kind="external", amount=1000)
    lot, delta = L.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=100,
                                       trade_date=date(2026, 1, 1), fee=1.0,
                                       fund_from_reserve=True)
    assert L.reserve_balance(db) == 1000 + delta

    out = sig_svc.void_trade(db, lot.id)
    assert out["reserve_restored"] == -delta
    assert L.reserve_balance(db) == 1000
    assert out["rearmed_band"] is None           # nothing to re-arm — no signal was involved


def test_manual_buy_fires_all_pending_bands_and_expires_the_stale_alert(db):
    """The user's ask: a manual trade that stands in for a live recommendation must not
    keep re-alerting for the same condition, and should re-arm only through the normal
    condition (price recovery), not be left stuck."""
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)

    # Price craters -55% off high, crossing all four T1 bands at once.
    provider = StubProvider({"AAA": MarketData("AAA", price=45, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    dd = next(r for r in new if r.rule == "buy")

    from tradr.db.models import EngineStateRow
    before = db.get(EngineStateRow, "AAA").dd_status
    assert set(before.values()) == {"PENDING"}

    out = sig_svc.record_manual_trade(db, ticker="AAA", side="buy", qty=50, price=45,
                                      trade_date=date(2026, 2, 2), fee=0.5,
                                      fund_from_reserve=True)
    assert sorted(out["bands_fired"]) == [-0.5, -0.4, -0.3, -0.2]   # ALL pending bands, not just deepest
    assert dd.id in out["signals_expired"]

    after = db.get(EngineStateRow, "AAA").dd_status
    assert set(after.values()) == {"FIRED"}
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == (50 * 45) / 2000  # base_i(T1)=2000

    from tradr.db.models import SignalRow
    row = db.get(SignalRow, dd.id)
    assert row.state == "EXPIRED"
    assert any("manually recorded trade" in n for n in row.notes)
    # No live duplicate left behind for the same condition.
    assert not [r for r in db.query(SignalRow).filter_by(state="PENDING").all()
               if r.ticker == "AAA"]


def test_manual_buy_without_fund_flag_does_not_touch_bands(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=45, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))

    from tradr.db.models import EngineStateRow
    before = dict(db.get(EngineStateRow, "AAA").dd_status)

    out = sig_svc.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=45,
                                      trade_date=date(2026, 2, 2), fund_from_reserve=False)
    assert out["bands_fired"] == []
    assert out["signals_expired"] == []
    assert dict(db.get(EngineStateRow, "AAA").dd_status) == before   # untouched

    from tradr.db.models import SignalRow
    assert len(new) == len([r for r in db.query(SignalRow).filter_by(state="PENDING").all()
                            if r.ticker == "AAA"])  # the alert is still there


def test_manual_trade_with_no_live_recommendation_leaves_bands_alone(db):
    # fund_from_reserve is checked but nothing on the ladder is PENDING -> no-op, no error.
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    out = sig_svc.record_manual_trade(db, ticker="AAA", side="buy", qty=1, price=100,
                                      trade_date=date(2026, 1, 5), fund_from_reserve=True)
    assert out == {"lot_id": out["lot_id"], "fee": 0.0, "reserve_delta": -100.0,
                   "bands_fired": [], "signals_expired": []}


def test_manual_sell_fires_pending_trim_band_only_not_dd_ladder(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=50, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=125, high_52w=250,
                                               ret_30d=0.0, ret_60d=0.5)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))
    trim = next(r for r in new if r.rule == "trim")

    out = sig_svc.record_manual_trade(db, ticker="AAA", side="sell", qty=20, price=125,
                                      trade_date=date(2026, 2, 2), fund_from_reserve=True)
    assert out["bands_fired"] == [1.2]
    assert out["signals_expired"] == [trim.id]

    from tradr.db.models import EngineStateRow
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == 0.0   # a sell never touches it


def test_manual_buy_fires_armed_not_just_pending_bands(db):
    """Regression: a band can be ARMED (not PENDING) at the moment you log the trade —
    e.g. right after a void re-armed it — while the price is still deep in dip territory.
    The fill must still settle it by price, or the very next engine run re-crosses the
    ARMED band back into PENDING and re-alerts for a dip already bought."""
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)

    # Cross all four bands once so a market snapshot with the deep high_52w exists...
    provider = StubProvider({"AAA": MarketData("AAA", price=45, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))

    # ...then reset the ladder back to ARMED by hand, simulating the void-reversal path —
    # no live PENDING alert exists, yet the price hasn't moved at all.
    from tradr.db.models import EngineStateRow
    row = db.get(EngineStateRow, "AAA")
    row.dd_status = {k: "ARMED" for k in row.dd_status}
    db.flush()

    out = sig_svc.record_manual_trade(db, ticker="AAA", side="buy", qty=50, price=45,
                                      trade_date=date(2026, 2, 3), fund_from_reserve=True)
    assert sorted(out["bands_fired"]) == [-0.5, -0.4, -0.3, -0.2]
    assert set(db.get(EngineStateRow, "AAA").dd_status.values()) == {"FIRED"}


def test_manual_buy_still_counts_toward_cap_when_every_band_was_already_fired(db):
    # Topping up a dip that's already fully consumed its ladder: no NEW band to fire,
    # but the cap counter must still move — it's real capital going into the name.
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", asset_class="STOCK", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=45, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2))

    from tradr.db.models import EngineStateRow
    row = db.get(EngineStateRow, "AAA")
    row.dd_status = {k: "FIRED" for k in row.dd_status}
    db.flush()

    out = sig_svc.record_manual_trade(db, ticker="AAA", side="buy", qty=10, price=45,
                                      trade_date=date(2026, 2, 3), fund_from_reserve=True)
    assert out["bands_fired"] == []           # nothing NEW crossed
    assert out["signals_expired"] == []
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == (10 * 45) / 2000  # still counted
