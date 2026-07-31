"""Signal service — bridges the pure engine and the database.

- build_snapshot: assemble the engine input from DB + market adapter.
- persist_result: store proposed band state + new PENDING signals (deduped).
- confirm_signal / skip_signal: the §2 lifecycle; DONE updates ledger, latches bands,
  moves the reserve. Nothing is ever assumed executed — only user confirmation advances.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tradr.db.models import (
    EngineStateRow,
    MarketSnapshotRow,
    SignalRow,
    UniverseRow,
)
from tradr.engine import (
    BandState,
    BandStatus,
    Position,
    Snapshot,
)
from tradr.engine import (
    costs as engine_costs,
)
from tradr.engine import (
    evaluate as engine_evaluate,
)
from tradr.market.adapter import MarketDataProvider
from tradr.services import config_service as cfg
from tradr.services import ledger as ledger_svc

# --- band-state (de)serialisation: JSON uses string keys ---------------------

def _load_band_state(row: EngineStateRow) -> BandState:
    return BandState(
        ticker=row.ticker,
        dd_status={float(k): BandStatus(v) for k, v in (row.dd_status or {}).items()},
        trim_status={float(k): BandStatus(v) for k, v in (row.trim_status or {}).items()},
        dd_deployed_mult=row.dd_deployed_mult,
        spike_peak=row.spike_peak,
    )


def _save_band_state(s: Session, bs: BandState) -> None:
    row = s.get(EngineStateRow, bs.ticker) or EngineStateRow(ticker=bs.ticker)
    row.dd_status = {str(k): v.value for k, v in bs.dd_status.items()}
    row.trim_status = {str(k): v.value for k, v in bs.trim_status.items()}
    row.dd_deployed_mult = bs.dd_deployed_mult
    row.spike_peak = bs.spike_peak
    s.merge(row)


# --- snapshot ---------------------------------------------------------------

def build_snapshot(s: Session, provider: MarketDataProvider, *, as_of: date,
                   is_dca_day: bool) -> Snapshot:
    positions = [
        Position(ticker=p.ticker, tier=p.tier, shares=p.shares,
                 avg_basis=p.avg_basis, asset_class=p.asset_class)
        for p in ledger_svc.derive_positions(s)
    ]
    held = {p.ticker for p in positions}
    universe = {u.ticker: u for u in s.scalars(select(UniverseRow)).all()}
    tickers = held | {t for t, u in universe.items() if u.subscribed and not u.is_avoid}

    market = {}
    for t in sorted(tickers):
        md = provider.get_stock(t)
        if md is None:
            continue
        market[t] = md
        s.add(MarketSnapshotRow(ticker=t, as_of=as_of, price=md.price, high_52w=md.high_52w,
                                ret_30d=md.ret_30d, ret_60d=md.ret_60d, fwd_pe=md.fwd_pe))

    band_state = {r.ticker: _load_band_state(r) for r in s.scalars(select(EngineStateRow)).all()}

    # Subscribed-but-unheld tickers → watchlist (lets the core be bootstrapped from 0 shares).
    watchlist = [
        Position(ticker=t, tier=u.tier, shares=0.0, avg_basis=0.0, asset_class=u.asset_class)
        for t, u in universe.items()
        if u.subscribed and not u.is_avoid and t not in held
    ]

    return Snapshot(
        as_of=as_of.isoformat(),
        is_dca_day=is_dca_day,
        positions=positions,
        market=market,
        band_state=band_state,
        reserve_balance=ledger_svc.reserve_balance(s),
        params=cfg.build_params(s),
        macro=cfg.latest_macro(s),
        flags=cfg.build_flags(s),
        avoid=cfg.avoid_set(s),
        watchlist=watchlist,
    )


# --- persist engine output ---------------------------------------------------

def persist_result(s: Session, result) -> list[SignalRow]:
    """Replace all outstanding alerts with this run's fresh signals.

    PENDING rows are wiped before inserting the new batch so every alert a
    user sees always carries this run's price/metrics — no cross-run relic
    can sit alongside a signal from a later run for the same ticker.
    """
    for bs in result.band_state.values():
        _save_band_state(s, bs)

    s.execute(delete(SignalRow).where(SignalRow.state == "PENDING"))

    created: list[SignalRow] = []
    for sig in result.signals:
        expired = any("EXPIRED" in n for n in sig.notes)
        row = SignalRow(
            ticker=sig.ticker, rule=sig.rule, action=sig.action, cause=sig.cause,
            size_shares=sig.size_shares, size_dollars=sig.size_dollars, band=sig.band,
            warnings=list(sig.warnings), notes=list(sig.notes),
            metrics=dict(sig.metrics),
            state="EXPIRED" if expired else "PENDING",
        )
        s.add(row)
        s.flush()
        if not expired:
            created.append(row)
    return created


def run_and_persist(s: Session, provider: MarketDataProvider, *, as_of: date,
                    is_dca_day: bool) -> list[SignalRow]:
    snap = build_snapshot(s, provider, as_of=as_of, is_dca_day=is_dca_day)
    result = engine_evaluate(snap)
    return persist_result(s, result)


# --- lifecycle (§2) ----------------------------------------------------------

def _tier_of(s: Session, ticker: str) -> str:
    u = s.get(UniverseRow, ticker)
    return u.tier if u else "T1"


def _fees_on_fill(params, ticker: str, gross: float, qty: float) -> float:
    """Estimated IBKR cost of the fill the user just recorded (Rule 8).

    Estimated, not actual — tradr never sees the broker statement. It keeps the reserve
    ledger from drifting a few dollars richer than the account every month.
    """
    if not params.commissions_on or gross <= 0:
        return 0.0
    return engine_costs.estimate_for_ticker(ticker, trade_value=gross, shares=qty,
                                            p=params).total


def _latch_band(s: Session, ticker: str, ladder: str, band: float | None,
                status: BandStatus) -> None:
    if band is None:
        return
    row = s.get(EngineStateRow, ticker)
    if row is None:
        return
    field = row.dd_status if ladder == "dd" else row.trim_status
    field = dict(field or {})
    field[str(band)] = status.value
    if ladder == "dd":
        row.dd_status = field
    else:
        row.trim_status = field
    s.flush()


def confirm_signal(s: Session, signal_id: int, *, fill_qty: float, fill_price: float,
                   trade_date: date | None = None) -> SignalRow:
    sig = s.get(SignalRow, signal_id)
    if sig is None:
        raise KeyError(f"signal {signal_id} not found")
    if sig.state != "PENDING":
        raise ValueError(f"signal {signal_id} is {sig.state}, not PENDING")
    td = trade_date or date.today()
    params = cfg.build_params(s)
    gross = fill_qty * fill_price
    # Every fill carries its estimated cost, whichever pot paid it — that is what makes
    # "fees paid this year" answerable (Rule 8).
    fees = _fees_on_fill(params, sig.ticker, gross, fill_qty)

    if sig.action in ("BUY", "DEPLOY", "REPLACE"):
        lot = ledger_svc.record_trade(s, ticker=sig.ticker, side="buy", qty=fill_qty,
                                      price=fill_price, trade_date=td, source_signal_id=sig.id,
                                      fee=fees)
        if sig.rule in ("double_down", "watch_opportunity"):
            # Reserve-funded: the reserve pays for the shares AND the fee, as separate
            # lines so the fee is visible in the ledger rather than buried in the draw.
            # The 3x cap measures capital put INTO the name, so it counts gross only.
            ledger_svc.add_reserve(s, kind="dd_draw", amount=-gross,
                                   note=f"{sig.rule} {sig.ticker}", source_lot_id=lot.id)
            if fees:
                ledger_svc.add_reserve(s, kind="fee", amount=-fees,
                                       note=f"IBKR fees — {sig.rule} {sig.ticker}",
                                       source_lot_id=lot.id)
            base_i = params.base_for(_tier_of(s, sig.ticker))
            row = s.get(EngineStateRow, sig.ticker)
            if row is not None and base_i:
                # Counts against the same 3x-per-name cap once it becomes a held
                # position, whether the first buy came from double-down or here.
                row.dd_deployed_mult += gross / base_i
            _latch_band(s, sig.ticker, "dd", sig.band, BandStatus.FIRED)
        elif sig.rule == "base_dca":
            if params.auto_bank_5050:
                base_i = params.base_for(_tier_of(s, sig.ticker))
                ledger_svc.add_reserve(s, kind="base_bank", amount=base_i / 2,
                                       note=f"50/50 bank {sig.ticker}", source_lot_id=lot.id)
        # core_build / base_dca / cash_floor are contribution-funded: the engine already
        # sized the order so shares + fee fit the contribution, so no reserve movement.
    elif sig.action == "SELL":
        lot = ledger_svc.record_trade(s, ticker=sig.ticker, side="sell", qty=fill_qty,
                                      price=fill_price, trade_date=td, source_signal_id=sig.id,
                                      fee=fees)
        ledger_svc.add_reserve(s, kind="trim_recycle", amount=gross,
                               note=f"trim proceeds {sig.ticker}", source_lot_id=lot.id)
        if fees:
            ledger_svc.add_reserve(s, kind="fee", amount=-fees,
                                   note=f"IBKR fees — trim {sig.ticker}", source_lot_id=lot.id)
        _latch_band(s, sig.ticker, "trim", sig.band, BandStatus.FIRED)

    sig.state = "DONE"
    sig.fill_qty = fill_qty
    sig.fill_price = fill_price
    sig.resolved_at = datetime.now()
    s.flush()
    return sig


def record_manual_trade(s: Session, *, ticker: str, side: str, qty: float, price: float,
                        trade_date: date, fee: float = 0.0,
                        fund_from_reserve: bool = False) -> dict:
    """Log a hand-entered fill (Trades page) and, when it stands in for something you'd
    otherwise have Approved on Alerts, give it the same engine-state consequences a
    confirmed signal would have (§2/§8) — so it does not keep generating alerts for a
    condition you already acted on, and only fires again once the band genuinely re-arms.

    `fund_from_reserve` is the signal that this fill IS that stand-in — a dip-buy or trim
    you placed yourself outside Alerts, not a routine contribution. Reconciliation only
    runs then: an ordinary contribution must never silently latch or re-arm a band it was
    never actually checked against.

    Reconciliation fires EVERY currently-PENDING band on the relevant ladder, not just the
    deepest — a signal-confirmed fill only ever latches `sig.band` (the deepest), which
    left shallower bands stuck PENDING forever when several crossed in the same run. A
    manual entry has no single `sig.band` to anchor on, so it settles the whole ladder;
    re-arming afterward still only happens through the normal Step 4 condition (price
    recovers above ARM_* for dd, or gain retraces past TRIM_ARM_BUFFER for trim) — nothing
    here bypasses that.
    """
    lot, reserve_delta = ledger_svc.record_manual_trade(
        s, ticker=ticker, side=side, qty=qty, price=price, trade_date=trade_date, fee=fee,
        fund_from_reserve=fund_from_reserve)

    fired_bands: list[float] = []
    expired_signal_ids: list[int] = []
    if fund_from_reserve:
        fired_bands, expired_signal_ids = _reconcile_bands_for_manual_fill(
            s, ticker=ticker, side=side, qty=qty, price=price)

    return {"lot_id": lot.id, "fee": fee, "reserve_delta": reserve_delta,
            "bands_fired": fired_bands, "signals_expired": expired_signal_ids}


# Same boundary tolerance as the engine's own band comparisons (pipeline.EPS) — kept as a
# local copy rather than importing pipeline internals into the service layer.
_EPS = 1e-9


def _latest_snapshot(s: Session, ticker: str) -> MarketSnapshotRow | None:
    return s.scalars(
        select(MarketSnapshotRow).where(MarketSnapshotRow.ticker == ticker)
        .order_by(MarketSnapshotRow.id.desc())
    ).first()


def _reconcile_bands_for_manual_fill(s: Session, *, ticker: str, side: str, qty: float,
                                     price: float) -> tuple[list[float], list[int]]:
    """Which bands this FILL satisfies — computed from price vs. the ladder, the same
    test the engine itself runs (`dd <= band` / `gain >= band`) — not just whichever
    band happens to still show a live PENDING alert.

    That distinction matters: a band can sit ARMED rather than PENDING at the moment you
    log the trade (e.g. right after a void re-armed it, or simply because no run has
    re-crossed it since), and PENDING-only matching would treat that as "nothing to
    settle" — leaving the ladder ARMED so the very next engine run re-crosses it and
    re-alerts for a dip you already bought. Matching on price against the ladder instead
    means it doesn't matter what the stored status happened to be.

    Falls back to whichever bands are PENDING when there's no cached market snapshot for
    the ticker yet (a brand-new name with no engine run behind it) — there is no 52-week
    high to test the fill's price against, so a live alert is the only available signal.
    """
    row = s.get(EngineStateRow, ticker)
    if row is None:
        return [], []  # no ladder has ever been initialized for this name — nothing to settle
    bs = _load_band_state(row)
    params = cfg.build_params(s)

    if side == "buy":
        statuses, rules = bs.dd_status, ("double_down", "watch_opportunity")
        uni = s.get(UniverseRow, ticker)
        bands = params.bands_for(uni.asset_class if uni else "STOCK")
        snap = _latest_snapshot(s, ticker)
        if snap and snap.high_52w:
            dd = price / snap.high_52w - 1
            crossed = {b for b in bands if dd <= b + _EPS}
        else:
            crossed = {b for b, st in statuses.items() if st is BandStatus.PENDING}
    else:
        statuses, rules = bs.trim_status, ("trim",)
        pos = next((p for p in ledger_svc.derive_positions(s) if p.ticker == ticker), None)
        if pos and pos.avg_basis:
            gain = price / pos.avg_basis - 1
            crossed = {b for b in params.trim_bands if gain >= b - _EPS}
        else:
            crossed = {b for b, st in statuses.items() if st is BandStatus.PENDING}

    # Already-FIRED bands need no action; re-marking them isn't wrong, just noise.
    fired = sorted(b for b in crossed if statuses.get(b) is not BandStatus.FIRED)
    for b in fired:
        statuses[b] = BandStatus.FIRED

    if side == "buy":
        # Counts toward the cap whenever a reserve-funded buy lands in this name, whether
        # or not a NEW band happened to fire this time (e.g. topping up a dip that had
        # already fired every band on the ladder) — same as confirm_signal's unconditional
        # increment for double_down/watch_opportunity.
        base_i = params.base_for(_tier_of(s, ticker))
        if base_i:
            # Mutate `bs`, not `row` — `_save_band_state` below writes dd_deployed_mult
            # FROM `bs` and would silently clobber a direct write to `row` with the stale
            # value it loaded before this increment.
            bs.dd_deployed_mult += (qty * price) / base_i
    _save_band_state(s, bs)

    expired_ids: list[int] = []
    if fired:
        # The alert(s) this fill matched are now stale — retire them immediately rather
        # than leaving them in Alerts until the next engine run wipes/re-derives PENDING.
        stale = s.scalars(select(SignalRow).where(
            SignalRow.ticker == ticker, SignalRow.state == "PENDING",
            SignalRow.rule.in_(rules))).all()
        for row_sig in stale:
            row_sig.state = "EXPIRED"
            row_sig.notes = [*row_sig.notes, "EXPIRED: matched by a manually recorded trade"]
            row_sig.resolved_at = datetime.now()
            expired_ids.append(row_sig.id)
    s.flush()
    return fired, expired_ids


def void_trade(s: Session, lot_id: int) -> dict:
    """Undo a recorded fill and everything confirming it caused (§2).

    `ledger.void_lot` alone only flags the lot — the reserve draw, the 3x-cap counter and
    the latched band all survive it, which leaves the name looking funded and capped by a
    trade that no longer exists. This reverses all three.

    Reserve movements are reversed with COMPENSATING ENTRIES, never deletions: the ledger
    is append-only so the original draw and its reversal both stay visible (same principle
    as `voided` on the lot itself).
    """
    from tradr.db.models import Lot

    lot = s.get(Lot, lot_id)
    if lot is None:
        raise KeyError(f"lot {lot_id} not found")

    already_reversed = any(e.kind == "void_reversal"
                           for e in ledger_svc.reserve_entries_for_lot(s, lot_id))
    ledger_svc.void_lot(s, lot_id)

    reversed_amount = 0.0
    if not already_reversed:
        for e in ledger_svc.reserve_entries_for_lot(s, lot_id):
            if e.kind == "void_reversal":
                continue
            ledger_svc.add_reserve(s, kind="void_reversal", amount=-e.amount,
                                   note=f"reverses #{e.id} ({e.kind}) — trade {lot_id} voided",
                                   source_lot_id=lot_id)
            reversed_amount += -e.amount

    # Give back the 3x-cap headroom this fill consumed, and re-arm the band it latched.
    # The decrement is recomputed from the lot at CURRENT params; it is clamped at zero, so
    # a since-changed base_unit can under-refund but never drive the counter negative.
    sig = s.get(SignalRow, lot.source_signal_id) if lot.source_signal_id else None
    rearmed_band = None
    if sig is not None and sig.rule in ("double_down", "watch_opportunity"):
        row = s.get(EngineStateRow, lot.ticker)
        base_i = cfg.build_params(s).base_for(_tier_of(s, lot.ticker))
        if row is not None and base_i:
            row.dd_deployed_mult = max(0.0, row.dd_deployed_mult - (lot.qty * lot.price) / base_i)
        _latch_band(s, lot.ticker, "dd", sig.band, BandStatus.ARMED)
        rearmed_band = sig.band
    elif sig is not None and sig.rule == "trim":
        _latch_band(s, lot.ticker, "trim", sig.band, BandStatus.ARMED)
        rearmed_band = sig.band

    s.flush()
    return {"voided": lot_id, "ticker": lot.ticker, "reserve_restored": reversed_amount,
            "rearmed_band": rearmed_band}


def skip_signal(s: Session, signal_id: int) -> SignalRow:
    sig = s.get(SignalRow, signal_id)
    if sig is None:
        raise KeyError(f"signal {signal_id} not found")
    if sig.state != "PENDING":
        raise ValueError(f"signal {signal_id} is {sig.state}, not PENDING")
    params = cfg.build_params(s)
    sig.state = "SKIPPED"
    sig.snooze_until = date.today() + timedelta(days=params.snooze_days)
    # Return the band to ARMED (SNOOZED = quiet-then-rearm, ruleset §2).
    ladder = "dd" if sig.rule in ("double_down", "watch_opportunity") else "trim" if sig.rule == "trim" else None
    if ladder:
        _latch_band(s, sig.ticker, ladder, sig.band, BandStatus.ARMED)
    s.flush()
    return sig
