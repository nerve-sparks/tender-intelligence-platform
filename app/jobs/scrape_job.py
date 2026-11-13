"""
Scrape and process jobs for GeM portal.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database.mongodb import get_database
from app.scraper.gem_scraper import GemScraper
from app.services.tender_service import TenderService
from app.utils.logger import get_logger

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _scrape_keyword_bids(scraper: GemScraper, db, keywords: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Run GeM's keyword search for each company-profile keyword; keep hits whose card text really matches."""
    from app.services.search_keywords import collect_search_keywords, keyword_matches_listing

    if keywords is None:
        keywords = await collect_search_keywords(db, limit=settings.SCRAPE_MAX_KEYWORDS)
    found: List[Dict[str, Any]] = []
    for keyword in keywords:
        try:
            bids = await scraper.scrape_bids(
                max_pages=settings.SCRAPE_KEYWORD_PAGES,
                max_bids=settings.SCRAPE_KEYWORD_MAX_BIDS,
                keyword=keyword,
            )
        except Exception as exc:
            logger.info("scrape.keyword_failed", keyword=keyword, error=str(exc))
            continue
        kept = [bid for bid in bids if keyword_matches_listing(keyword, bid.get("items"))]
        logger.info("scrape.keyword_done", keyword=keyword, found=len(bids), kept=len(kept))
        found.extend(kept)
    return found


def _dedupe(bids: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for bid in bids:
        bid_id = bid.get("bid_id")
        if bid_id and bid_id not in seen:
            seen.add(bid_id)
            unique.append(bid)
    return unique


async def run_scrape_job(
    keywords: Optional[List[str]] = None,
    use_keywords: bool = True,
    max_pages: Optional[int] = None,
    max_bids: Optional[int] = None,
) -> None:
    """
    Scrape GeM and analyse new bids.

    1. GeM keyword searches built from every ready company profile (relevant bids are a tiny
       share of the full listing, so they are searched for directly) - analysed and matched first.
    2. The general newest-first listing.
    `keywords` overrides the profile keywords; `use_keywords=False` skips step 1.
    """
    db = get_database()
    service = TenderService(db)
    job_id = str(uuid.uuid4())
    scrape_logs = db.get_collection("scrape_logs")

    log_entry = {
        "job_id": job_id,
        "job_type": "scrape",
        "started_at": utcnow(),
        "status": "running",
        "stats": {
            "pages_scraped": 0,
            "tenders_found": 0,
            "new_tenders": 0,
            "pdfs_downloaded": 0,
            "llm_processed": 0,
            "errors": 0,
        },
        "errors": [],
    }
    await scrape_logs.insert_one(log_entry)
    await _broadcast_progress(
        job="SCRAPE_TENDERS",
        job_id=job_id,
        status="started",
        current=0,
        total=1,
        message="Scrape job started",
    )

    stats = log_entry["stats"]
    errors: List[Dict[str, Any]] = []

    try:
        async with GemScraper() as scraper:
            keyword_bids: List[Dict[str, Any]] = []
            if use_keywords:
                keyword_bids = _dedupe(await _scrape_keyword_bids(scraper, db, keywords))
            listing_bids = await scraper.scrape_bids(max_pages=max_pages, max_bids=max_bids)

        keyword_ids = {bid["bid_id"] for bid in keyword_bids}
        listing_bids = [bid for bid in _dedupe(listing_bids) if bid.get("bid_id") not in keyword_ids]
        bids = keyword_bids + listing_bids
        stats["tenders_found"] = len(bids)
        stats["keyword_tenders"] = len(keyword_bids)

        total_bids = max(len(bids), 1)
        for index, bid in enumerate(bids, start=1):
            try:
                await service.create_or_update_from_scrape(bid)
                stats["new_tenders"] += 1
                if await service.process_tender(bid.get("bid_id")):
                    stats["pdfs_downloaded"] += 1
                    stats["llm_processed"] += 1
            except Exception as exc:
                stats["errors"] += 1
                errors.append(
                    {
                        "bid_id": bid.get("bid_id"),
                        "error": str(exc),
                        "timestamp": utcnow(),
                    }
                )

            await _broadcast_progress(
                job="PROCESS_TENDERS",
                job_id=job_id,
                status="progress",
                current=index,
                total=total_bids,
                message=f"Processed {index}/{total_bids} tenders",
            )
            if keyword_bids and index == len(keyword_bids):
                # Keyword hits are the likely matches - surface them before the long general listing.
                await _refresh_matches(db)

        await scrape_logs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "completed_at": utcnow(),
                    "status": "completed",
                    "stats": stats,
                    "errors": errors,
                }
            },
        )
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=job_id,
            status="completed",
            current=stats["llm_processed"],
            total=max(stats["tenders_found"], 1),
            message="Scrape and analysis completed",
        )
        await _refresh_matches(db)
    except Exception as exc:
        import traceback

        error_msg = str(exc) or repr(exc) or type(exc).__name__
        error_detail = {
            "error": error_msg,
            "type": type(exc).__name__,
            "traceback": traceback.format_exc(),
            "timestamp": utcnow(),
        }
        await scrape_logs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "completed_at": utcnow(),
                    "status": "failed",
                    "stats": stats,
                    "errors": errors + [error_detail],
                }
            },
        )
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=job_id,
            status="failed",
            current=0,
            total=max(stats["tenders_found"], 1),
            message=error_msg,
        )
        logger.error("scrape.job_failed", error=error_msg, error_type=type(exc).__name__, exc_info=True)


