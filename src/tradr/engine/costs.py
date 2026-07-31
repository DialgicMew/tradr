"""Transaction-cost model — IBKR Tiered, per order (ruleset Rule 8).

Pure arithmetic, no I/O: given a venue, a trade value and a share count, return the
itemised cost of putting that order on. The engine uses it to (a) show the real drag on
every alert and (b) keep the reserve ledger honest (a dip-buy draws price × shares *plus*
commission; a trim recycles proceeds *minus* commission).

The schedule is IBKR's *Tiered* plan, in USD, with Indian GST at 18% on the commission
component only (exchange and clearing fees are quoted GST-free, as on the IBKR sheet).
GBP-denominated exchange/clearing minimums are stored pre-converted to USD rather than
FX'd here — the engine has no FX feed, and these are cent-scale fixed costs. Re-check
`lse_exchange_min` / `lse_clearing` if GBPUSD moves a lot.

The asymmetry this model exists to expose: the LSE leg has a USD 1.70 commission floor
that does not dilute until ~$3,400 per order, while the US leg's floor is USD 0.35. Splitting
entries is nearly free on the US side and expensive on the LSE side.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tickers carrying an LSE listing suffix price/settle in GBP-denominated venue fees even
# when the line itself is USD-denominated (e.g. CSPX.L, VWRA.L, EIMI.L).
LSE_SUFFIX = ".L"

LSE = "LSE"
US = "US"


def venue_for(ticker: str) -> str:
    """Which fee schedule applies to this ticker. Suffix-based — matches how the
    market adapter names LSE lines (`CSPX.L`) versus US ones (`NVDA`)."""
    return LSE if ticker.upper().endswith(LSE_SUFFIX) else US


@dataclass(frozen=True)
class OrderCost:
    """Itemised one-order cost. All amounts USD; `ratio` is cost ÷ trade value."""

    venue: str
    commission: float
    gst: float
    exchange_fee: float
    clearing_fee: float
    total: float
    ratio: float

    def as_metrics(self) -> dict:
        """Flat, JSON-safe shape for Signal.metrics (the UI reads these keys)."""
        return {
            "venue": self.venue,
            "commission": round(self.commission, 4),
            "gst": round(self.gst, 4),
            "exchange_fee": round(self.exchange_fee, 4),
            "clearing_fee": round(self.clearing_fee, 4),
            "total": round(self.total, 4),
            "ratio": self.ratio,
        }


TIERED = "tiered"
FIXED = "fixed"


class UnconfiguredSchedule(ValueError):
    """Raised when a fee schedule needed for an estimate has not been supplied.

    Better a loud failure than a plausible invented number — the caller should ask the
    user for the actual fee off their broker statement instead.
    """


def estimate_order_cost(*, venue: str, trade_value: float, shares: float, p,
                        plan: str | None = None) -> OrderCost:
    """Cost of executing `trade_value` (USD) / `shares` as ONE order on `venue`.

    `p` is a `tradr.engine.params.Params`. `plan` selects IBKR's Tiered or Fixed schedule,
    defaulting to the account's `commission_plan` — pass it explicitly when back-filling an
    old fill that was traded under the other plan.

    Pass the un-rounded share count — the per-share tiers are cent-scale and the floors
    usually bind anyway.
    """
    trade_value = max(0.0, trade_value)
    shares = max(0.0, shares)
    plan = (plan or getattr(p, "commission_plan", TIERED) or TIERED).lower()

    if plan == FIXED:
        # Fixed is an all-in rate: exchange and clearing are bundled into the commission
        # rather than charged separately, which is the whole point of the plan.
        if venue == LSE:
            if p.fixed_lse_min <= 0:
                raise UnconfiguredSchedule(
                    "fixed-plan LSE commission minimum is not configured "
                    "(params.fixed_lse_min) — set it from your statement, or record the "
                    "actual fee for this trade")
            commission = max(trade_value * p.fixed_lse_pct, p.fixed_lse_min)
        else:
            commission = max(shares * p.fixed_us_per_share, p.fixed_us_min)
            if p.fixed_us_max_pct > 0:
                commission = min(commission, trade_value * p.fixed_us_max_pct)
        gst = commission * p.gst_rate
        total = commission + gst
        return OrderCost(venue=venue, commission=commission, gst=gst, exchange_fee=0.0,
                         clearing_fee=0.0, total=total,
                         ratio=total / trade_value if trade_value > 0 else 0.0)

    if venue == LSE:
        # 0.05% of trade value, floored at USD 1.70; fixed per-order venue fees.
        commission = max(trade_value * p.lse_commission_pct, p.lse_commission_min)
        exchange_fee = p.lse_exchange_min
        clearing_fee = p.lse_clearing
    else:
        # USD/share, floored at USD 0.35 and capped at a % of trade value (IBKR's
        # tiered maximum — it is what makes very small US orders survivable).
        commission = max(shares * p.us_commission_per_share, p.us_commission_min)
        if p.us_commission_max_pct > 0:
            commission = min(commission, trade_value * p.us_commission_max_pct)
        exchange_fee = 0.0
        clearing_fee = shares * p.us_clearing_per_share

    gst = commission * p.gst_rate
    total = commission + gst + exchange_fee + clearing_fee
    ratio = total / trade_value if trade_value > 0 else 0.0
    return OrderCost(venue=venue, commission=commission, gst=gst, exchange_fee=exchange_fee,
                     clearing_fee=clearing_fee, total=total, ratio=ratio)


def estimate_for_ticker(ticker: str, *, trade_value: float, shares: float, p,
                        plan: str | None = None) -> OrderCost:
    """Convenience wrapper: infer the venue from the ticker, then cost the order."""
    return estimate_order_cost(venue=venue_for(ticker), trade_value=trade_value,
                               shares=shares, p=p, plan=plan)


def gross_for_budget(ticker: str, *, budget: float, price: float, p) -> float:
    """Largest order value whose value + fees fits inside `budget`.

    For contribution-funded buys (core build, base DCA, cash floor) the fee comes out of
    the same monthly contribution as the shares — a $180 contribution buys ~$177.77 of an
    LSE line and hands $2.23 to IBKR. Reserve-funded dip-buys work the other way (the
    reserve pays the fee on top), so they size off the ladder and draw `_cash_needed`.

    The fee depends on the gross, which depends on the fee; two passes converge because
    the fee is dominated by a fixed floor.
    """
    if not p.commissions_on or budget <= 0 or price <= 0:
        return max(0.0, budget)
    gross = budget
    for _ in range(3):
        fee = estimate_for_ticker(ticker, trade_value=gross, shares=gross / price, p=p).total
        gross = budget - fee
        if gross <= 0:
            return 0.0
    return gross


def break_even_value(venue: str, p, *, target_ratio: float) -> float:
    """Smallest order on `venue` whose cost is ≤ `target_ratio` of trade value.

    Answers "how big does this order have to be before the fee floor stops hurting?" —
    used to tell the user *what* to consolidate to, not just that a small order is dear.
    Returns 0.0 if the target is unreachable at any size (percentage components alone
    already exceed it) or is already met at any size. Solved on the fee-floor branch, so
    it ignores the cent-scale per-share components — the floor is what dominates here.
    """
    if target_ratio <= 0:
        return 0.0
    if venue == LSE:
        # cost(v) = max(v·pct, min)·(1+gst) + fixed. Solve on the floor branch, where
        # cost is constant, then confirm the percentage branch would also satisfy it.
        fixed = p.lse_commission_min * (1 + p.gst_rate) + p.lse_exchange_min + p.lse_clearing
        if p.lse_commission_pct * (1 + p.gst_rate) >= target_ratio:
            return 0.0  # even a huge order stays above target
        return fixed / target_ratio
    fixed = p.us_commission_min * (1 + p.gst_rate)
    if p.us_commission_max_pct > 0 and p.us_commission_max_pct * (1 + p.gst_rate) <= target_ratio:
        return 0.0  # the tiered % cap already holds every order at/below target
    return fixed / target_ratio
