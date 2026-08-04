"""SQLAlchemy engine/session. Works with Postgres (prod) or SQLite (dev/tests)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tradr.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


# Additive columns added after a table already shipped. There is no Alembic here, and
# create_all() only creates missing TABLES — it will not add a column to one that exists,
# so an existing dev DB needs the ALTER. Keep entries here forever: they are idempotent
# (skipped when the column is present) and a fresh DB never runs them.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("lots", "fee", "FLOAT DEFAULT 0.0"),  # Rule 8 — per-fill transaction cost
    ("reserve_entries", "source_lot_id", "INTEGER"),  # lets a void reverse its movements
    ("market_snapshot", "prev_close", "FLOAT"),  # previous trading-day close for day-change
)


def _apply_added_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in tables:
                continue  # create_all just made it with the column already present
            if column in {c["name"] for c in inspector.get_columns(table)}:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _deduplicate_market_snapshots() -> None:
    """Keep only the most recent row per ticker — converts the old append-only snapshot
    table into a 1-row-per-ticker current-state cache. Idempotent."""
    from sqlalchemy import inspect, text

    if "market_snapshot" not in set(inspect(engine).get_table_names()):
        return
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM market_snapshot WHERE id NOT IN "
            "(SELECT MAX(id) FROM market_snapshot GROUP BY ticker)"
        ))


def init_db() -> None:
    from tradr.db import models  # noqa: F401 — register mappers

    Base.metadata.create_all(engine)
    _apply_added_columns()
    _deduplicate_market_snapshots()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
