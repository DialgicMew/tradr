"""Rule 8 — IBKR transaction costs: the schedule itself, and its overlay on signals."""

from __future__ import annotations

import dataclasses

import pytest

from tests.conftest import make_snapshot, md, pos
from tradr.engine import (
    Position,
    UnconfiguredSchedule,
    break_even_value,
    demo_params,
    estimate_for_ticker,
    estimate_order_cost,
    evaluate,
    venue_for,
)

# --- the fee schedule --------------------------------------------------------


def test_venue_inferred_from_ticker_suffix():
    assert venue_for("CSPX.L") == "LSE"
    assert venue_for("vwra.l") == "LSE"
    assert venue_for("NVDA") == "US"


def test_lse_etf_400_dollar_order_matches_published_schedule(p):
    # $400 LSE ETF: 0.05% = $0.20 so the USD 1.70 floor binds, +18% GST, +fixed venue fees.
    c = estimate_order_cost(venue="LSE", trade_value=400.0, shares=0.5, p=p)
    assert round(c.commission, 2) == 1.70
    assert round(c.gst, 2) == 0.31
    assert round(c.total, 2) == 2.23
    assert round(c.ratio, 4) == 0.0056  # 0.56% of the contribution


def test_us_stock_600_dollar_order_matches_published_schedule(p):
    # $600 / 100 shares: 0.0035×100 = $0.35 ties the floor; clearing 0.0002×100 = $0.02.
    c = estimate_order_cost(venue="US", trade_value=600.0, shares=100.0, p=p)
    assert round(c.commission, 2) == 0.35
    assert round(c.clearing_fee, 2) == 0.02
    assert round(c.total, 2) == 0.43
    assert c.ratio < 0.001  # ~0.07% — an order of magnitude cheaper than the LSE leg


def test_lse_commission_floor_dilutes_only_at_large_orders(p):
    # The floor stops binding at 1.70 / 0.0005 = $3,400.
    assert estimate_order_cost(venue="LSE", trade_value=3_000.0, shares=4, p=p).commission == 1.70
    big = estimate_order_cost(venue="LSE", trade_value=10_000.0, shares=12, p=p)
    assert round(big.commission, 2) == 5.00  # 0.05% now exceeds the floor


def test_us_commission_capped_at_tiered_maximum(p):
    # 5000 shares of a penny-ish line: per-share would be $17.50, capped at 1% of $200.
    c = estimate_order_cost(venue="US", trade_value=200.0, shares=5_000.0, p=p)
    assert round(c.commission, 2) == 2.00


def test_splitting_is_cheap_on_us_side_and_dear_on_lse_side(p):
    """The whole point of the model: three US orders cost cents extra, three LSE
    orders cost dollars extra."""
    one_us = estimate_order_cost(venue="US", trade_value=600.0, shares=100.0, p=p).total
    three_us = 3 * estimate_order_cost(venue="US", trade_value=200.0, shares=33.3, p=p).total
    one_lse = estimate_order_cost(venue="LSE", trade_value=400.0, shares=0.5, p=p).total
    three_lse = 3 * estimate_order_cost(venue="LSE", trade_value=133.0, shares=0.17, p=p).total
    assert three_us - one_us < 1.0
    assert three_lse - one_lse > 4.0


def test_zero_value_order_costs_nothing_and_has_no_ratio(p):
    c = estimate_order_cost(venue="LSE", trade_value=0.0, shares=0.0, p=p)
    assert c.ratio == 0.0


def test_break_even_value_reports_the_consolidation_target(p):
    # LSE fixed cost is $2.23; at a 0.5% target that needs a ~$446 order.
    floor = break_even_value("LSE", p, target_ratio=0.005)
    assert 440 < floor < 450
    at_floor = estimate_order_cost(venue="LSE", trade_value=floor, shares=1, p=p)
    assert at_floor.ratio <= 0.005 + 1e-9
    # The US floor is a fifth of the LSE one, so US break-even is a fifth of the size.
    assert 80 < break_even_value("US", p, target_ratio=0.005) < 85
    # A target the percentage components alone can't reach has no such size.
    assert break_even_value("LSE", p, target_ratio=0.0001) == 0.0
    assert break_even_value("LSE", p, target_ratio=0.0) == 0.0


# --- the overlay on signals --------------------------------------------------


def _buy(result, ticker):
    return next(s for s in result.signals if s.ticker == ticker and s.action == "BUY")


