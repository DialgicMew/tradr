"""Rule 1 — per-stock double-down: bands, gap-through, 3× cap, reserve contention, re-arm."""

from __future__ import annotations

from tests.conftest import make_snapshot, md, pos
from tradr.engine import BandState, BandStatus, evaluate


def _dd_signals(result):
    return [s for s in result.signals if s.rule == "double_down" and s.action == "BUY"]


def test_first_band_fires_at_2x_base():
    # high 100, price 80 -> dd = -20% -> first stock band. base_i = 1000*2 = 2000.
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=80, high_52w=100)])
    sig = _dd_signals(evaluate(snap))
    assert len(sig) == 1
    assert sig[0].size_dollars == 2.0 * 2000  # first band ≈ 2× base
    assert sig[0].band == -0.20
    assert sig[0].size_shares == 50  # 4000 / 80


def test_gap_through_fires_all_crossed_and_caps_at_3x():
    # price 65 -> dd = -35% crosses -20 and -30; cumulative target hits 3× cap.
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=65, high_52w=100)])
    sig = _dd_signals(evaluate(snap))
    assert sig[0].size_dollars == 3.0 * 2000  # 3× cap
    assert sig[0].band == -0.30


def test_never_exceeds_3x_even_in_deep_crash():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=40, high_52w=100)])
    sig = _dd_signals(evaluate(snap))
    assert sig[0].size_dollars == 3.0 * 2000  # -60% dd, still clamped to 3×


def test_at_cap_produces_no_signal_not_a_zero_dollar_one():
    # Already fully deployed (3x) and a deeper band crosses -> nothing left to add.
    # Must not surface a "Buy $0 — Approve" card; just a silent diagnostic.
    bs = {"AAA": BandState("AAA",
                           dd_status={-0.20: BandStatus.FIRED, -0.30: BandStatus.FIRED,
                                      -0.40: BandStatus.FIRED},
                           dd_deployed_mult=3.0)}
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=48, high_52w=100)],
                         band_state=bs)
    result = evaluate(snap)
    assert _dd_signals(result) == []
    assert any("cap already" in d for d in result.diagnostics)


def test_already_deployed_reduces_new_suggestion():
    bs = {"AAA": BandState("AAA", dd_status={-0.20: BandStatus.FIRED}, dd_deployed_mult=2.0)}
    # price 65 -> deepest -30 target 3×, minus 2× already deployed -> only 1× new.
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=65, high_52w=100)],
                         band_state=bs)
    sig = _dd_signals(evaluate(snap))
    assert sig[0].size_dollars == 1.0 * 2000


def test_reserve_shortfall_flags_not_shrinks():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=80, high_52w=100)],
                         reserve=1000.0)
    sig = _dd_signals(evaluate(snap))
    assert sig[0].size_dollars == 4000  # not shrunk
    assert any("reserve short" in w for w in sig[0].warnings)


def test_shared_reserve_contention_deepest_first():
    # BBB deeper (-35%) gets first claim on 6000 reserve; AAA (-20%) then short.
    snap = make_snapshot(
        [pos("AAA", tier="T1"), pos("BBB", tier="T1")],
        [md("AAA", price=80, high_52w=100), md("BBB", price=65, high_52w=100)],
        reserve=6000.0,
    )
    by_ticker = {s.ticker: s for s in _dd_signals(evaluate(snap))}
    assert not by_ticker["BBB"].warnings                     # funded first
    assert any("reserve short" in w for w in by_ticker["AAA"].warnings)


def test_ladder_rearms_after_recovery_above_arm_line():
    bs = {"AAA": BandState("AAA", dd_status={-0.20: BandStatus.FIRED}, dd_deployed_mult=2.0)}
    # price 95 -> dd -5% >= arm_stock -10% -> ladder re-arms, deployed resets.
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=95, high_52w=100)],
                         band_state=bs)
    result = evaluate(snap)
    assert result.band_state["AAA"].dd_status[-0.20] is BandStatus.ARMED
    assert result.band_state["AAA"].dd_deployed_mult == 0.0
    assert _dd_signals(result) == []  # -5% doesn't cross any band


def test_avoid_list_never_buys():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=80, high_52w=100)],
                         avoid=frozenset({"AAA"}))
    assert _dd_signals(evaluate(snap)) == []


def test_etf_uses_shallower_ladder():
    # ETF band 1 is -10%. price 90 -> dd -10% fires for ETF asset class.
    snap = make_snapshot([pos("SPY", tier="ETF", asset_class="ETF")],
                         [md("SPY", price=90, high_52w=100)])
    sig = _dd_signals(evaluate(snap))
    assert len(sig) == 1 and sig[0].band == -0.10
