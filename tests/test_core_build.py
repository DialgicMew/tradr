"""Rule 3 (proactive) — core-build recommendation to invest into the index."""

from __future__ import annotations

from tradr.engine import MarketData, Position, evaluate
from tests.conftest import make_snapshot, md, pos


def _core(result):
    return [s for s in result.signals if s.rule == "core_build"]


def _snap_with_watchlist(positions, market, watchlist, **kw):
    snap = make_snapshot(positions, market, is_dca_day=False, **kw)
    object.__setattr__(snap, "watchlist", watchlist)
    return snap


def test_core_build_fires_from_zero_shares_off_dca_day():
    # Hold one stock, zero index. Core = 0% < 40% → should nudge to buy the index.
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = _snap_with_watchlist([stock], [md("AAA", price=100), md("CSPX.L", price=800)], [watch])
    sig = _core(evaluate(snap))
    assert len(sig) == 1
    assert sig[0].ticker == "CSPX.L"
    assert sig[0].action == "BUY"
    assert sig[0].size_dollars > 0


def test_core_build_splits_across_multiple_core_etfs():
    # Two core ETFs (CSPX weight 2, EIMI weight 1) → both nudged in a 2:1 ratio.
    from tradr.engine import Params
    params = Params(base_unit=100.0, tier_weight={"T1": 2, "ETF": 2, "ETFC": 1},
                    core_tiers=("ETF", "ETFC"), etf_tier_key="ETF")
    stock = pos("AAA", tier="T1", shares=10, avg_basis=100)   # value 1000
    watch = [
        Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF"),
        Position(ticker="EIMI.L", tier="ETFC", shares=0.0, avg_basis=0.0, asset_class="ETF"),
    ]
    snap = _snap_with_watchlist(
        [stock], [md("AAA", price=100), md("CSPX.L", price=800), md("EIMI.L", price=50)],
        watch, params=params)
    sigs = {s.ticker: s for s in _core(evaluate(snap))}
    assert set(sigs) == {"CSPX.L", "EIMI.L"}
    # CSPX base = 100*2 = 200, EIMI base = 100*1 = 100 → 2:1 ratio
    assert round(sigs["CSPX.L"].size_dollars / sigs["EIMI.L"].size_dollars, 2) == 2.0


def test_core_build_silent_when_core_on_target():
    # Core already ≥ 40% → no nudge.
    stock = pos("AAA", tier="T2", shares=1, avg_basis=100)          # value 100
    core = pos("CSPX.L", tier="ETF", shares=1, avg_basis=800, asset_class="ETF")  # value 800 → 89%
    snap = _snap_with_watchlist([stock, core], [md("AAA", price=100), md("CSPX.L", price=800)], [])
    assert _core(evaluate(snap)) == []


def test_core_build_not_converted_to_replace_at_holdings_cap():
    # Even at MAX_HOLDINGS, a core-build (ETF) must not become a "replace" prompt.
    stocks = [pos(f"S{i}", tier="T2", shares=1, avg_basis=100) for i in range(20)]
    market = [md(f"S{i}", price=100) for i in range(20)] + [md("CSPX.L", price=800)]
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = _snap_with_watchlist(stocks, market, [watch])
    sig = _core(evaluate(snap))
    assert len(sig) == 1 and sig[0].action == "BUY"
