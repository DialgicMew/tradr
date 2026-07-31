"""Engine Core — the deterministic daily pipeline (ruleset.md §4, precedence §6).

Pure function: `evaluate(snapshot) -> EngineResult`. No I/O, no clock. It PROPOSES
signals and the resulting band/reserve state; the Signal Service commits state on user
confirmation (ruleset §2). Pipeline step order is significant and mirrors §4.
"""

from __future__ import annotations

import copy

from tradr.engine.costs import (
    break_even_value,
    estimate_for_ticker,
    gross_for_budget,
    venue_for,
)
from tradr.engine.models import (
    BandState,
    BandStatus,
    EngineResult,
    Macro,
    Signal,
    Snapshot,
)
from tradr.engine.params import Params

# Tolerance for band-boundary comparisons — float makes 80/100-1 == -0.19999999996,
# which must still count as crossing the -0.20 band.
EPS = 1e-9


def _round_shares(x: float) -> float:
    return float(int(x))  # whole-share suggestions; keep dollars exact


def _opportunity_score(magnitude: float, vol_20d: float) -> float:
    """Move size relative to this ticker's own trailing volatility — how unusual the
    move is for THIS stock, not just how big in absolute terms (used for alert ranking)."""
    magnitude = abs(magnitude)
    return magnitude / vol_20d if vol_20d > EPS else magnitude


def _pe_zone(fwd_pe: float | None) -> str:
    if fwd_pe is None:
        return "PE unknown"
    if fwd_pe < 17:
        return f"PE {fwd_pe:.1f} — strong (<17)"
    if fwd_pe <= 24:
        return f"PE {fwd_pe:.1f} — fair (17–24)"
    return f"PE {fwd_pe:.1f} — risky (>24)"


def _is_frothy(macro: Macro, p: Params) -> bool:
    if macro.sp_fwd_pe is None or macro.sp_10yr_avg_fwd_pe is None:
        return False
    return macro.sp_fwd_pe > macro.sp_10yr_avg_fwd_pe * (1 + p.froth_threshold)


def _init_bands(bs: BandState, dd_bands: tuple[float, ...], trim_bands: tuple[float, ...]) -> None:
    for b in dd_bands:
        bs.dd_status.setdefault(b, BandStatus.ARMED)
    for b in trim_bands:
        bs.trim_status.setdefault(b, BandStatus.ARMED)


def _dd_cumulative_target(p: Params, deepest_k: int) -> float:
    """Cumulative double-down target (in units of BASE_i) after the k-th band fires.

    First band ≈ dd_first_band_mult (2×); deeper bands top up toward the 3× cap.
    (ruleset Rule 1 sizing.)
    """
    if deepest_k <= 0:
        return 0.0
    step = p.dd_cap_mult - p.dd_first_band_mult
    return min(p.dd_cap_mult, p.dd_first_band_mult + (deepest_k - 1) * step)


# Rules whose order, if skipped, is simply GONE — a monthly contribution does not come
# back, and the user asked to keep seeing unusual dips on small names even when the rule's
# own size is uneconomic. Everything else re-derives on a later run (a latched band, a
# widening core gap, cash still sitting idle), so it is safe to hide until it is big enough.
_SHOW_BELOW_MIN = ("watch_opportunity", "base_dca")


def _min_order(p: Params, rule: str, dollars: float) -> tuple[float, bool] | None:
    """Apply the per-order floor (Rule 8). Returns (dollars, below_min) or None to defer.

    Sized orders below the floor pay IBKR's fee minimum on too little money. An order
    close to the floor is rounded UP to it — one real tranche beats three dribbles — but
    never by more than `min_order_roundup_max`, so the floor cannot quietly turn a $29
    starter into a $100 position and call it the same recommendation.

    Called at each sizing site rather than as a pass over finished signals, so reserve
    contention and the 3x cap both see the number that will actually be traded.
    """
    if p.min_order_dollars <= 0 or dollars <= 0 or dollars >= p.min_order_dollars - EPS:
        return dollars, False
    if dollars * p.min_order_roundup_max >= p.min_order_dollars - EPS:
        return p.min_order_dollars, False
    if rule in _SHOW_BELOW_MIN:
        return dollars, True
    return None


