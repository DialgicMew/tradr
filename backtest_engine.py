"""
tradr Engine Backtest — Neutral Universe (SPY)

Tests the MECHANICS of the rule engine (not the stock picks) by running
four strategy variants on the same SPY daily data:

  A. Plain monthly DCA (baseline)
  B. DCA + double-down bands
  C. DCA + double-down + trim recycling
  D. DCA + double-down + trim + 50/50 reserve (full system)

Since SPY is the only holding, several rules collapse:
  - No contention / diversification / tier weights / PE gate.
  - ETF bands: -10 / -20 / -30 / -40, ARM at -5%.
  - Trim bands: +100 / +150 / +200% from avg basis, 20% of shares, 20pp buffer.
  - Reserve target: 20% of portfolio; cash ceiling: 5% above target.

All four arms receive the SAME total cash inflows over time — the only
difference is deployment timing.  This isolates whether the machinery
adds value vs. naive DCA.
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import json
import sys


# ── Parameters (from RULESET.md defaults) ────────────────────────────

BASE = 1000.0              # monthly base contribution ($)
DD_BANDS = [-0.10, -0.20, -0.30, -0.40]   # ETF drop bands
ARM_LINE = -0.05           # re-arm when dd recovers above -5%
DD_CAP_MULT = 3.0          # max cumulative DD per crash = 3x base
TRIM_BANDS = [1.00, 1.50, 2.00]           # +100%, +150%, +200% gain
TRIM_PCT = 0.20            # sell 20% of shares per trim band
TRIM_ARM_BUFFER = 0.20     # re-arm when gain drops 20pp below band
RESERVE_TARGET_PCT = 0.20  # 20% of portfolio earmarked
CASH_CEILING_PCT = 0.05    # 5% idle above reserve target triggers deploy


# ── Data ──────────────────────────────────────────────────────────────

def load_data(csv_path: str = "spy_data.csv") -> pd.DataFrame:
    print(f"Loading from {csv_path}...")
    df = pd.read_csv(csv_path, parse_dates=["Date"], index_col="Date")
    df.columns = ["close"]
    df = df.dropna().sort_index()
    df["high_52w"] = df["close"].rolling(252, min_periods=1).max()
    df["dd"] = df["close"] / df["high_52w"] - 1
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    print(f"  Loaded {len(df)} trading days: {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ── Strategy A: Plain DCA ─────────────────────────────────────────────

@dataclass
class PlainDCA:
    name: str = "A: Plain DCA"
    shares: float = 0.0
    cash: float = 0.0          # uninvested cash (should stay ~0)
    total_contributed: float = 0.0

    def month_start(self, price: float, row: pd.Series):
        self.total_contributed += BASE
        shares_bought = BASE / price
        self.shares += shares_bought

    def daily(self, price: float, row: pd.Series):
        pass

    def portfolio_value(self, price: float) -> float:
        return self.shares * price + self.cash


# ── Strategy B: DCA + Double-Down ─────────────────────────────────────

@dataclass
class DCAWithDD:
    name: str = "B: DCA + Double-Down"
    shares: float = 0.0
    cash: float = 0.0
    total_contributed: float = 0.0
    fired: Dict[float, bool] = field(default_factory=lambda: {b: False for b in DD_BANDS})
    crash_active: bool = False
    dd_spent_this_crash: float = 0.0

    def month_start(self, price: float, row: pd.Series):
        self.total_contributed += BASE
        self.shares += BASE / price

    def daily(self, price: float, row: pd.Series):
        dd = row["dd"]

        if self.crash_active and dd >= ARM_LINE:
            self.fired = {b: False for b in DD_BANDS}
            self.crash_active = False
            self.dd_spent_this_crash = 0.0

        fire_set = [b for b in DD_BANDS if not self.fired[b] and dd <= b]
        if fire_set:
            self.crash_active = True
            intended = len(fire_set) * 2 * BASE
            clamped = min(intended, DD_CAP_MULT * BASE - self.dd_spent_this_crash)
            clamped = max(clamped, 0)
            if clamped > 0:
                self.total_contributed += clamped
                self.shares += clamped / price
                self.dd_spent_this_crash += clamped
            for b in fire_set:
                self.fired[b] = True

    def portfolio_value(self, price: float) -> float:
        return self.shares * price + self.cash


# ── Strategy C: DCA + DD + Trim ───────────────────────────────────────

@dataclass
class DCAWithDDTrim:
    name: str = "C: DCA + DD + Trim"
    shares: float = 0.0
    cash: float = 0.0
    total_contributed: float = 0.0
    avg_basis: float = 0.0
    dd_fired: Dict[float, bool] = field(default_factory=lambda: {b: False for b in DD_BANDS})
    trim_fired: Dict[float, bool] = field(default_factory=lambda: {b: False for b in TRIM_BANDS})
    crash_active: bool = False
    dd_spent_this_crash: float = 0.0
    trimmed_today: bool = False

    def _update_basis(self, new_shares: float, price: float):
        if self.shares + new_shares == 0:
            return
        total_cost = self.avg_basis * self.shares + price * new_shares
        self.shares += new_shares
        self.avg_basis = total_cost / self.shares if self.shares > 0 else 0

    def month_start(self, price: float, row: pd.Series):
        self.total_contributed += BASE
        new_shares = BASE / price
        self._update_basis(new_shares, price)

    def daily(self, price: float, row: pd.Series):
        self.trimmed_today = False
        dd = row["dd"]
        gain = (price / self.avg_basis - 1) if self.avg_basis > 0 else 0

        # re-arm DD
        if self.crash_active and dd >= ARM_LINE:
            self.dd_fired = {b: False for b in DD_BANDS}
            self.crash_active = False
            self.dd_spent_this_crash = 0.0

        # re-arm trims
        for b in TRIM_BANDS:
            if self.trim_fired[b] and gain < (b - TRIM_ARM_BUFFER):
                self.trim_fired[b] = False

        # TRIM first (trim > DD precedence)
        trim_set = [b for b in TRIM_BANDS if not self.trim_fired[b] and gain >= b]
        if trim_set and self.shares > 0:
            self.trimmed_today = True
            for b in sorted(trim_set):
                sell_shares = self.shares * TRIM_PCT
                proceeds = sell_shares * price
                self.shares -= sell_shares
                self.cash += proceeds
                self.trim_fired[b] = True

        # DD (skipped if trimmed today)
        if not self.trimmed_today:
            dd_fire = [b for b in DD_BANDS if not self.dd_fired[b] and dd <= b]
            if dd_fire:
                self.crash_active = True
                intended = len(dd_fire) * 2 * BASE
                clamped = min(intended, DD_CAP_MULT * BASE - self.dd_spent_this_crash)
                clamped = max(clamped, 0)
                if clamped > 0:
                    self.total_contributed += clamped
                    new_shares = clamped / price
                    self._update_basis(new_shares, price)
                    self.dd_spent_this_crash += clamped
                for b in dd_fire:
                    self.dd_fired[b] = True

    def portfolio_value(self, price: float) -> float:
        return self.shares * price + self.cash


# ── Strategy D: Full System (DCA + DD + Trim + 50/50 Reserve) ─────────

@dataclass
class FullSystem:
    name: str = "D: Full System (DCA+DD+Trim+Reserve)"
    shares: float = 0.0
    cash: float = 0.0
    reserve: float = 0.0
    total_contributed: float = 0.0
    avg_basis: float = 0.0
    dd_fired: Dict[float, bool] = field(default_factory=lambda: {b: False for b in DD_BANDS})
    trim_fired: Dict[float, bool] = field(default_factory=lambda: {b: False for b in TRIM_BANDS})
    crash_active: bool = False
    dd_spent_this_crash: float = 0.0
    trimmed_today: bool = False

    def _update_basis(self, new_shares: float, price: float):
        if self.shares + new_shares == 0:
            return
        total_cost = self.avg_basis * self.shares + price * new_shares
        self.shares += new_shares
        self.avg_basis = total_cost / self.shares if self.shares > 0 else 0

    def _portfolio_equity(self, price: float) -> float:
        return self.shares * price

    def month_start(self, price: float, row: pd.Series):
        self.total_contributed += BASE
        dd = row["dd"]

        if self.crash_active:
            # bad market: deploy full base + draw from reserve (capped by DD logic)
            deploy = BASE
        else:
            # normal: 50/50 split
            deploy = BASE / 2.0
            self.reserve += BASE / 2.0

        new_shares = deploy / price
        self._update_basis(new_shares, price)

        # cash ceiling check: idle = cash + reserve above target
        pv = self._portfolio_equity(price) + self.cash + self.reserve
        reserve_target_abs = pv * RESERVE_TARGET_PCT
        idle = self.cash + max(0, self.reserve - reserve_target_abs)
        ceiling = pv * CASH_CEILING_PCT
        if idle > ceiling:
            excess = idle - ceiling
            deploy_excess = min(excess, self.cash + self.reserve)
            # draw from cash first, then reserve above target
            from_cash = min(self.cash, deploy_excess)
            self.cash -= from_cash
            from_reserve = min(deploy_excess - from_cash, max(0, self.reserve - reserve_target_abs))
            self.reserve -= from_reserve
            total_deploy = from_cash + from_reserve
            if total_deploy > 0:
                self._update_basis(total_deploy / price, price)

    def daily(self, price: float, row: pd.Series):
        self.trimmed_today = False
        dd = row["dd"]
        gain = (price / self.avg_basis - 1) if self.avg_basis > 0 else 0

        # re-arm DD
        if self.crash_active and dd >= ARM_LINE:
            self.dd_fired = {b: False for b in DD_BANDS}
            self.crash_active = False
            self.dd_spent_this_crash = 0.0

        # re-arm trims
        for b in TRIM_BANDS:
            if self.trim_fired[b] and gain < (b - TRIM_ARM_BUFFER):
                self.trim_fired[b] = False

        # TRIM
        trim_set = [b for b in TRIM_BANDS if not self.trim_fired[b] and gain >= b]
        if trim_set and self.shares > 0:
            self.trimmed_today = True
            for b in sorted(trim_set):
                sell_shares = self.shares * TRIM_PCT
                proceeds = sell_shares * price
                self.shares -= sell_shares
                self.reserve += proceeds       # recycle into reserve
                self.trim_fired[b] = True

        # DD (skipped if trimmed today; funded from reserve)
        if not self.trimmed_today:
            dd_fire = [b for b in DD_BANDS if not self.dd_fired[b] and dd <= b]
            if dd_fire:
                self.crash_active = True
                intended = len(dd_fire) * 2 * BASE
                clamped = min(intended, DD_CAP_MULT * BASE - self.dd_spent_this_crash)
                clamped = min(clamped, self.reserve)   # can only deploy what's in reserve
                clamped = max(clamped, 0)
                if clamped > 0:
                    self.reserve -= clamped
                    new_shares = clamped / price
                    self._update_basis(new_shares, price)
                    self.dd_spent_this_crash += clamped
                for b in dd_fire:
                    self.dd_fired[b] = True

    def portfolio_value(self, price: float) -> float:
        return self.shares * price + self.cash + self.reserve


# ── Runner ────────────────────────────────────────────────────────────

def get_monthly_dates(df: pd.DataFrame, day: int = 15) -> set:
    """Return the first trading day on or after the `day`th of each month."""
    dates = set()
    seen_months = set()
    for dt in df.index:
        key = (dt.year, dt.month)
        if key not in seen_months and dt.day >= day:
            seen_months.add(key)
            dates.add(dt)
    return dates


def run_backtest(df: pd.DataFrame):
    strategies = [PlainDCA(), DCAWithDD(), DCAWithDDTrim(), FullSystem()]
    monthly_dates = get_monthly_dates(df, day=15)

    history = {s.name: [] for s in strategies}

    for dt, row in df.iterrows():
        price = row["close"]

        for s in strategies:
            if dt in monthly_dates:
                s.month_start(price, row)
            s.daily(price, row)
            history[s.name].append({
                "date": dt,
                "pv": s.portfolio_value(price),
                "contributed": s.total_contributed,
            })

    return strategies, history


# ── Metrics ───────────────────────────────────────────────────────────

def compute_metrics(name: str, hist: List[dict], df: pd.DataFrame) -> dict:
    h = pd.DataFrame(hist)
    final_pv = h["pv"].iloc[-1]
    total_in = h["contributed"].iloc[-1]
    total_return = (final_pv / total_in - 1) * 100
    years = (h["date"].iloc[-1] - h["date"].iloc[0]).days / 365.25

    # time-weighted: approximate via monthly portfolio values
    monthly = h.set_index("date").resample("ME").last().dropna()
    if len(monthly) > 1:
        monthly_rets = monthly["pv"].pct_change().dropna()
        ann_return = ((1 + monthly_rets.mean()) ** 12 - 1) * 100
        ann_vol = monthly_rets.std() * (12 ** 0.5) * 100
        sharpe = (ann_return - 4.0) / ann_vol if ann_vol > 0 else 0  # 4% risk-free approx
    else:
        ann_return = ann_vol = sharpe = 0

    # max drawdown on portfolio value
    cummax = h["pv"].cummax()
    drawdown = (h["pv"] - cummax) / cummax
    max_dd = drawdown.min() * 100

    return {
        "strategy": name,
        "years": round(years, 1),
        "total_contributed": round(total_in, 0),
        "final_value": round(final_pv, 0),
        "total_return_pct": round(total_return, 1),
        "ann_return_pct": round(ann_return, 1),
        "ann_volatility_pct": round(ann_vol, 1),
        "sharpe_approx": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 1),
    }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    df = load_data("/Users/vatsalya.singh@zomato.com/Documents/tradr/spy_data.csv")
    strategies, history = run_backtest(df)

    print("\n" + "=" * 90)
    print("BACKTEST RESULTS — SPY (S&P 500 ETF)")
    print(f"Period: {df.index[0].date()} → {df.index[-1].date()}")
    print(f"Monthly base contribution: ${BASE:,.0f}")
    print("=" * 90)

    metrics = []
    for s in strategies:
        m = compute_metrics(s.name, history[s.name], df)
        metrics.append(m)

    baseline = metrics[0]

    headers = [
        ("Strategy", 42),
        ("Contributed", 14),
        ("Final Value", 14),
        ("Total Ret%", 11),
        ("Ann Ret%", 9),
        ("Ann Vol%", 9),
        ("Sharpe", 8),
        ("Max DD%", 9),
    ]
    header_line = "".join(h.ljust(w) for h, w in headers)
    print(f"\n{header_line}")
    print("-" * len(header_line))

    for m in metrics:
        row = [
            m["strategy"].ljust(42),
            f"${m['total_contributed']:>11,.0f}  ",
            f"${m['final_value']:>11,.0f}  ",
            f"{m['total_return_pct']:>8.1f}%  ",
            f"{m['ann_return_pct']:>6.1f}%  ",
            f"{m['ann_volatility_pct']:>6.1f}%  ",
            f"{m['sharpe_approx']:>6.2f}  ",
            f"{m['max_drawdown_pct']:>6.1f}%",
        ]
        print("".join(row))

    # relative comparison
    print(f"\n{'─' * 90}")
    print("RELATIVE TO PLAIN DCA (Strategy A):")
    print(f"{'─' * 90}")
    for m in metrics[1:]:
        delta_ret = m["total_return_pct"] - baseline["total_return_pct"]
        delta_sharpe = m["sharpe_approx"] - baseline["sharpe_approx"]
        delta_dd = m["max_drawdown_pct"] - baseline["max_drawdown_pct"]
        extra_contrib = m["total_contributed"] - baseline["total_contributed"]
        extra_val = m["final_value"] - baseline["final_value"]
        print(f"\n  {m['strategy']}:")
        print(f"    Extra contributed:  ${extra_contrib:>+12,.0f}")
        print(f"    Extra final value:  ${extra_val:>+12,.0f}")
        print(f"    Total return delta: {delta_ret:>+8.1f}pp")
        print(f"    Sharpe delta:       {delta_sharpe:>+8.2f}")
        print(f"    Max DD delta:       {delta_dd:>+8.1f}pp  {'(deeper)' if delta_dd < 0 else '(shallower)'}")

    # Value added per extra dollar contributed
    print(f"\n{'─' * 90}")
    print("EFFICIENCY: value generated per extra dollar contributed")
    print(f"{'─' * 90}")
    for m in metrics[1:]:
        extra_contrib = m["total_contributed"] - baseline["total_contributed"]
        extra_val = m["final_value"] - baseline["final_value"]
        if extra_contrib > 0:
            efficiency = extra_val / extra_contrib
            print(f"  {m['strategy']}: ${efficiency:.2f} of value per $1 extra contributed")
        else:
            print(f"  {m['strategy']}: no extra contribution")

    # Full system detail
    fs = strategies[3]
    print(f"\n{'─' * 90}")
    print(f"FULL SYSTEM (D) STATE AT END:")
    print(f"  Shares:  {fs.shares:,.2f}")
    print(f"  Reserve: ${fs.reserve:,.0f}")
    print(f"  Cash:    ${fs.cash:,.0f}")
    print(f"  Avg basis: ${fs.avg_basis:,.2f}")
    print(f"{'─' * 90}")

    # Save history for charting
    out = {}
    for s in strategies:
        h = pd.DataFrame(history[s.name])
        h = h.set_index("date").resample("ME").last()
        out[s.name] = {"date": h.index.strftime("%Y-%m-%d").tolist(),
                       "pv": [round(v, 2) for v in h["pv"].tolist()],
                       "contributed": [round(v, 2) for v in h["contributed"].tolist()]}

    outpath = "/Users/vatsalya.singh@zomato.com/Documents/tradr/backtest_results.json"
    with open(outpath, "w") as f:
        json.dump(out, f)
    print(f"\nMonthly series saved to {outpath} for charting.")


if __name__ == "__main__":
    main()