def _dip_snapshot(**params_kw):
    """One US name 25% off its high — fires a double-down worth ~2x BASE_i."""
    p = dataclasses.replace(demo_params(), **params_kw) if params_kw else demo_params()
    holding = pos("AAA", tier="T1", shares=100, avg_basis=100)
    return make_snapshot([holding], [md("AAA", price=75, high_52w=100)], params=p)


def test_buy_signal_carries_itemised_cost_and_cash_needed():
    sig = _buy(evaluate(_dip_snapshot()), "AAA")
    cost = sig.metrics["cost"]
    assert cost["venue"] == "US"
    assert cost["total"] > 0
    # Cash out of the door is the order plus its fees, never just the order.
    assert sig.metrics["cash_needed"] == pytest.approx(sig.size_dollars + cost["total"])
    assert any("est. IBKR cost" in n for n in sig.notes)


def test_sell_signal_reports_proceeds_net_of_fees():
    holding = pos("AAA", tier="T1", shares=100, avg_basis=100)
    snap = make_snapshot([holding], [md("AAA", price=250, high_52w=250, ret_60d=0.6)])
    sig = next(s for s in evaluate(snap).signals if s.action == "SELL")
    assert sig.metrics["net_proceeds"] < sig.size_dollars
    assert sig.metrics["net_proceeds"] == sig.size_dollars - sig.metrics["cost"]["total"]


def test_small_lse_order_gets_a_fee_warning_naming_the_consolidation_size():
    # base_unit 20 x ETF weight 4 = an $80 core-build order — the leaky habit.
    p = dataclasses.replace(demo_params(), base_unit=20.0,
                            tier_weight={"T1": 2.0, "T2": 1.0, "ETF": 4.0})
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = make_snapshot([stock], [md("AAA", price=100), md("CSPX.L", price=800)],
                         params=p, watchlist=[watch])
    sig = _buy(evaluate(snap), "CSPX.L")
    assert sig.metrics["cost_ratio"] > 0.02  # ~2.8% of an $80 order
    warn = next(w for w in sig.warnings if "fees are" in w)
    assert "would get under" in warn


def test_large_us_order_stays_under_the_warning_line():
    sig = _buy(evaluate(_dip_snapshot()), "AAA")
    assert sig.metrics["cost_ratio"] < 0.005
    assert not any("fees are" in w for w in sig.warnings)


def test_reserve_contention_draws_fees_as_well_as_the_order():
    # Reserve holds exactly the order value, so the LSE fees alone push it short.
    p = demo_params()
    holding = pos("CSPX.L", tier="ETF", shares=100, avg_basis=1000, asset_class="ETF")
    dollars = p.dd_first_band_mult * p.base_for("ETF")  # -15% crosses the ETF -10% band only
    snap = make_snapshot([holding], [md("CSPX.L", price=850, high_52w=1000)],
                         params=p, reserve=dollars)
    sig = _buy(evaluate(snap), "CSPX.L")
    assert sig.size_dollars == dollars
    assert any("reserve short" in w for w in sig.warnings)


def test_sub_dollar_fee_shortfall_is_not_reported_as_short():
    # A 43-cent fee gap on a $4,000 order isn't a real shortfall — don't nag about it.
    p = demo_params()
    holding = pos("AAA", tier="T1", shares=100, avg_basis=100)
    snap = make_snapshot([holding], [md("AAA", price=75, high_52w=100)],
                         params=p, reserve=p.dd_first_band_mult * p.base_for("T1"))
    assert not any("reserve short" in w for w in _buy(evaluate(snap), "AAA").warnings)


def test_costs_can_be_switched_off_entirely():
    sig = _buy(evaluate(_dip_snapshot(commissions_on=False)), "AAA")
    assert "cost" not in sig.metrics
    assert not any("est. IBKR cost" in n for n in sig.notes)


def test_block_ratio_suppresses_a_buy_too_small_to_be_worth_an_order():
    p = dataclasses.replace(demo_params(), base_unit=20.0, cost_block_ratio=0.01,
                            tier_weight={"T1": 2.0, "T2": 1.0, "ETF": 4.0})
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = make_snapshot([stock], [md("AAA", price=100), md("CSPX.L", price=800)],
                         params=p, watchlist=[watch])
    result = evaluate(snap)
    assert not [s for s in result.signals if s.ticker == "CSPX.L"]
    assert any("wait and consolidate" in d for d in result.diagnostics)


