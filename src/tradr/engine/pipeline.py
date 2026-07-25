"""Engine Core — the deterministic daily pipeline (ruleset.md §4, precedence §6).

Pure function: `evaluate(snapshot) -> EngineResult`. No I/O, no clock. It PROPOSES
signals and the resulting band/reserve state; the Signal Service commits state on user
confirmation (ruleset §2). Pipeline step order is significant and mirrors §4.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from tradr.engine.models import (
    BandState,
    BandStatus,
    EngineResult,
    Macro,
    Position,
    Signal,
    Snapshot,
)
from tradr.engine.params import Params


# Tolerance for band-boundary comparisons — float makes 80/100-1 == -0.19999999996,
# which must still count as crossing the -0.20 band.
EPS = 1e-9


def _round_shares(x: float) -> float:
    return float(int(x))  # whole-share suggestions; keep dollars exact


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


def evaluate(snap: Snapshot) -> EngineResult:  # noqa: C901 — pipeline is intentionally linear
    p: Params = snap.params  # type: ignore[assignment]
    result = EngineResult(reserve_balance=snap.reserve_balance)

    # Work on a deep copy of band state so the engine stays side-effect free.
    band_state: dict[str, BandState] = {t: copy.deepcopy(b) for t, b in snap.band_state.items()}
    for pos in snap.positions:
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
        crossed = sorted(b for b, st in bs.trim_status.items()
                         if st is BandStatus.ARMED and gain >= b - EPS)
        if not crossed:
            continue
        # Velocity gate (Rule 2): only trim genuine momentum spikes, not slow
        # compounders. Band stays ARMED (may fire later if velocity spikes).
        if md.ret_60d < p.trim_velocity_pct - EPS:
            result.diagnostics.append(
                f"{pos.ticker}: trim suppressed — 60d gain {md.ret_60d:+.0%} "
                f"< velocity gate {p.trim_velocity_pct:+.0%}")
            continue
        # Gap-up fires all crossed bands; trim_pct of *running* shares each (compounds).
        remaining = pos.shares
        sold = 0.0
        for band in crossed:
            sell = remaining * p.trim_pct
            remaining -= sell
            sold += sell
            bs.trim_status[band] = BandStatus.PENDING
        # MIN_WEIGHT floor: never trim below min_weight of the portfolio.
        floor_shares = (p.min_weight * invested_equity) / md.price if invested_equity else 0.0
        max_sellable = max(0.0, pos.shares - floor_shares)
        capped = min(sold, max_sellable)
        notes = [f"gain {gain:+.0%}; bands {[f'{b:+.0%}' for b in crossed]}",
                 "proceeds recycle into reserve on DONE"]
        if capped < sold:
            notes.append(f"capped by MIN_WEIGHT floor ({p.min_weight:.0%})")
        trimmed_today.add(pos.ticker)
        result.signals.append(Signal(pos.ticker, "trim", "SELL",
            cause=f"{pos.ticker} up {gain:+.0%} vs basis — trim {p.trim_pct:.0%}/band",
            size_shares=_round_shares(capped), size_dollars=capped * md.price,
            band=crossed[-1], notes=notes,
            metrics={"price": md.price, "gain": gain, "ret_60d": md.ret_60d,
                     "trim_pct": p.trim_pct, "floored": capped < sold - EPS,
                     "bands": crossed, "avg_basis": pos.avg_basis}))

    # ---- Step 6: DOUBLE-DOWN (Rule 1) — skip names that trimmed today ----------
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
        crossed = [b for b in bands if bs.dd_status[b] is BandStatus.ARMED and dd <= b + EPS]
        if not crossed:
            continue
        deepest = min(crossed)
        deepest_k = bands.index(deepest) + 1
        target = _dd_cumulative_target(p, deepest_k)
        new_mult = max(0.0, target - bs.dd_deployed_mult)  # clamp to 3× cap
        base_i = p.base_for(pos.tier)
        dollars = new_mult * base_i
        dd_candidates.append((dd, pos, md, crossed, dollars))
        for b in crossed:
            bs.dd_status[b] = BandStatus.PENDING

    # Shared-reserve contention: deepest dd first; ties T1<T2 then alphabetical.
    tier_rank = {"T1": 0, "T2": 1, "ETF": 2}
    dd_candidates.sort(key=lambda c: (c[0], tier_rank.get(c[1].tier, 9), c[1].ticker))
    available = snap.reserve_balance
    for dd, pos, md, crossed, dollars in dd_candidates:
        warnings = []
        if dollars <= 0:
            warnings.append(f"at 3× cap already — suggest $0 (cap {p.dd_cap_mult:g}× base)")
        elif dollars > available:
            warnings.append(f"reserve short by ${dollars - available:,.0f} — top up or fund from cash")
        else:
            available -= dollars
        result.signals.append(Signal(pos.ticker, "double_down", "BUY",
            cause=f"{pos.ticker} {dd:+.0%} off 52wk high — bands {[f'{b:+.0%}' for b in crossed]}",
            size_shares=_round_shares(dollars / md.price) if dollars > 0 else 0.0,
            size_dollars=dollars, band=min(crossed), warnings=warnings,
            notes=["PE-exempt (dip buy)", "funded from reserve"],
            metrics={"price": md.price, "dd": dd, "high_52w": md.high_52w,
                     "bands": crossed, "asset_class": pos.asset_class,
                     "reserve_short": any("reserve short" in w for w in warnings)}))

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
            deploy = deploy_base * mult
            notes = [f"bank ${bank:,.0f} to reserve" if bank else "no auto-bank", _pe_zone(md.fwd_pe)]
            if brake:
                notes.append("momentum brake ×0.5 (30d > +20%)")
            if frothy:
                notes.append("froth overlay ×0.5 (S&P fwd P/E rich)")
            result.signals.append(Signal(pos.ticker, "base_dca", "BUY",
                cause=f"scheduled DCA — {pos.tier} base ${base_i:,.0f}",
                size_shares=_round_shares(deploy / md.price), size_dollars=deploy,
                notes=notes,
                metrics={"price": md.price, "deploy": deploy, "bank": bank,
                         "tier": pos.tier, "brake": brake, "froth": frothy, "steered": False}))

    # ---- Step 7b: CORE BUILD (Rule 3, proactive) — keep the index core ≥ 40% ---
    # Runs every evaluation, not just the DCA day. Targets a subscribed core ETF even
    # at zero shares, so the core can be bootstrapped from nothing.
    core_candidates = [c for c in (list(snap.positions) + list(snap.watchlist))
                       if p.is_core(c.tier) and price_of(c.ticker) is not None]
    core_value = sum(pos.shares * price_of(pos.ticker) for pos in snap.positions  # type: ignore[operator]
                     if p.is_core(pos.tier) and price_of(pos.ticker) is not None)
    core_w = (core_value / invested_equity) if invested_equity else 0.0
    if core_candidates and core_w < p.core_low - EPS:
        # Split the contribution across EVERY core ETF by its tier weight (e.g. 60/40),
        # scaled down so the whole round never overshoots the 40% target.
        total_base = sum(p.base_for(c.tier) for c in core_candidates)
        gap = ((p.core_low * invested_equity - core_value) / (1 - p.core_low)
               if invested_equity else total_base)
        scale = min(1.0, gap / total_base) if total_base > 0 else 0.0
        # primary core tier first, then heavier weights, then alphabetical
        for c in sorted(core_candidates,
                        key=lambda x: (x.tier != p.etf_tier_key, -p.base_for(x.tier), x.ticker)):
            dollars = p.base_for(c.tier) * scale
            if dollars <= 0:
                continue
            price = price_of(c.ticker)
            result.signals.append(Signal(c.ticker, "core_build", "BUY",
                cause=f"index core is {core_w:.0%} of your portfolio — below the {p.core_low:.0%} target",
                size_shares=_round_shares(dollars / price), size_dollars=dollars,
                notes=["builds your index core toward 40–50%", "long-term anchor, not a trade"],
                metrics={"price": price, "core_weight": core_w, "target": p.core_low}))

    # ---- Step 8: CASH FLOOR (Rule 6) ------------------------------------------
    excess_reserve = max(0.0, snap.reserve_balance - p.reserve_target * invested_equity)
    idle = snap.flags.idle_cash + excess_reserve
    if invested_equity and idle > p.cash_ceiling * invested_equity:
        deploy = idle - p.cash_ceiling * invested_equity
        etf = (next((pos for pos in snap.positions if pos.tier == p.etf_tier_key), None)
               or next((pos for pos in snap.positions if p.is_core(pos.tier)), None))
        if etf and price_of(etf.ticker):
            result.signals.append(Signal(etf.ticker, "cash_floor", "DEPLOY",
                cause=f"idle capital ${idle:,.0f} > {p.cash_ceiling:.0%} ceiling — deploy excess into core",
                size_shares=_round_shares(deploy / price_of(etf.ticker)),  # type: ignore[arg-type]
                size_dollars=deploy, notes=["reserve up to target is exempt"],
                metrics={"price": price_of(etf.ticker), "idle": idle,
                         "ceiling": p.cash_ceiling}))
        else:
            result.diagnostics.append("cash-floor tripped but no ETF core position to deploy into")

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