def _cash_needed(p: Params, ticker: str, dollars: float, price: float) -> float:
    """Gross order value plus the commission it will incur — what the reserve actually
    gives up. Falls back to the bare order value when the cost model is off."""
    if not p.commissions_on or dollars <= 0 or price <= 0:
        return max(0.0, dollars)
    return dollars + estimate_for_ticker(ticker, trade_value=dollars,
                                         shares=dollars / price, p=p).total


def _apply_cost_overlay(result: EngineResult, p: Params) -> None:
    """Price every tradeable signal (Rule 8) and annotate it in place.

    Adds `cost` / `cost_ratio` plus `cash_needed` (buys) or `net_proceeds` (sells) to
    metrics, warns when the drag clears `cost_warn_ratio`, and — only if the user has
    turned on `cost_block_ratio` — drops buys that are too small to be worth an order.
    """
    if not p.commissions_on:
        return
    kept: list[Signal] = []
    for s in result.signals:
        price = s.metrics.get("price")
        if s.action not in ("BUY", "SELL", "DEPLOY") or not s.size_dollars or not price:
            kept.append(s)  # withdrawals / REPLACE nudges have no order to price
            continue
        c = estimate_for_ticker(s.ticker, trade_value=s.size_dollars,
                                shares=s.size_dollars / price, p=p)
        s.metrics["cost"] = c.as_metrics()
        s.metrics["cost_ratio"] = c.ratio
        if s.action == "SELL":
            s.metrics["net_proceeds"] = s.size_dollars - c.total
        else:
            s.metrics["cash_needed"] = s.size_dollars + c.total

        if s.action == "BUY" and p.cost_block_ratio > 0 and c.ratio > p.cost_block_ratio + EPS:
            result.diagnostics.append(
                f"{s.ticker}: {s.rule} suppressed — est. cost ${c.total:.2f} is "
                f"{c.ratio:.2%} of a ${s.size_dollars:,.0f} order, over the "
                f"{p.cost_block_ratio:.2%} limit; wait and consolidate")
            continue

        s.notes.append(f"est. IBKR cost ${c.total:.2f} ({c.ratio:.2%} of the order)")
        if s.metrics.get("below_min_order"):
            # Deliberately kept visible rather than deferred, so say what it would take.
            s.warnings.append(
                f"under your ${p.min_order_dollars:,.0f} per-order minimum — fees are "
                f"{c.ratio:.2%} at this size; size up to ${p.min_order_dollars:,.0f} if "
                f"you take it")
        elif c.ratio > p.cost_warn_ratio + EPS:
            warn = (f"fees are {c.ratio:.2%} of this ${s.size_dollars:,.0f} order "
                    f"(${c.total:.2f})")
            floor = break_even_value(venue_for(s.ticker), p, target_ratio=p.cost_warn_ratio)
            if floor > s.size_dollars:
                warn += f" — one order of ~${floor:,.0f}+ would get under {p.cost_warn_ratio:.2%}"
            s.warnings.append(warn)
        kept.append(s)
    result.signals = kept


