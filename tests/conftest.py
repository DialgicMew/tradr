"""Test builders for engine snapshots. Generic values only — no personal data."""

from __future__ import annotations

import pytest

from tradr.engine import (
    Flags,
    Macro,
    MarketData,
    Params,
    Position,
    Snapshot,
    demo_params,
)


def pos(ticker: str, *, tier: str = "T1", shares: float = 100.0,
        avg_basis: float = 100.0, asset_class: str = "STOCK",
        last_buy_date: str | None = "2026-07-20") -> Position:
    # Default last_buy_date is just a few days before the default snapshot `as_of`
    # (2026-07-24) — well under the 30d rolling cadence window — so existing tests that
    # don't care about cadence don't accidentally trip it. Pass last_buy_date=None for
    # "never bought" or an older date to test the cadence leg explicitly.
    return Position(ticker=ticker, tier=tier, shares=shares,
                    avg_basis=avg_basis, asset_class=asset_class,
                    last_buy_date=last_buy_date)


def md(ticker: str, *, price: float, high_52w: float | None = None,
       ret_30d: float = 0.0, ret_60d: float = 0.5, fwd_pe: float | None = None,
       vol_20d: float = 0.0) -> MarketData:
    # ret_60d defaults high so trim tests pass the velocity gate unless overridden.
    return MarketData(ticker=ticker, price=price,
                      high_52w=high_52w if high_52w is not None else price,
                      ret_30d=ret_30d, ret_60d=ret_60d, fwd_pe=fwd_pe, vol_20d=vol_20d)


def make_snapshot(positions, market, *, reserve=1_000_000.0, as_of="2026-07-24",
                  macro=None, flags=None, avoid=frozenset(), band_state=None,
                  params: Params | None = None, watchlist=None) -> Snapshot:
    return Snapshot(
        as_of=as_of,
        positions=list(positions),
        market={m.ticker: m for m in market},
        band_state=band_state or {},
        reserve_balance=reserve,
        params=params or demo_params(),
        macro=macro or Macro(),
        flags=flags or Flags(emergency_fund_ok=True),
        avoid=avoid,
        watchlist=list(watchlist) if watchlist else [],
    )


@pytest.fixture
def p() -> Params:
    return demo_params()


@pytest.fixture
def db():
    """Fresh in-memory SQLite session (services take a session, so no singleton needed)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tradr.db import models  # noqa: F401 — register tables
    from tradr.db.base import Base

    eng = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


class StubProvider:
    """In-memory market-data provider for tests."""

    def __init__(self, data):
        self.data = {k.upper(): v for k, v in data.items()}

    def get_stock(self, ticker):
        return self.data.get(ticker.upper())