def test_block_ratio_off_by_default_leaves_the_small_order_visible():
    p = dataclasses.replace(demo_params(), base_unit=20.0,
                            tier_weight={"T1": 2.0, "T2": 1.0, "ETF": 4.0})
    assert p.cost_block_ratio == 0.0
    stock = pos("AAA", tier="T2", shares=10, avg_basis=100)
    watch = Position(ticker="CSPX.L", tier="ETF", shares=0.0, avg_basis=0.0, asset_class="ETF")
    snap = make_snapshot([stock], [md("AAA", price=100), md("CSPX.L", price=800)],
                         params=p, watchlist=[watch])
    assert _buy(evaluate(snap), "CSPX.L").size_dollars > 0


def test_expired_withdrawal_signals_are_not_priced():
    from tradr.engine import BandState, BandStatus

    bs = BandState(ticker="AAA", dd_status={-0.20: BandStatus.PENDING})
    holding = pos("AAA", tier="T1", shares=100, avg_basis=100)
    snap = make_snapshot([holding], [md("AAA", price=95, high_52w=100)],
                         band_state={"AAA": bs})
    withdrawn = next(s for s in evaluate(snap).signals
                     if any("EXPIRED" in n for n in s.notes))
    assert "cost" not in withdrawn.metrics


def test_estimate_for_ticker_routes_by_suffix(p):
    lse = estimate_for_ticker("CSPX.L", trade_value=400.0, shares=0.5, p=p)
    us = estimate_for_ticker("NVDA", trade_value=400.0, shares=2.0, p=p)
    assert lse.total > us.total * 4


# --- per-order minimum (Rule 8) ----------------------------------------------

def _floor_params(**kw):
    """base_unit 50: T1 base 100 (dip band1 = 200), T2 base 25 (band1 = 50, 3x cap 75)."""
    return dataclasses.replace(demo_params(), base_unit=50.0, min_order_dollars=100.0,
                               tier_weight={"T1": 2.0, "T2": 0.5, "ETF": 18.0}, **kw)


def _dip(ticker, tier, *, price=75.0, high=100.0, params=None):
    p = params or _floor_params()
    holding = pos(ticker, tier=tier, shares=100, avg_basis=100)
    return evaluate(make_snapshot([holding], [md(ticker, price=price, high_52w=high)], params=p))


def test_order_at_or_above_the_floor_is_untouched():
    sig = _buy(_dip("AAA", "T1"), "AAA")       # 2 x base 100 = $200
    assert sig.size_dollars == 200
    assert not sig.metrics["below_min_order"]


def test_order_within_roundup_tolerance_is_lifted_to_the_floor():
    # T2 at the -30% band: 3x cap x base 25 = $75. 75 x 1.25 = 93.75 < 100 -> deferred.
    # Widen the tolerance so the same order rounds up instead, proving the knob works.
    p = _floor_params(min_order_roundup_max=1.5)
    sig = _buy(_dip("AAA", "T2", price=65.0, params=p), "AAA")
    assert sig.size_dollars == 100.0
    assert not sig.metrics["below_min_order"]   # rounded, so not flagged


def test_roundup_never_inflates_beyond_the_tolerance():
    # $75 order, default 1.25x tolerance -> 93.75 short of $100, so it must NOT fire.
    result = _dip("AAA", "T2", price=65.0)
    assert not [s for s in result.signals if s.rule == "double_down"]
    assert any("per-order minimum" in d and "deeper band" in d for d in result.diagnostics)


def test_deferred_dip_buy_returns_once_a_deeper_band_makes_it_big_enough():
    """The floor defers rather than deletes: T2's band-1 order is too small, but the
    deeper band raises the ladder target until it clears."""
    p = _floor_params(min_order_roundup_max=1.5)
    shallow = _dip("AAA", "T2", price=78.0, params=p)          # -22%, band 1 -> 2x25 = $50
    assert not [s for s in shallow.signals if s.rule == "double_down"]
    deep = _dip("AAA", "T2", price=65.0, params=p)             # -35%, cap -> $75 -> $100
    assert _buy(deep, "AAA").size_dollars == 100.0


def test_floor_off_by_default_changes_nothing():
    p = dataclasses.replace(demo_params(), base_unit=50.0,
                            tier_weight={"T1": 2.0, "T2": 0.5, "ETF": 18.0})
    assert p.min_order_dollars == 0.0
    assert _buy(_dip("AAA", "T2", price=65.0, params=p), "AAA").size_dollars == 75.0