def evaluate(snap: Snapshot) -> EngineResult:
    p: Params = snap.params  # type: ignore[assignment]
    result = EngineResult(reserve_balance=snap.reserve_balance)

    # Work on a deep copy of band state so the engine stays side-effect free.
    band_state: dict[str, BandState] = {t: copy.deepcopy(b) for t, b in snap.band_state.items()}
    for pos in list(snap.positions) + list(snap.watchlist):
        band_state.setdefault(pos.ticker, BandState(ticker=pos.ticker))
        _init_bands(band_state[pos.ticker], p.bands_for(pos.asset_class), p.trim_bands)

    # Portfolio / invested equity (weights, floors, ceilings). Excludes reserve & cash.
    def price_of(ticker: str) -> float | None:
        md = snap.market.get(ticker)
        return md.price if md else None

    invested_equity = sum(
        pos.shares * price_of(pos.ticker)  # type: ignore[operator]
        for pos in snap.positions
        if price_of(pos.ticker) is not None
    )
    result.diagnostics.append(f"invested_equity={invested_equity:.2f}")

    # ---- Step 2: EXPIRE stale PENDING whose condition cleared ------------------
    for pos in snap.positions:
        md = snap.market.get(pos.ticker)
        if md is None:
            continue
        bs = band_state[pos.ticker]
        dd = md.price / md.high_52w - 1 if md.high_52w else 0.0
        gain = md.price / pos.avg_basis - 1 if pos.avg_basis else 0.0
        for band, st in list(bs.dd_status.items()):
            if st is BandStatus.PENDING and dd > band + EPS:  # recovered above the band
                bs.dd_status[band] = BandStatus.ARMED
                result.signals.append(Signal(pos.ticker, "double_down", "BUY",
                    cause=f"withdrawn — {pos.ticker} recovered above {band:+.0%} drawdown band",
                    band=band, notes=["EXPIRED: condition cleared before confirmation"]))
        for band, st in list(bs.trim_status.items()):
            if st is BandStatus.PENDING and gain < band - EPS:
                bs.trim_status[band] = BandStatus.ARMED
                result.signals.append(Signal(pos.ticker, "trim", "SELL",
                    cause=f"withdrawn — {pos.ticker} fell back below {band:+.0%} gain band",
                    band=band, notes=["EXPIRED: condition cleared before confirmation"]))

    # Watchlist tickers only carry a dd ladder (no basis to trim against).
    for pos in snap.watchlist:
        md = snap.market.get(pos.ticker)
        if md is None or not md.high_52w:
            continue
        bs = band_state[pos.ticker]
        dd = md.price / md.high_52w - 1
        for band, st in list(bs.dd_status.items()):
            if st is BandStatus.PENDING and dd > band + EPS:  # recovered above the band
                bs.dd_status[band] = BandStatus.ARMED
                result.signals.append(Signal(pos.ticker, "watch_opportunity", "BUY",
                    cause=f"withdrawn — {pos.ticker} recovered above {band:+.0%} drawdown band",
                    band=band, notes=["EXPIRED: condition cleared before confirmation"]))

    # ---- Step 4: RESET / RE-ARM -----------------------------------------------
    for pos in snap.positions:
        md = snap.market.get(pos.ticker)
        if md is None:
            continue
        bs = band_state[pos.ticker]
        dd = md.price / md.high_52w - 1 if md.high_52w else 0.0
        gain = md.price / pos.avg_basis - 1 if pos.avg_basis else 0.0
        arm_line = p.arm_for(pos.asset_class)
        # DD ladder re-arms as a whole once price recovers above the ARM line.
        if dd >= arm_line - EPS and any(s is BandStatus.FIRED for s in bs.dd_status.values()):
            for band in bs.dd_status:
                if bs.dd_status[band] is BandStatus.FIRED:
                    bs.dd_status[band] = BandStatus.ARMED
            bs.dd_deployed_mult = 0.0  # new crash cycle
        # Trim bands re-arm individually below band − buffer.
        for band, st in bs.trim_status.items():
            if st is BandStatus.FIRED and gain < band - p.trim_arm_buffer:
                bs.trim_status[band] = BandStatus.ARMED

    trimmed_today: set[str] = set()

    # ---- Step 5: TRIM (Rule 2) — beats double-down ----------------------------
    for pos in snap.positions:
        md = snap.market.get(pos.ticker)
        if md is None or not pos.avg_basis:
            continue
        bs = band_state[pos.ticker]
        gain = md.price / pos.avg_basis - 1
        newly_armed = sorted(b for b, st in bs.trim_status.items()
                             if st is BandStatus.ARMED and gain >= b - EPS)
        if newly_armed:
            # Velocity gate (Rule 2): only trim genuine momentum spikes, not slow
            # compounders. Band stays ARMED (may fire later if velocity spikes).
            if md.ret_60d < p.trim_velocity_pct - EPS:
                result.diagnostics.append(
                    f"{pos.ticker}: trim suppressed — 60d gain {md.ret_60d:+.0%} "
                    f"< velocity gate {p.trim_velocity_pct:+.0%}")
            else:
                for band in newly_armed:
                    bs.trim_status[band] = BandStatus.PENDING
                trimmed_today.add(pos.ticker)

        # Re-derive a fresh signal for every outstanding (PENDING) band each run —
        # whether it just fired above or was already awaiting confirmation from an
        # earlier run — so the alert shown always carries this run's price, not a
        # relic from whichever run first fired it.
        pending = sorted(b for b, st in bs.trim_status.items() if st is BandStatus.PENDING)
        if not pending:
            continue
        # Gap-up fires all pending bands; trim_pct of *running* shares each (compounds).
        remaining = pos.shares
        sold = 0.0
        for band in pending:
            sell = remaining * p.trim_pct
            remaining -= sell
            sold += sell
        # MIN_WEIGHT floor: never trim below min_weight of the portfolio.
        floor_shares = (p.min_weight * invested_equity) / md.price if invested_equity else 0.0
        max_sellable = max(0.0, pos.shares - floor_shares)
        capped = min(sold, max_sellable)
        notes = [f"gain {gain:+.0%}; bands {[f'{b:+.0%}' for b in pending]}",
                 "proceeds recycle into reserve on DONE"]
        if capped < sold:
            notes.append(f"capped by MIN_WEIGHT floor ({p.min_weight:.0%})")
        result.signals.append(Signal(pos.ticker, "trim", "SELL",
            cause=f"{pos.ticker} up {gain:+.0%} vs basis — trim {p.trim_pct:.0%}/band",
            size_shares=_round_shares(capped), size_dollars=capped * md.price,
            band=pending[-1], notes=notes,
            metrics={"price": md.price, "gain": gain, "ret_60d": md.ret_60d,
                     "trim_pct": p.trim_pct, "floored": capped < sold - EPS,
                     "bands": pending, "avg_basis": pos.avg_basis,
                     "vol_20d": md.vol_20d, "opportunity_score": _opportunity_score(gain, md.vol_20d)}))

    # ---- Step 6: DOUBLE-DOWN (Rule 1, held) + WATCHLIST OPPORTUNITY (unheld) --------
    # Both share one reserve pool and contend deepest-drawdown-first (§6).
    dd_candidates = []
    for pos in snap.positions:
        # Trim beats double-down (§6): suppress the dip-buy if the name trimmed this run
        # OR has an unconfirmed trim still outstanding (else you'd see buy+sell at once).
        bs_pos = band_state.get(pos.ticker)
        has_open_trim = bs_pos is not None and any(
            st is BandStatus.PENDING for st in bs_pos.trim_status.values())
        if pos.ticker in trimmed_today or has_open_trim or pos.ticker in snap.avoid:
            continue
        md = snap.market.get(pos.ticker)
        if md is None or not md.high_52w:
            continue
        bs = band_state[pos.ticker]
        dd = md.price / md.high_52w - 1
        bands = p.bands_for(pos.asset_class)
        newly_crossed = [b for b in bands if bs.dd_status[b] is BandStatus.ARMED and dd <= b + EPS]
        for b in newly_crossed:
            bs.dd_status[b] = BandStatus.PENDING

        # Re-derive against every outstanding (PENDING) band each run — not just ones
        # that just crossed — so a dip-buy awaiting confirmation from an earlier run
        # gets refreshed to this run's price instead of sitting on a stale one.
        pending = [b for b in bands if bs.dd_status[b] is BandStatus.PENDING]
        if not pending:
            continue
        deepest = min(pending)
        deepest_k = bands.index(deepest) + 1
        target = _dd_cumulative_target(p, deepest_k)
        new_mult = max(0.0, target - bs.dd_deployed_mult)  # clamp to 3× cap
        dollars = new_mult * p.base_for(pos.tier)
        floored = _min_order(p, "double_down", dollars)
        if floored is None:
            result.diagnostics.append(
                f"{pos.ticker}: dip-buy of ${dollars:,.0f} is under the "
                f"${p.min_order_dollars:,.0f} per-order minimum — waiting for a deeper band")
            continue
        dollars, below_min = floored
        dd_candidates.append({
            "rule": "double_down", "dd": dd, "pos": pos, "md": md, "pending": pending,
            "dollars": dollars, "below_min": below_min,
            "cause": f"{pos.ticker} {dd:+.0%} off 52wk high — bands {[f'{b:+.0%}' for b in pending]}",
            "notes": ["PE-exempt (dip buy)", "funded from reserve"],
        })

    # Watchlist Opportunity: subscribed but unheld names. Reuses the same band ladder
    # and PENDING/ARMED/FIRED hysteresis as Rule 1, but gated by opportunity_score so it
    # only fires on drops that are unusual for THIS ticker's own volatility — a routine
    # -20% wiggle for a choppy name shouldn't open a new position, a genuinely rare drop
    # should. Sized as a flat starter tranche, not the averaging-down ladder (there's no
    # existing basis to average into).
    for pos in snap.watchlist:
        # Core-tier ETFs (ETF/ETFC) are already steered by Rule 3's core-build target —
        # don't also flag them here, or the same ticker gets two competing buy alerts.
        if pos.ticker in snap.avoid or p.is_core(pos.tier):
            continue
        md = snap.market.get(pos.ticker)
        if md is None or not md.high_52w:
            continue
        bs = band_state[pos.ticker]
        dd = md.price / md.high_52w - 1
        score = _opportunity_score(dd, md.vol_20d)
        bands = p.bands_for(pos.asset_class)
        newly_crossed = [b for b in bands if bs.dd_status[b] is BandStatus.ARMED and dd <= b + EPS]
        for b in newly_crossed:
            bs.dd_status[b] = BandStatus.PENDING
        pending = [b for b in bands if bs.dd_status[b] is BandStatus.PENDING]
        if not pending or score < p.watch_opportunity_min_score:
            continue
        dollars = p.watch_starter_mult * p.base_for(pos.tier)
        # Starters below the floor are still surfaced (see _SHOW_BELOW_MIN) — a rare dip on
        # a small name is worth knowing about even when the rule's own size is uneconomic.
        dollars, below_min = _min_order(p, "watch_opportunity", dollars)  # type: ignore[misc]
        dd_candidates.append({
            "rule": "watch_opportunity", "dd": dd, "pos": pos, "md": md, "pending": pending,
            "dollars": dollars, "below_min": below_min,
            "cause": (f"{pos.ticker} {dd:+.0%} off 52wk high, opportunity score {score:.1f} "
                      f"(≥{p.watch_opportunity_min_score:g} threshold) — new starter position"),
            "notes": ["starter tranche for a new position — not averaging down", "funded from reserve"],
        })

    # Shared-reserve contention: deepest dd first; ties T1<T2 then alphabetical.
    tier_rank = {"T1": 0, "T2": 1, "ETF": 2}
    dd_candidates.sort(key=lambda c: (c["dd"], tier_rank.get(c["pos"].tier, 9), c["pos"].ticker))
    available = snap.reserve_balance
    for c in dd_candidates:
        pos, md, pending, dollars, dd = c["pos"], c["md"], c["pending"], c["dollars"], c["dd"]
        if dollars <= 0:
            # Already at the cap — nothing to actually approve. Band stays latched
            # PENDING (won't re-check until confirmed/skipped/re-armed); just note it
            # rather than showing a "Buy $0 — Approve" card with nothing to confirm.
            result.diagnostics.append(
                f"{pos.ticker}: {c['rule']} at {p.dd_cap_mult:g}× cap already — no new buy suggested")
            continue
        warnings = []
        # Reserve is drawn for the order value PLUS its commission (Rule 8) — otherwise
        # a run of dip-buys quietly overdraws the pool by the fees.
        need = _cash_needed(p, pos.ticker, dollars, md.price)
        short = need - available
        # Sub-dollar gaps are not a real shortfall — the reserve is a notional pool the
        # user tops up in round numbers, and saying "short by $0" is just noise.
        if short > 0.5:
            warnings.append(f"reserve short by ${short:,.0f} — top up or fund from cash")
        else:
            available = max(0.0, available - need)
        result.signals.append(Signal(pos.ticker, c["rule"], "BUY",
            cause=c["cause"],
            size_shares=_round_shares(dollars / md.price),
            size_dollars=dollars, band=min(pending), warnings=warnings,
            notes=c["notes"],
            metrics={"price": md.price, "dd": dd, "high_52w": md.high_52w,
                     "bands": pending, "asset_class": pos.asset_class,
                     "reserve_short": any("reserve short" in w for w in warnings),
                     "below_min_order": c["below_min"],
                     "vol_20d": md.vol_20d, "opportunity_score": _opportunity_score(dd, md.vol_20d)}))

    # ---- Step 7: BASE DCA (Rule 7) — scheduled monthly date, individual names --
    # The ETF core is handled by the always-on core-build step below (Rule 3), so DCA
    # here only covers individual names.
    if snap.is_dca_day:
        frothy = _is_frothy(snap.macro, p)
        for pos in snap.positions:
            if p.is_core(pos.tier):
                continue  # core built by the dedicated step
            md = snap.market.get(pos.ticker)
            if md is None:
                continue
            base_i = p.base_for(pos.tier)
            deploy_base = base_i / 2 if p.auto_bank_5050 else base_i
            bank = base_i / 2 if p.auto_bank_5050 else 0.0
            # Momentum brake & froth: half-speed, NON-compounding (floor 0.5).
            brake = md.ret_30d > p.momentum_brake_ret
            mult = 0.5 if (brake or frothy) else 1.0
            # A skipped monthly contribution is gone, so an under-floor DCA is shown and
            # flagged rather than deferred (see _SHOW_BELOW_MIN).
            budget, dca_below_min = _min_order(p, "base_dca", deploy_base * mult)  # type: ignore[misc]
            # The contribution funds the fee as well as the shares (Rule 8).
            deploy = gross_for_budget(pos.ticker, budget=budget, price=md.price, p=p)
            if deploy <= 0:
                result.diagnostics.append(
                    f"{pos.ticker}: base DCA of ${budget:,.2f} is smaller than its fees — skipped")
                continue
            notes = [f"bank ${bank:,.0f} to reserve" if bank else "no auto-bank", _pe_zone(md.fwd_pe)]
            if brake:
                notes.append("momentum brake ×0.5 (30d > +20%)")
            if frothy:
                notes.append("froth overlay ×0.5 (S&P fwd P/E rich)")
            result.signals.append(Signal(pos.ticker, "base_dca", "BUY",
                cause=f"scheduled DCA — {pos.tier} base ${base_i:,.0f}",
                size_shares=_round_shares(deploy / md.price), size_dollars=deploy,
                notes=notes,
                metrics={"price": md.price, "deploy": deploy, "bank": bank, "budget": budget,
                         "tier": pos.tier, "brake": brake, "froth": frothy, "steered": False,
                         "below_min_order": dca_below_min}))

    # ---- Step 7b: CORE BUILD (Rule 3, proactive) — keep the index core ≥ 40% ---
    # Runs every evaluation, not just the DCA day. Targets a subscribed core ETF even
    # at zero shares, so the core can be bootstrapped from nothing.
    core_candidates = [c for c in (list(snap.positions) + list(snap.watchlist))
                       if p.is_core(c.tier) and price_of(c.ticker) is not None]
    core_value = sum(pos.shares * price_of(pos.ticker) for pos in snap.positions  # type: ignore[operator]
                     if p.is_core(pos.tier) and price_of(pos.ticker) is not None)
    core_w = (core_value / invested_equity) if invested_equity else 0.0
    if core_candidates and core_w < p.core_low - EPS:
        # The round's total contribution, scaled down so it never overshoots the 40% target.
        total_base = sum(p.base_for(c.tier) for c in core_candidates)
        gap = ((p.core_low * invested_equity - core_value) / (1 - p.core_low)
               if invested_equity else total_base)
        scale = min(1.0, gap / total_base) if total_base > 0 else 0.0
        # primary core tier first, then heavier weights, then alphabetical
        ordered = sorted(core_candidates,
                         key=lambda x: (x.tier != p.etf_tier_key, -p.base_for(x.tier), x.ticker))
        plan = [(c, p.base_for(c.tier) * scale) for c in ordered]

        if p.core_rotate and len(ordered) > 1:
            # ROTATION (Rule 8): one order per run, not one per core line. Every order pays
            # its venue's fee minimum, so three $180 LSE orders pay USD 1.70 three times to
            # move the same $540 — splitting the core across lines triples the fee without
            # changing what you own. Give the whole round to whichever line sits furthest
            # below its intended share of the core; that self-rotates, because buying one
            # line makes another the most underweight next run. No new state needed.
            line_value = {c.ticker: 0.0 for c in ordered}
            for held in snap.positions:
                if held.ticker in line_value and price_of(held.ticker) is not None:
                    line_value[held.ticker] = held.shares * price_of(held.ticker)  # type: ignore[operator]

            def _underweight(c) -> float:
                target_share = p.base_for(c.tier) / total_base if total_base else 0.0
                actual_share = (line_value[c.ticker] / core_value) if core_value else 0.0
                return target_share - actual_share

            chosen = max(ordered, key=_underweight)  # ties keep `ordered`'s precedence
            plan = [(chosen, total_base * scale)]
            result.diagnostics.append(
                f"core rotation: {chosen.ticker} this run of "
                f"{len(ordered)} core lines ({', '.join(c.ticker for c in ordered)})")

        for c, raw_budget in plan:
            price = price_of(c.ticker)
            # The core gap widens on its own, so an under-floor round just waits.
            capped = _min_order(p, "core_build", raw_budget)
            if capped is None:
                result.diagnostics.append(
                    f"{c.ticker}: core contribution of ${raw_budget:,.0f} is under the "
                    f"${p.min_order_dollars:,.0f} per-order minimum — accumulating")
                continue
            budget, _ = capped
            # The contribution funds the fee as well as the shares (Rule 8).
            dollars = gross_for_budget(c.ticker, budget=budget, price=price, p=p)
            if dollars <= 0:
                if budget > 0:
                    result.diagnostics.append(
                        f"{c.ticker}: core contribution of ${budget:,.2f} is smaller than "
                        f"its fees — skipped")
                continue
            cause = f"index core is {core_w:.0%} of your portfolio — below the {p.core_low:.0%} target"
            notes = ["builds your index core toward 40–50%", "long-term anchor, not a trade"]
            metrics = {"price": price, "core_weight": core_w, "target": p.core_low,
                       "budget": budget}
            if len(plan) == 1 and len(ordered) > 1:
                notes.append(
                    f"one order instead of {len(ordered)} — your core lines rotate so you "
                    f"pay the exchange fee minimum once, not {len(ordered)}×")
                metrics["rotating"] = [x.ticker for x in ordered]

            # Same ticker can ALSO be a live dip-buy opportunity (Watchlist Opportunity's
            # trigger) — fold that into this one signal instead of emitting a second,
            # competing recommendation for the same name (§6-style precedence).
            md = snap.market.get(c.ticker)
            if md and md.high_52w:
                dd = md.price / md.high_52w - 1
                score = _opportunity_score(dd, md.vol_20d)
                if any(dd <= b + EPS for b in p.bands_for(c.asset_class)) and score >= p.watch_opportunity_min_score:
                    cause += f"; also {abs(dd):.0%} off its 52-week high (opportunity score {score:.1f})"
                    notes.append("bonus: also a dip-buy opportunity right now")
                    metrics.update({"opportunistic": True, "dd": dd, "high_52w": md.high_52w,
                                    "opportunity_score": score})

            result.signals.append(Signal(c.ticker, "core_build", "BUY",
                cause=cause, size_shares=_round_shares(dollars / price), size_dollars=dollars,
                notes=notes, metrics=metrics))

    # ---- Step 8: CASH FLOOR (Rule 6) ------------------------------------------
    excess_reserve = max(0.0, snap.reserve_balance - p.reserve_target * invested_equity)
    idle = snap.flags.idle_cash + excess_reserve
    if invested_equity and idle > p.cash_ceiling * invested_equity:
        # Idle cash keeps sitting there, so an under-floor sweep just waits for more.
        swept = _min_order(p, "cash_floor", idle - p.cash_ceiling * invested_equity)
        etf = (next((pos for pos in snap.positions if pos.tier == p.etf_tier_key), None)
               or next((pos for pos in snap.positions if p.is_core(pos.tier)), None))
        if swept is None:
            result.diagnostics.append(
                f"idle cash over the ceiling but the excess is under the "
                f"${p.min_order_dollars:,.0f} per-order minimum — leaving it")
        elif etf and price_of(etf.ticker):
            budget, _ = swept
            # The idle cash funds the fee as well as the shares (Rule 8).
            deploy = gross_for_budget(etf.ticker, budget=budget,
                                      price=price_of(etf.ticker), p=p)  # type: ignore[arg-type]
            result.signals.append(Signal(etf.ticker, "cash_floor", "DEPLOY",
                cause=f"idle capital ${idle:,.0f} > {p.cash_ceiling:.0%} ceiling — deploy excess into core",
                size_shares=_round_shares(deploy / price_of(etf.ticker)),  # type: ignore[arg-type]
                size_dollars=deploy, notes=["reserve up to target is exempt"],
                metrics={"price": price_of(etf.ticker), "idle": idle,
                         "ceiling": p.cash_ceiling, "budget": budget}))
        else:
            result.diagnostics.append("cash-floor tripped but no ETF core position to deploy into")

    # ---- Step 8b: TRANSACTION COSTS (Rule 8) ----------------------------------
    # Runs once, over every signal emitted above, so no rule has to remember to price
    # its own order. Must precede Step 10, which rewrites BUY -> REPLACE.
    _apply_cost_overlay(result, p)

    # ---- Step 9: EMERGENCY-FUND gate (warn-only on buys) ----------------------
    if not snap.flags.emergency_fund_ok:
        for s in result.signals:
            if s.action in ("BUY", "DEPLOY"):
                s.warnings.append("emergency fund not attested — confirm 6–12 months funded first")

    # ---- Step 10: DIVERSIFICATION (Rule 5) — new name at MAX_HOLDINGS ----------
    held = {pos.ticker for pos in snap.positions}
    # MAX_HOLDINGS counts individual STOCKS only (all ETFs — core or satellite — excluded).
    individual_count = sum(1 for pos in snap.positions if pos.asset_class != "ETF")
    if individual_count >= p.max_holdings:
        for s in result.signals:
            if s.action == "BUY" and s.ticker not in held and s.rule not in ("base_dca", "core_build"):
                s.rule = "replace"
                s.action = "REPLACE"
                s.notes.append(f"at MAX_HOLDINGS ({p.max_holdings}) — drop a name before adding {s.ticker}")

    result.band_state = band_state
    result.reserve_balance = snap.reserve_balance  # engine never mutates reserve; committed on DONE
    return result