def run_scrape_job_sync() -> None:
    asyncio.run(run_scrape_job())


async def run_process_job() -> None:
    db = get_database()
    service = TenderService(db)
    job_id = str(uuid.uuid4())
    scrape_logs = db.get_collection("scrape_logs")
    log_entry = {
        "job_id": job_id,
        "job_type": "process",
        "started_at": utcnow(),
        "status": "running",
        "stats": {"processed": 0, "errors": 0},
        "errors": [],
    }
    await scrape_logs.insert_one(log_entry)
    await _broadcast_progress(
        job="PROCESS_TENDERS",
        job_id=job_id,
        status="started",
        current=0,
        total=settings.PROCESS_BATCH_LIMIT,
        message="Process job started",
    )

    try:
        processed = await service.process_pending_tenders(limit=settings.PROCESS_BATCH_LIMIT)
        stats = {"processed": processed, "errors": 0}
        await scrape_logs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "completed_at": utcnow(),
                    "status": "completed",
                    "stats": stats,
                    "errors": [],
                }
            },
        )
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=job_id,
            status="completed",
            current=processed,
            total=max(processed, 1),
            message="Pending tender processing completed",
        )
        logger.info("process.job_completed", processed=processed)
        if processed:
            await _refresh_matches(db)
    except Exception as exc:
        await scrape_logs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "completed_at": utcnow(),
                    "status": "failed",
                    "stats": {"processed": 0, "errors": 1},
                    "errors": [{"error": str(exc), "timestamp": utcnow()}],
                }
            },
        )
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=job_id,
            status="failed",
            current=0,
            total=settings.PROCESS_BATCH_LIMIT,
            message=str(exc),
        )
        logger.info("process.job_failed", error=str(exc))


def run_process_job_sync() -> None:
    asyncio.run(run_process_job())


async def _refresh_matches(db) -> None:
    """Judge newly processed tenders against every ready company profile."""
    if not settings.MATCH_AUTO_REFRESH:
        return
    try:
        from app.services.relevance_service import RelevanceService

        await RelevanceService(db).refresh_all_companies()
    except Exception as exc:
        logger.info("relevance.refresh_after_job_failed", error=str(exc))


async def _broadcast_progress(
    job: str,
    job_id: str,
    status: str,
    current: int,
    total: int,
    message: str,
) -> None:
    try:
        from app.services.socket_manager import manager

        payload = {
            "type": "JOB_PROGRESS",
            "job": job,
            "job_id": job_id,
            "status": status,
            "progress": {
                "current": current,
                "total": total,
                "percent": round((current / total), 4) if total else 0,
            },
            "message": message,
            "ts": utcnow().isoformat(),
        }
        await manager.broadcast(payload)
    except Exception:
        return