def test_watch_opportunity_below_the_floor_is_shown_and_flagged_not_hidden():
    """The user wants unusual dips on small names visible even when the rule's own
    starter size is uneconomic — so it is surfaced with a warning, never dropped."""
    p = _floor_params()                      # T2 starter = 1 x 25 = $25, far under $100
    watch = Position(ticker="AAA", tier="T2", shares=0.0, avg_basis=0.0)
    snap = make_snapshot([pos("BBB", tier="T1", shares=10, avg_basis=100)],
                         [md("BBB", price=100), md("AAA", price=65, high_52w=100, vol_20d=0.02)],
                         params=p, watchlist=[watch])
    sig = _buy(evaluate(snap), "AAA")
    assert sig.rule == "watch_opportunity"
    assert sig.size_dollars == 25.0           # NOT inflated to the floor
    assert sig.metrics["below_min_order"] is True
    assert any("under your $100 per-order minimum" in w for w in sig.warnings)
    assert any("size up to $100" in w for w in sig.warnings)


def test_base_dca_below_the_floor_is_shown_and_flagged_not_hidden():
    # A skipped monthly contribution never comes back, so it must not be deferred.
    p = _floor_params()                       # T2 deploy = base 25 / 2 = $12.50
    snap = make_snapshot([pos("AAA", tier="T2", shares=10, avg_basis=100)],
                         [md("AAA", price=100)], params=p, is_dca_day=True)
    dca = next(s for s in evaluate(snap).signals if s.rule == "base_dca")
    assert dca.metrics["below_min_order"] is True
    assert any("per-order minimum" in w for w in dca.warnings)


def test_floor_never_blocks_a_sell():
    # A tiny trim must still be actionable — a fee floor must not trap you in a position.
    p = _floor_params()
    holding = pos("AAA", tier="T2", shares=1, avg_basis=10)
    snap = make_snapshot([holding], [md("AAA", price=30, high_52w=30, ret_60d=0.6)], params=p)
    sell = next(s for s in evaluate(snap).signals if s.action == "SELL")
    assert sell.size_dollars < p.min_order_dollars
    assert not any("per-order minimum" in w for w in sell.warnings)


# --- Fixed vs Tiered plan (Rule 8) -------------------------------------------

def test_fixed_plan_us_bundles_exchange_and_clearing_into_commission(p):
    # Fixed: $0.005/share, min $1.00, capped at 1% of trade value; nothing added on top.
    c = estimate_order_cost(venue="US", trade_value=600.0, shares=100.0, p=p, plan="fixed")
    assert round(c.commission, 2) == 1.00      # 0.005 x 100 = 0.50, floored at 1.00
    assert c.exchange_fee == 0.0 and c.clearing_fee == 0.0
    assert round(c.total, 2) == 1.18           # + 18% GST


def test_fixed_plan_is_dearer_than_tiered_for_a_small_us_order(p):
    kw = {"venue": "US", "trade_value": 600.0, "shares": 100.0, "p": p}
    assert (estimate_order_cost(**kw, plan="fixed").total
            > estimate_order_cost(**kw, plan="tiered").total)


def test_fixed_plan_us_respects_the_one_percent_cap(p):
    c = estimate_order_cost(venue="US", trade_value=50.0, shares=500.0, p=p, plan="fixed")
    assert round(c.commission, 2) == 0.50      # 1% of $50 beats both 0.005/share and the $1 floor


def test_fixed_plan_lse_refuses_to_guess_an_unverified_minimum(p):
    # fixed_lse_min ships at 0 = not configured. Failing loudly beats inventing a number.
    assert p.fixed_lse_min == 0.0
    with pytest.raises(UnconfiguredSchedule, match="fixed_lse_min"):
        estimate_order_cost(venue="LSE", trade_value=400.0, shares=0.5, p=p, plan="fixed")


def test_fixed_plan_lse_works_once_the_minimum_is_supplied(p):
    cfgd = dataclasses.replace(p, fixed_lse_min=6.0)
    c = estimate_order_cost(venue="LSE", trade_value=400.0, shares=0.5, p=cfgd, plan="fixed")
    assert round(c.commission, 2) == 6.00
    assert round(c.total, 2) == 7.08


def test_plan_defaults_to_the_account_setting(p):
    assert p.commission_plan == "tiered"
    default = estimate_for_ticker("AAA", trade_value=600.0, shares=100.0, p=p)
    tiered = estimate_for_ticker("AAA", trade_value=600.0, shares=100.0, p=p, plan="tiered")
    assert default.total == tiered.total
    on_fixed = dataclasses.replace(p, commission_plan="fixed")
    assert estimate_for_ticker("AAA", trade_value=600.0, shares=100.0, p=on_fixed).total \
        == estimate_for_ticker("AAA", trade_value=600.0, shares=100.0, p=p, plan="fixed").total
