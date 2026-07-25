"""Precedence (§6) + Rule 4/7 overlays + Rules 5/6 + gates + expiry."""

from __future__ import annotations

from tests.conftest import make_snapshot, md, pos
from tradr.engine import BandState, BandStatus, Flags, Macro, evaluate


def test_trim_beats_double_down_same_day():
    # basis 50, price 125 -> gain +150% (trim, velocity ok) AND dd 125/250-1 = -50% (DD).
    snap = make_snapshot([pos("AAA", tier="T1", shares=100, avg_basis=50)],
                         [md("AAA", price=125, high_52w=250, ret_60d=0.5)])
    result = evaluate(snap)
    assert any(s.rule == "trim" for s in result.signals)
    assert not any(s.rule == "double_down" and s.action == "BUY" for s in result.signals)
    # DD band left untouched (not burned) since it was suppressed, not fired.
    assert result.band_state["AAA"].dd_status[-0.20] is BandStatus.ARMED


def test_open_trim_suppresses_double_down_across_runs():
    # A trim from a PRIOR run is still PENDING; a dip-buy must NOT fire on the same
    # name (else the card shows buy + sell at once). basis 50, price 125 -> both eligible.
    bs = {"AAA": BandState("AAA", trim_status={1.20: BandStatus.PENDING})}
    snap = make_snapshot([pos("AAA", tier="T1", shares=100, avg_basis=50)],
                         [md("AAA", price=125, high_52w=250, ret_60d=0.5)], band_state=bs)
    result = evaluate(snap)
    assert not any(s.rule == "double_down" and s.action == "BUY" for s in result.signals)


def test_dca_momentum_brake_halves_deploy():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=100, ret_30d=0.25)],
                         is_dca_day=True)
    dca = next(s for s in evaluate(snap).signals if s.rule == "base_dca")
    # base_i 2000, auto-bank -> deploy_base 1000, ×0.5 brake -> 500.
    assert dca.size_dollars == 500
    assert any("momentum brake" in n for n in dca.notes)


def test_froth_and_brake_do_not_compound():
    macro = Macro(sp_fwd_pe=25.0, sp_10yr_avg_fwd_pe=20.0)  # 25 > 20*1.15 -> frothy
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=100, ret_30d=0.25)],
                         is_dca_day=True, macro=macro)
    dca = next(s for s in evaluate(snap).signals if s.rule == "base_dca")
    assert dca.size_dollars == 500  # floor at 0.5, NOT 0.25


def test_dca_normal_market_is_5050():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=100, fwd_pe=15)],
                         is_dca_day=True)
    dca = next(s for s in evaluate(snap).signals if s.rule == "base_dca")
    assert dca.size_dollars == 1000               # base_i/2, full speed
    assert any("bank $1,000" in n for n in dca.notes)
    assert any("strong" in n for n in dca.notes)  # PE annotation


def test_emergency_fund_gate_warns_on_buys():
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=80, high_52w=100)],
                         flags=Flags(emergency_fund_ok=False))
    buys = [s for s in evaluate(snap).signals if s.action == "BUY"]
    assert buys and all(any("emergency fund" in w for w in s.warnings) for s in buys)


def test_cash_floor_deploys_excess_into_core():
    # invested 10000; idle cash 2000 > 5% ceiling (500) -> deploy 1500 into ETF core.
    positions = [pos("SPY", tier="ETF", asset_class="ETF", shares=100, avg_basis=100)]
    snap = make_snapshot(positions, [md("SPY", price=100)], reserve=0.0,
                         flags=Flags(emergency_fund_ok=True, idle_cash=2000.0))
    cf = [s for s in evaluate(snap).signals if s.rule == "cash_floor"]
    assert len(cf) == 1
    assert cf[0].size_dollars == 1500  # 2000 - 0.05*10000
    assert cf[0].ticker == "SPY"


def test_expire_withdraws_pending_when_recovered():
    bs = {"AAA": BandState("AAA", dd_status={-0.20: BandStatus.PENDING})}
    # dd now -5% (price 95) -> pending band recovered -> withdrawal + re-arm.
    snap = make_snapshot([pos("AAA", tier="T1")], [md("AAA", price=95, high_52w=100)],
                         band_state=bs)
    result = evaluate(snap)
    assert result.band_state["AAA"].dd_status[-0.20] is BandStatus.ARMED
    assert any("withdrawn" in s.cause for s in result.signals)
