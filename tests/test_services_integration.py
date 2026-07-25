"""End-to-end (DB): config → snapshot → engine → persist → confirm updates ledger."""

from __future__ import annotations

from datetime import date

from tests.conftest import StubProvider
from tradr.db.models import UniverseRow
from tradr.engine import MarketData
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
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2), is_dca_day=False)

    dd = [r for r in new if r.rule == "double_down"]
    assert len(dd) == 1
    assert dd[0].size_dollars == 4000        # first band 2× base (base_i = 1000*2)
    assert dd[0].band == -0.20

    # Confirm the fill: buy 50 @ 80.
    sig_svc.confirm_signal(db, dd[0].id, fill_qty=50, fill_price=80, trade_date=date(2026, 2, 2))

    pos = {p.ticker: p for p in L.derive_positions(db)}
    assert pos["AAA"].shares == 150
    assert round(pos["AAA"].avg_basis, 2) == round((100 * 100 + 50 * 80) / 150, 2)  # 93.33
    assert L.reserve_balance(db) == 6000     # 10000 − 4000 draw

    from tradr.db.models import EngineStateRow, SignalRow
    assert db.get(SignalRow, dd[0].id).state == "DONE"
    assert db.get(EngineStateRow, "AAA").dd_status["-0.2"] == "FIRED"
    assert db.get(EngineStateRow, "AAA").dd_deployed_mult == 2.0


def test_dedupe_no_duplicate_pending(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    first = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2), is_dca_day=False)
    second = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 3), is_dca_day=False)
    assert len(first) == 1
    assert second == []   # already-PENDING signal not re-created


def test_skip_snoozes_and_rearms_band(db):
    _configure(db)
    db.add(UniverseRow(ticker="AAA", tier="T1", subscribed=True))
    L.record_trade(db, ticker="AAA", side="buy", qty=100, price=100, trade_date=date(2026, 1, 1))
    L.add_reserve(db, kind="external", amount=10000)
    provider = StubProvider({"AAA": MarketData("AAA", price=80, high_52w=100,
                                               ret_30d=0.0, ret_60d=0.0)})
    new = sig_svc.run_and_persist(db, provider, as_of=date(2026, 2, 2), is_dca_day=False)
    sig = sig_svc.skip_signal(db, new[0].id)
    assert sig.state == "SKIPPED" and sig.snooze_until is not None

    from tradr.db.models import EngineStateRow
    assert db.get(EngineStateRow, "AAA").dd_status["-0.2"] == "ARMED"  # returned to armed
