"""Rule 3 (proactive) — core-build recommendation to invest into the index."""

from __future__ import annotations

import pytest

from tests.conftest import make_snapshot, md, pos
from tradr.engine import Position, evaluate


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


def _two_core_params(**kw):
    from tradr.engine import Params
    return Params(base_unit=100.0, tier_weight={"T1": 2, "ETF": 2, "ETFC": 1},
                  core_tiers=("ETF", "ETFC"), etf_tier_key="ETF", **kw)


def _two_core_snap(params, held_core=()):
    stock = pos("AAA", tier="T1", shares=10, avg_basis=100)   # value 1000
    tickers = {"CSPX.L": "ETF", "EIMI.L": "ETFC"}
    held = [pos(t, tier=tickers[t], shares=sh, avg_basis=b, asset_class="ETF")
            for t, sh, b in held_core]
    watch = [Position(ticker=t, tier=tier, shares=0.0, avg_basis=0.0, asset_class="ETF")
             for t, tier in tickers.items() if t not in {h.ticker for h in held}]
    return _snap_with_watchlist(
        [stock, *held],
        [md("AAA", price=100), md("CSPX.L", price=800), md("EIMI.L", price=50)],
        watch, params=params)


def test_core_build_splits_across_multiple_core_etfs_when_rotation_off():
    # Two core ETFs (CSPX weight 2, EIMI weight 1) → both nudged in a 2:1 ratio.
    snap = _two_core_snap(_two_core_params(core_rotate=False))
    sigs = {s.ticker: s for s in _core(evaluate(snap))}
    assert set(sigs) == {"CSPX.L", "EIMI.L"}
    # CSPX base = 100*2 = 200, EIMI base = 100*1 = 100 → 2:1 ratio
    assert round(sigs["CSPX.L"].metrics["budget"] / sigs["EIMI.L"].metrics["budget"], 2) == 2.0


def test_core_rotation_emits_one_order_carrying_the_whole_contribution():
    """Rule 8: three $180 LSE orders pay the USD 1.70 floor three times to move the same
    money. Rotation spends the whole round on one line instead."""
    p = _two_core_params()
    split = _core(evaluate(_two_core_snap(_two_core_params(core_rotate=False))))
    rotated = _core(evaluate(_two_core_snap(p)))
    assert len(rotated) == 1
    # Same dollars into the core, one order instead of two.
    assert rotated[0].metrics["budget"] == pytest.approx(sum(s.metrics["budget"] for s in split))
    # And strictly cheaper, because the fee floor is paid once.
    assert rotated[0].metrics["cost"]["total"] < sum(s.metrics["cost"]["total"] for s in split)
    assert any("one order instead of 2" in n for n in rotated[0].notes)


def test_core_rotation_picks_the_primary_tier_first_from_nothing():
    # All core lines at zero → tie on underweight, broken by the primary-tier ordering.
    rotated = _core(evaluate(_two_core_snap(_two_core_params())))
    assert rotated[0].ticker == "CSPX.L"


def test_core_rotation_moves_to_the_underweight_line_next():
    # CSPX.L is the entire core (100% of it) but its target share is only 2/3, so it is
    # overweight WITHIN the core and the round should go to EIMI.L. This is the rotation:
    # buying one line makes another the most underweight next run. Core is still 7% of the
    # portfolio overall, so the step does fire.
    rotated = _core(evaluate(_two_core_snap(_two_core_params(),
                                            held_core=[("CSPX.L", 0.1, 800.0)])))
    assert len(rotated) == 1
    assert rotated[0].ticker == "EIMI.L"


def test_core_rotation_is_a_noop_with_a_single_core_line():
    from tradr.engine import Params
    params = Params(base_unit=100.0, tier_weight={"T1": 2, "ETF": 2}, core_tiers=("ETF",))
    stock = pos("AAA", tier="T1", shares=10, avg_basis=100)
    watch = [Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")]
    snap = _snap_with_watchlist([stock], [md("AAA", price=100), md("CSPX.L", price=800)],
                               watch, params=params)
    sigs = _core(evaluate(snap))
    assert len(sigs) == 1
    assert not any("one order instead" in n for n in sigs[0].notes)


def test_core_contribution_pays_its_own_fee():
    """The contribution funds the fee, so the shares bought are the contribution minus it —
    the card must not claim $180 of shares for a $180 contribution."""
    sig = _core(evaluate(_two_core_snap(_two_core_params())))[0]
    fee = sig.metrics["cost"]["total"]
    assert fee > 0
    assert sig.size_dollars == pytest.approx(sig.metrics["budget"] - fee)
    assert sig.metrics["cash_needed"] == pytest.approx(sig.metrics["budget"])


def test_core_build_silent_when_core_on_target():
    # Core already ≥ 40% → no nudge.
    stock = pos("AAA", tier="T2", shares=1, avg_basis=100)          # value 100
    core = pos("CSPX.L", tier="ETF", shares=1, avg_basis=800, asset_class="ETF")  # value 800 → 89%
    snap = _snap_with_watchlist([stock, core], [md("AAA", price=100), md("CSPX.L", price=800)], [])
    assert _core(evaluate(snap)) == []


def test_core_build_folds_in_opportunistic_reason_instead_of_a_second_signal():
    # CSPX.L is both below the 40% core target AND deeply off its 52wk high with a
    # high opportunity score -> ONE core_build signal carrying both reasons, not a
    # separate watch_opportunity signal competing for the same ticker.
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = _snap_with_watchlist(
        [stock], [md("AAA", price=100), md("CSPX.L", price=700, high_52w=800, vol_20d=0.005)],
        [watch])
    result = evaluate(snap)
    sig = _core(result)
    assert len(sig) == 1
    assert sig[0].metrics["opportunistic"] is True
    assert "opportunity score" in sig[0].cause
    assert not any(s.rule == "watch_opportunity" for s in result.signals)


def test_core_build_no_bonus_reason_when_not_a_notable_dip():
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = _snap_with_watchlist([stock], [md("AAA", price=100), md("CSPX.L", price=800)], [watch])
    sig = _core(evaluate(snap))
    assert len(sig) == 1
    assert "opportunistic" not in sig[0].metrics


def test_core_build_not_converted_to_replace_at_holdings_cap():
    # Even at MAX_HOLDINGS, a core-build (ETF) must not become a "replace" prompt.
    stocks = [pos(f"S{i}", tier="T2", shares=1, avg_basis=100) for i in range(20)]
    market = [md(f"S{i}", price=100) for i in range(20)] + [md("CSPX.L", price=800)]
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = _snap_with_watchlist(stocks, market, [watch])
    sig = _core(evaluate(snap))
    assert len(sig) == 1 and sig[0].action == "BUY"
