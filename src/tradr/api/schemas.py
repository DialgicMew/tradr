"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class SetupIn(BaseModel):
    base_unit: float = Field(gt=0)
    tier_weight: dict[str, float]           # e.g. {"T1": 2.0, "T2": 1.0, "ETF": 18.0}
    emergency_fund_ok: bool = False


class ParamOut(BaseModel):
    key: str
    value: object
    status: str


class ParamIn(BaseModel):
    value: object


class FlagIn(BaseModel):
    value: object


class MacroIn(BaseModel):
    sp_fwd_pe: float | None = None
    sp_10yr_avg_fwd_pe: float | None = None


class UniverseIn(BaseModel):
    ticker: str
    tier: str = "T1"
    asset_class: str = "STOCK"
    is_avoid: bool = False
    subscribed: bool = True
    notify: bool = True


class TradeIn(BaseModel):
    ticker: str
    side: str                                # buy | sell
    qty: float = Field(gt=0)
    price: float = Field(gt=0)
    trade_date: date | None = None


class ReserveIn(BaseModel):
    amount: float                            # signed: + top-up, − withdrawal
    note: str = ""
    kind: str = "external"


class ConfirmIn(BaseModel):
    fill_qty: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    trade_date: date | None = None


class HoldingOut(BaseModel):
    ticker: str
    tier: str
    asset_class: str
    shares: float
    avg_basis: float
    price: float | None
    value: float | None
    gain_pct: float | None
    weight: float | None
