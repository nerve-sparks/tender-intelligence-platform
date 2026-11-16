"""
Background job scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.jobs.scrape_job import run_process_job, run_scrape_job
from app.utils.logger import get_logger

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler(first_scrape_at: Optional[datetime] = None) -> None:
    """
    Scrape every SCRAPE_INTERVAL_HOURS so matches are ready before anyone opens the app.
    `first_scrape_at` lets the first run follow on from the last completed scrape instead of
    waiting a full interval after every restart.
    """
    if scheduler.running:
        return

    # Use async functions directly - APScheduler handles them properly
    scheduler.add_job(
        run_scrape_job,
        "interval",
        hours=settings.SCRAPE_INTERVAL_HOURS,
        id="scrape_job",
        replace_existing=True,
        next_run_time=first_scrape_at,
    )
    scheduler.add_job(
        run_process_job,
        "interval",
        minutes=settings.PROCESS_INTERVAL_MINUTES,
        id="process_job",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("scheduler.started")


async def next_scrape_time(db) -> datetime:
    """Last completed scrape + interval; if that is already past, a few minutes from now."""
    now = datetime.now(timezone.utc)
    last = await db.get_collection("scrape_logs").find_one(
        {"job_type": "scrape", "status": "completed"}, sort=[("completed_at", -1)]
    )
    soon = now + timedelta(minutes=3)
    finished = (last or {}).get("completed_at")
    if not isinstance(finished, datetime):
        return soon
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(finished + timedelta(hours=settings.SCRAPE_INTERVAL_HOURS), soon)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")
