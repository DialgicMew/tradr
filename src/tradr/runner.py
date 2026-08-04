"""Evaluation Runner (ARCHITECTURE §2 / ruleset §4).

Orchestrates one pipeline run: build snapshot → engine → persist signals → notify.
Market-calendar aware so it no-ops when the US market is closed. Callable from the
scheduler or manually (API "run now").
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from tradr.config import get_settings
from tradr.db import session_scope
from tradr.market import get_provider
from tradr.services import config_service as cfg
from tradr.services import signals as sig_svc

log = logging.getLogger("tradr.runner")


def is_trading_day(d: date) -> bool:
    try:
        import pandas_market_calendars as mcal

        sched = mcal.get_calendar("XNYS").schedule(start_date=d, end_date=d)
        return not sched.empty
    except Exception:
        return d.weekday() < 5  # fallback: Mon–Fri (ignores holidays)


def run_once(as_of: date | None = None, *, force: bool = False, notify: bool = True) -> dict:
    settings = get_settings()
    as_of = as_of or date.today()
    if not force and not is_trading_day(as_of):
        log.info("market closed on %s — skipping run", as_of)
        return {"ran": False, "reason": "market closed", "new_signals": 0}

    provider = get_provider(settings.market_data_provider)

    with session_scope() as s:
        if not cfg.is_configured(s):
            log.warning("engine not configured — skipping run")
            return {"ran": False, "reason": "not configured", "new_signals": 0}
        new_rows = sig_svc.run_and_persist(s, provider, as_of=as_of)
        payloads = [_to_payload(r) for r in new_rows]

    if notify and payloads:
        _notify(payloads)
    return {"ran": True, "new_signals": len(payloads), "signals": payloads}


def _to_payload(row) -> dict:
    return {
        "id": row.id, "ticker": row.ticker, "rule": row.rule, "action": row.action,
        "cause": row.cause, "size_shares": row.size_shares, "size_dollars": row.size_dollars,
        "band": row.band, "warnings": row.warnings, "notes": row.notes,
    }


def _notify(payloads: list[dict]) -> None:
    try:
        from tradr.bot import send_signals  # lazy import to avoid hard dep in tests

        send_signals(payloads)
    except Exception:  # notification must never crash a run
        log.exception("notification failed at %s", datetime.now())


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level)
    print(run_once())
