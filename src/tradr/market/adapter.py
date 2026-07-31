"""Market-data adapter (ARCHITECTURE §2).

The engine must never touch raw price series (ruleset §0) — so the ADAPTER computes
52wk-high / 30d / 60d returns and hands the engine finished figures. Providers are
swappable behind `MarketDataProvider`. Fail-safe: on any error, return None so the
runner skips that ticker for the day (never act on a blank price).

Forward P/E is left None here (free tiers rarely carry it); macro fwd-P/E comes from the
manual UI entry. Swap in a paid provider later without touching the engine.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

import httpx

from tradr.engine import MarketData


class MarketDataProvider(Protocol):
    def get_stock(self, ticker: str) -> MarketData | None: ...


def _trailing_vol(closes: list[float], window: int = 20) -> float:
    """Stddev of daily returns over the trailing `window` days — this ticker's own
    'normal' volatility, so a move can be judged relative to itself (opportunity scoring)."""
    tail = closes[-(window + 1):]
    if len(tail) < 2:
        return 0.0
    rets = [tail[i] / tail[i - 1] - 1 for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return var ** 0.5


class NoneProvider:
    """Offline provider — used in tests and before a provider is configured."""

    def get_stock(self, ticker: str) -> MarketData | None:
        return None


class YahooProvider:
    """Free, keyless daily closes from Yahoo Finance's chart endpoint (JSON).

    Unofficial but widely used; needs a browser User-Agent. 52wk-high comes from the
    endpoint's own `meta` (or falls back to max close); 30d/60d returns are computed here
    in the adapter so the engine never touches a raw series. fwd P/E left None (manual).
    """

    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    HEADERS: ClassVar[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=15.0, headers=self.HEADERS)

    def get_stock(self, ticker: str) -> MarketData | None:
        try:
            r = self._client.get(self.URL.format(ticker=ticker.upper()),
                                  params={"range": "1y", "interval": "1d"})
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            meta = res.get("meta", {})
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        except Exception:
            return None
        if len(closes) < 2:
            return None
        price = float(meta.get("regularMarketPrice") or closes[-1])
        high_52w = float(meta.get("fiftyTwoWeekHigh") or max(closes))
        ret_30d = price / closes[-31] - 1 if len(closes) > 31 else 0.0
        ret_60d = price / closes[-61] - 1 if len(closes) > 61 else 0.0
        vol_20d = _trailing_vol(closes)
        return MarketData(ticker=ticker.upper(), price=price, high_52w=high_52w,
                          ret_30d=ret_30d, ret_60d=ret_60d, fwd_pe=None, vol_20d=vol_20d)


class StooqProvider:
    """Free daily closes from stooq.com. NOTE: stooq now gates automated requests behind
    a JS bot-challenge, so this often fails — prefer YahooProvider. Kept for completeness."""

    BASE = "https://stooq.com/q/d/l/"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10.0)

    def get_stock(self, ticker: str) -> MarketData | None:
        try:
            closes = self._daily_closes(ticker)
        except Exception:
            return None
        if len(closes) < 2:
            return None
        price = closes[-1]
        window = closes[-252:]  # ~52 weeks of trading days
        high_52w = max(window)
        ret_30d = price / closes[-31] - 1 if len(closes) > 31 else 0.0
        ret_60d = price / closes[-61] - 1 if len(closes) > 61 else 0.0
        vol_20d = _trailing_vol(closes)
        return MarketData(ticker=ticker.upper(), price=price, high_52w=high_52w,
                          ret_30d=ret_30d, ret_60d=ret_60d, fwd_pe=None, vol_20d=vol_20d)

    def _daily_closes(self, ticker: str) -> list[float]:
        sym = ticker.lower()
        if "." not in sym:
            sym = f"{sym}.us"
        resp = self._client.get(self.BASE, params={"s": sym, "i": "d"})
        resp.raise_for_status()
        lines = resp.text.strip().splitlines()
        if not lines or not lines[0].lower().startswith("date"):
            return []
        closes: list[float] = []
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) >= 5:
                try:
                    closes.append(float(parts[4]))  # Close column
                except ValueError:
                    continue
        return closes


def get_provider(name: str) -> MarketDataProvider:
    if name == "yahoo":
        return YahooProvider()
    if name == "stooq":
        return StooqProvider()
    return NoneProvider()
