"""Rule 2 — trim: bands (+120/+170/+220), velocity gate, compounding, MIN_WEIGHT, re-arm."""

from __future__ import annotations

from tests.conftest import make_snapshot, md, pos
from tradr.engine import BandState, BandStatus, evaluate


def _trims(result):
    return [s for s in result.signals if s.rule == "trim"]


def test_first_trim_band_sells_20pct():
    # basis 100, price 230 -> gain +130% crosses +120% band. 20% of 100 shares = 20.
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)], [md("AAA", price=230)])
    sig = _trims(evaluate(snap))
    assert len(sig) == 1
    assert sig[0].size_shares == 20
    assert sig[0].band == 1.20
    assert any("recycle into reserve" in n for n in sig[0].notes)


def test_gap_up_fires_all_bands_compounding():
    # price 330 -> gain +230% crosses +120/+170/+220. 0.8^3 -> ~48.8 sold of 100.
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)], [md("AAA", price=330)])
    sig = _trims(evaluate(snap))
    assert sig[0].size_shares == 48  # int(48.8)
    assert sig[0].band == 2.20


def test_velocity_gate_suppresses_slow_compounder():
    # Crosses +120% band but only +10% over 60 days -> gradual compounder, no trim.
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)],
                         [md("AAA", price=230, ret_60d=0.10)])
    result = evaluate(snap)
    assert _trims(result) == []
    assert result.band_state["AAA"].trim_status[1.20] is BandStatus.ARMED  # stays armed
    assert any("velocity gate" in d for d in result.diagnostics)


def test_velocity_gate_lets_momentum_spike_fire():
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)],
                         [md("AAA", price=230, ret_60d=0.35)])
    assert len(_trims(evaluate(snap))) == 1


def test_min_weight_floor_caps_trim():
    # Small position A next to a big position B: trimming A would breach the 3% floor.
    a = pos("AAA", shares=10, avg_basis=100)          # value 2300 at price 230
    b = pos("BIG", shares=1000, avg_basis=50)         # value 100000
    snap = make_snapshot([a, b], [md("AAA", price=230), md("BIG", price=100)])
    sig = next(s for s in _trims(evaluate(snap)) if s.ticker == "AAA")
    assert sig.size_shares == 0                        # floored
    assert any("MIN_WEIGHT" in n for n in sig.notes)


def test_trim_band_rearms_below_buffer():
    bs = {"AAA": BandState("AAA", trim_status={1.20: BandStatus.FIRED})}
    # gain +90% < band(120) − buffer(20pp)=+100% -> re-arms. No new fire (90<120).
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)], [md("AAA", price=190)],
                         band_state=bs)
    result = evaluate(snap)
    assert result.band_state["AAA"].trim_status[1.20] is BandStatus.ARMED
    assert _trims(result) == []


def test_fired_band_does_not_refire_within_buffer():
    bs = {"AAA": BandState("AAA", trim_status={1.20: BandStatus.FIRED})}
    # gain +130% still above band−buffer(+100%) -> stays fired, no new trim.
    snap = make_snapshot([pos("AAA", shares=100, avg_basis=100)], [md("AAA", price=230)],
                         band_state=bs)
    result = evaluate(snap)
    assert result.band_state["AAA"].trim_status[1.20] is BandStatus.FIRED
    assert _trims(result) == []
