"""ORM tables (ARCHITECTURE.md §2). All personal values live here, entered via UI/bot."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from tradr.db.base import Base


class UniverseRow(Base):
    """A watched ticker. tier ∈ {T1,T2,ETF}; is_avoid overrides all buys (Rule 1)."""

    __tablename__ = "universe"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    tier: Mapped[str] = mapped_column(String, default="T1")
    asset_class: Mapped[str] = mapped_column(String, default="STOCK")  # STOCK | ETF
    is_avoid: Mapped[bool] = mapped_column(default=False)
    subscribed: Mapped[bool] = mapped_column(default=True)   # actively watched
    notify: Mapped[bool] = mapped_column(default=True)


class Lot(Base):
    """Append-only fill. Positions & avg basis are DERIVED from these (never hand-set)."""

    __tablename__ = "lots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    side: Mapped[str] = mapped_column(String)   # buy | sell
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    source_signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voided: Mapped[bool] = mapped_column(default=False)  # "remove" = compensating flag
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReserveEntry(Base):
    """Append-only notional dry-powder ledger (ruleset §3). Balance = sum(amount)."""

    __tablename__ = "reserve_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    kind: Mapped[str] = mapped_column(String)   # external | base_bank | trim_recycle | dd_draw | manual
    amount: Mapped[float] = mapped_column(Float)  # signed: + funds, − draws
    note: Mapped[str] = mapped_column(String, default="")


class SignalRow(Base):
    """A signal with its lifecycle state (ruleset §2)."""

    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    rule: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    cause: Mapped[str] = mapped_column(String)
    size_shares: Mapped[float] = mapped_column(Float, default=0.0)
    size_dollars: Mapped[float] = mapped_column(Float, default=0.0)
    band: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # numbers for human-readable UI
    state: Mapped[str] = mapped_column(String, default="PENDING")  # PENDING|DONE|SKIPPED|EXPIRED
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    snooze_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    fill_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)


class EngineStateRow(Base):
    """Per-ticker band state as a JSON blob mapping to engine.models.BandState."""

    __tablename__ = "engine_state"
    ticker: Mapped[str] = mapped_column(String, primary_key=True)
    dd_status: Mapped[dict] = mapped_column(JSON, default=dict)     # {band(str): status}
    trim_status: Mapped[dict] = mapped_column(JSON, default=dict)
    dd_deployed_mult: Mapped[float] = mapped_column(Float, default=0.0)
    spike_peak: Mapped[float | None] = mapped_column(Float, nullable=True)


class ParamRow(Base):
    """Engine parameters (ruleset §1). value is JSON to hold scalars or dicts."""

    __tablename__ = "params"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="DEFAULT")  # LOCKED | DEFAULT | USER


class FlagRow(Base):
    __tablename__ = "flags"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class MacroRow(Base):
    """Manually-entered slow-moving macro (S&P fwd P/E + 10yr avg). Latest row wins."""

    __tablename__ = "macro"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sp_fwd_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    sp_10yr_avg_fwd_pe: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketSnapshotRow(Base):
    """Cache/audit of fetched market data per run (adapter output)."""

    __tablename__ = "market_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[date] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Float)
    high_52w: Mapped[float] = mapped_column(Float)
    ret_30d: Mapped[float] = mapped_column(Float)
    ret_60d: Mapped[float] = mapped_column(Float)
    fwd_pe: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    kind: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
