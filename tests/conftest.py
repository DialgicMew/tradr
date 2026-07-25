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
        avg_basis: float = 100.0, asset_class: str = "STOCK") -> Position:
    return Position(ticker=ticker, tier=tier, shares=shares,
                    avg_basis=avg_basis, asset_class=asset_class)


def md(ticker: str, *, price: float, high_52w: float | None = None,
       ret_30d: float = 0.0, ret_60d: float = 0.5, fwd_pe: float | None = None) -> MarketData:
    # ret_60d defaults high so trim tests pass the velocity gate unless overridden.
    return MarketData(ticker=ticker, price=price,
                      high_52w=high_52w if high_52w is not None else price,
                      ret_30d=ret_30d, ret_60d=ret_60d, fwd_pe=fwd_pe)


def make_snapshot(positions, market, *, reserve=1_000_000.0, is_dca_day=False,
                  macro=None, flags=None, avoid=frozenset(), band_state=None,
                  params: Params | None = None) -> Snapshot:
    return Snapshot(
        as_of="2026-07-24",
        is_dca_day=is_dca_day,
        positions=list(positions),
        market={m.ticker: m for m in market},
        band_state=band_state or {},
        reserve_balance=reserve,
        params=params or demo_params(),
        macro=macro or Macro(),
        flags=flags or Flags(emergency_fund_ok=True),
        avoid=avoid,
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
