"""Watchlist Opportunity — dip-buy trigger for unheld, subscribed names.

Reuses Rule 1's band ladder + hysteresis, gated by opportunity_score (move size ÷
this ticker's own trailing volatility) so only unusually large drops for THAT name
open a new starter position. Sized as a flat tranche, not the averaging-down ladder.

Consolidated with the held-name dip-buy and rolling-cadence buy into one "buy" rule
(ruleset §4/§7) — a watchlist starter is distinguished by `metrics["trigger"] == "dip"`
and the ticker not being a held position, not by its own rule name.
"""

from __future__ import annotations

from tradr.engine import BandState, BandStatus, evaluate
from tests.conftest import make_snapshot, md, pos


def _watch(result):
    return [s for s in result.signals if s.rule == "buy" and s.action == "BUY"
            and s.metrics.get("trigger") == "dip" and s.metrics.get("funded_from") == "reserve"
            and "opportunity score" in s.cause]


def test_fires_starter_buy_above_score_threshold():
    # high 100, price 65 -> dd -35%, vol_20d 0.02 -> score 17.5, well above the 8.0 default.
    watchlist = [pos("AAA", tier="T1", shares=0.0, avg_basis=0.0)]
    snap = make_snapshot([], [md("AAA", price=65, high_52w=100, vol_20d=0.02)],
                         watchlist=watchlist)
    sig = _watch(evaluate(snap))
    assert len(sig) == 1
    assert sig[0].size_dollars == 2000  # 1x base_for(T1) = 1000*2 (demo_params tier_weight)
    assert sig[0].band == -0.30


def test_suppressed_below_score_threshold():
    # Same -20% drawdown, but high vol_20d makes the move routine for this name.
    watchlist = [pos("AAA", tier="T1", shares=0.0, avg_basis=0.0)]
    snap = make_snapshot([], [md("AAA", price=80, high_52w=100, vol_20d=0.10)],
                         watchlist=watchlist)
    assert _watch(evaluate(snap)) == []


def test_avoid_list_blocks_watch_opportunity():
    watchlist = [pos("AAA", tier="T1", shares=0.0, avg_basis=0.0)]
    snap = make_snapshot([], [md("AAA", price=65, high_52w=100, vol_20d=0.02)],
                         watchlist=watchlist, avoid=frozenset({"AAA"}))
    assert _watch(evaluate(snap)) == []


def test_expires_when_price_recovers_before_confirmation():
    watchlist = [pos("AAA", tier="T1", shares=0.0, avg_basis=0.0)]
    bs = {"AAA": BandState("AAA", dd_status={-0.20: BandStatus.PENDING})}
    # dd now -5% -> pending band recovered -> withdrawal, band re-armed.
    snap = make_snapshot([], [md("AAA", price=95, high_52w=100, vol_20d=0.02)],
                         watchlist=watchlist, band_state=bs)
    result = evaluate(snap)
    assert result.band_state["AAA"].dd_status[-0.20] is BandStatus.ARMED
    assert any("withdrawn" in s.cause for s in result.signals if s.rule == "buy")


def test_core_tier_etf_excluded_rule3_already_owns_it():
    # ETF/ETFC core tiers are steered by Rule 3 (core-build target); a watchlist starter
    # must not also fire for them, or the same ticker gets two competing buy alerts.
    watchlist = [pos("CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")]
    snap = make_snapshot([], [md("CSPX.L", price=90, high_52w=100, vol_20d=0.005)],
                         watchlist=watchlist)
    assert _watch(evaluate(snap)) == []


def test_shares_shared_reserve_with_held_dip_buy_deepest_first():
    # BBB (held, -35% -> wants 3x base = 6000) is deeper than AAA (watchlist, -25% ->
    # wants 1x base = 2000) -> BBB, being deeper, exhausts the reserve first.
    held = [pos("BBB", tier="T1", shares=100, avg_basis=50)]
    watchlist = [pos("AAA", tier="T1", shares=0.0, avg_basis=0.0)]
    snap = make_snapshot(held,
                         [md("AAA", price=75, high_52w=100, vol_20d=0.02),
                          md("BBB", price=65, high_52w=100, vol_20d=0.02)],
                         watchlist=watchlist, reserve=6000.0)
    result = evaluate(snap)
    by_ticker = {s.ticker: s for s in result.signals if s.action == "BUY"}
    assert by_ticker["BBB"].size_dollars == 6000
    assert not by_ticker["BBB"].warnings          # funded first (deeper dd)
    assert any("reserve short" in w for w in by_ticker["AAA"].warnings)
