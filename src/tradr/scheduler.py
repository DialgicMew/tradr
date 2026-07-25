"""Scheduler — runs the evaluation pipeline a few times during US market hours.

Cron times cover both DST states; the runner's trading-day gate makes off-hours
firings harmless no-ops. Start at 2–3×/day; tune later (dynamic cadence is a v2 idea).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from tradr.config import get_settings
from tradr.db import init_db
from tradr.runner import run_once

log = logging.getLogger("tradr.scheduler")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_db()
    sched = BlockingScheduler(timezone="UTC")
    # ~mid-session, late-session across EST/EDT (13:30–21:00 UTC window).
    for hour in (14, 17, 20):
        sched.add_job(run_once, CronTrigger(day_of_week="mon-fri", hour=hour, minute=0),
                      id=f"eval-{hour}", misfire_grace_time=3600)
    log.info("scheduler started (UTC 14:00, 17:00, 20:00 Mon–Fri)")
    sched.start()


if __name__ == "__main__":
    main()
