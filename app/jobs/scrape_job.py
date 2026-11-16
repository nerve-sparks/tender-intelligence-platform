"""
Scrape and process jobs for GeM portal.

The scrape is a pipeline so matching tenders show up within minutes:
  search GeM with every company keyword (several browsers in parallel)
     -> each kept bid goes straight into a queue
     -> several bids are analysed at the same time while searching continues
     -> matches are judged in the background every few analysed bids
  then the general newest-first listing (only cards relevant to some company's keywords).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from app.config import settings
from app.database.mongodb import get_database
from app.scraper.gem_scraper import GemScraper
from app.services.tender_service import TenderService
from app.utils.logger import get_logger

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Only one scrape at a time: parallel runs would open several browsers and send the same
# bids to the LLM several times.
_scrape_running = False


def is_scrape_running() -> bool:
    return _scrape_running


def _dedupe(bids: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for bid in bids:
        bid_id = bid.get("bid_id")
        if bid_id and bid_id not in seen:
            seen.add(bid_id)
            unique.append(bid)
    return unique


class _Pipeline:
    """Analyses queued bids with bounded concurrency and judges matches in the background as it goes."""

    def __init__(self, db, service: TenderService, job_id: str, stats: Dict[str, Any], errors: List[Dict[str, Any]]):
        self.db = db
        self.service = service
        self.job_id = job_id
        self.stats = stats
        self.errors = errors
        self.queue: asyncio.Queue = asyncio.Queue()
        self.seen: Set[str] = set()
        self.queued = 0
        self.done = 0
        self._since_refresh = 0
        self._refresh_task: Optional[asyncio.Task] = None
        self._workers: List[asyncio.Task] = []

    def start(self) -> None:
        workers = max(1, settings.SCRAPE_PROCESS_CONCURRENCY)
        self._workers = [asyncio.create_task(self._worker()) for _ in range(workers)]

    def add(self, bid: Dict[str, Any]) -> bool:
        bid_id = bid.get("bid_id")
        if not bid_id or bid_id in self.seen:
            return False
        self.seen.add(bid_id)
        self.queued += 1
        self.stats["tenders_found"] = self.queued
        self.queue.put_nowait(bid)
        return True

    async def finish(self) -> None:
        """Wait until every queued bid is analysed and any background judging has finished."""
        for _ in self._workers:
            self.queue.put_nowait(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        if self._refresh_task:
            await asyncio.gather(self._refresh_task, return_exceptions=True)

    async def cancel(self) -> None:
        for task in self._workers + ([self._refresh_task] if self._refresh_task else []):
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _worker(self) -> None:
        while True:
            bid = await self.queue.get()
            if bid is None:
                return
            await self._process(bid)

    async def _process(self, bid: Dict[str, Any]) -> None:
        try:
            await self.service.create_or_update_from_scrape(bid)
            self.stats["new_tenders"] += 1
            if await self.service.process_tender(bid.get("bid_id")):
                self.stats["pdfs_downloaded"] += 1
                self.stats["llm_processed"] += 1
        except Exception as exc:
            self.stats["errors"] += 1
            self.errors.append({"bid_id": bid.get("bid_id"), "error": str(exc), "timestamp": utcnow()})
        self.done += 1
        self._since_refresh += 1
        if self._since_refresh >= max(1, settings.MATCH_REFRESH_EVERY):
            self._kick_refresh()
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=self.job_id,
            status="progress",
            current=self.done,
            total=max(self.queued, 1),
            message=f"Analysed {self.done}/{self.queued} tenders",
        )

    def _kick_refresh(self) -> None:
        """Judge the newly analysed tenders now, without holding up analysis (one judging run at a time)."""
        if self._refresh_task and not self._refresh_task.done():
            return
        self._since_refresh = 0
        self._refresh_task = asyncio.create_task(_refresh_matches(self.db))


async def _search_keywords(keywords: List[str], quiet: Set[str], pipeline: _Pipeline) -> Dict[str, List[str]]:
    """
    GeM keyword search with several browsers in parallel (each scraper owns its own browser thread).
    Kept hits go straight into the analysis queue. Returns {keyword: kept bid ids}.
    """
    from app.services.matching_utils import normalize_text
    from app.services.search_keywords import keyword_matches_listing

    hits: Dict[str, List[str]] = {}
    workers = max(1, min(settings.SCRAPE_SEARCH_WORKERS, len(keywords)))

    async def search(share: List[str]) -> None:
        try:
            async with GemScraper() as scraper:
                for keyword in share:
                    pages = 1 if normalize_text(keyword) in quiet else settings.SCRAPE_KEYWORD_PAGES
                    try:
                        bids = await scraper.scrape_bids(
                            max_pages=pages, max_bids=settings.SCRAPE_KEYWORD_MAX_BIDS, keyword=keyword
                        )
                    except Exception as exc:
                        logger.info("scrape.keyword_failed", keyword=keyword, error=str(exc))
                        continue
                    kept = [bid for bid in bids if keyword_matches_listing(keyword, bid.get("items"))]
                    hits[keyword] = [bid["bid_id"] for bid in kept if bid.get("bid_id")]
                    new = sum(1 for bid in kept if pipeline.add(bid))
                    logger.info("scrape.keyword_done", keyword=keyword, found=len(bids), kept=len(kept),
                                new=new, pages=pages)
        except Exception as exc:
            logger.info("scrape.search_worker_failed", error=str(exc))

    await asyncio.gather(*(search(keywords[i::workers]) for i in range(workers)))
    return hits


async def run_scrape_job(
    keywords: Optional[List[str]] = None,
    use_keywords: bool = True,
    max_pages: Optional[int] = None,
    max_bids: Optional[int] = None,
) -> None:
    """
    Scrape GeM and analyse new bids.

    1. GeM keyword searches built from every ready company profile (relevant bids are a tiny
       share of the full listing, so they are searched for directly) - analysed while searching.
    2. The general newest-first listing (filtered to cards relevant to some company's keywords).
    `keywords` overrides the profile keywords; `use_keywords=False` skips step 1.
    """
    global _scrape_running
    if _scrape_running:
        logger.info("scrape.already_running")
        return
    _scrape_running = True
    try:
        await _run_scrape_job(keywords, use_keywords, max_pages, max_bids)
    finally:
        _scrape_running = False


async def _run_scrape_job(
    keywords: Optional[List[str]],
    use_keywords: bool,
    max_pages: Optional[int],
    max_bids: Optional[int],
) -> None:
    from app.services.search_keywords import collect_search_keywords, listing_relevant, quiet_keywords

    db = get_database()
    service = TenderService(db)
    job_id = str(uuid.uuid4())
    scrape_logs = db.get_collection("scrape_logs")
    started = utcnow()

    log_entry = {
        "job_id": job_id,
        "job_type": "scrape",
        "started_at": started,
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
        job="SCRAPE_TENDERS", job_id=job_id, status="started", current=0, total=1, message="Scrape job started",
    )

    stats = log_entry["stats"]
    errors: List[Dict[str, Any]] = []
    pipeline = _Pipeline(db, service, job_id, stats, errors)
    keyword_hits: Dict[str, List[str]] = {}

    try:
        pipeline.start()

        # 1. Keyword searches - hits are analysed while the remaining keywords are still being searched.
        if use_keywords:
            if keywords is None:
                keywords = await collect_search_keywords(db, limit=settings.SCRAPE_MAX_KEYWORDS)
            quiet = await quiet_keywords(db)
            logger.info("scrape.keywords", count=len(keywords), quiet=len(quiet), keywords=keywords)
            keyword_hits = await _search_keywords(keywords, quiet, pipeline)
            stats["keyword_tenders"] = pipeline.queued
            # Per-keyword results show right away; "matched" is updated again after judging.
            await _record_keyword_yield(db, keyword_hits, new_run=True)
        keywords = keywords or []

        # 2. General newest-first listing, only cards relevant to some company's keywords.
        listing: List[Dict[str, Any]] = []
        try:
            async with GemScraper() as scraper:
                listing = await scraper.scrape_bids(max_pages=max_pages, max_bids=max_bids)
        except Exception as exc:  # keyword results are still analysed if the listing fails
            logger.info("scrape.listing_failed", error=str(exc))
            errors.append({"error": f"General listing failed: {exc}", "timestamp": utcnow()})
        skipped = 0
        for bid in _dedupe(listing):
            if settings.SCRAPE_LISTING_FILTER and keywords and not listing_relevant(bid.get("items"), keywords):
                skipped += 1
                continue
            pipeline.add(bid)
        stats["listing_skipped"] = skipped
        logger.info("scrape.listing_done", found=len(listing), skipped=skipped, queued=pipeline.queued)

        # 3. Finish analysis, then a final judging pass and keyword-yield update.
        await pipeline.finish()
        await _refresh_matches(db)
        await _record_keyword_yield(db, keyword_hits, new_run=False)

        stats["duration_seconds"] = round((utcnow() - started).total_seconds())
        await scrape_logs.update_one(
            {"job_id": job_id},
            {"$set": {"completed_at": utcnow(), "status": "completed", "stats": stats, "errors": errors}},
        )
        await _broadcast_progress(
            job="PROCESS_TENDERS",
            job_id=job_id,
            status="completed",
            current=stats["llm_processed"],
            total=max(stats["tenders_found"], 1),
            message="Scrape and analysis completed",
        )
        logger.info("scrape.job_completed", **{k: v for k, v in stats.items() if isinstance(v, int)})
    except Exception as exc:
        import traceback

        await pipeline.cancel()
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
    if _scrape_running:
        # The scrape analyses its own bids; running both would send the same bids to the LLM twice.
        logger.info("process.skipped_scrape_running")
        return
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


async def _record_keyword_yield(db, keyword_hits: Dict[str, List[str]], new_run: bool) -> None:
    """Remember what each keyword found and which hits matched, so the best keywords are searched first."""
    try:
        from app.services.search_keywords import record_keyword_yield

        await record_keyword_yield(db, keyword_hits, new_run=new_run)
    except Exception as exc:
        logger.info("scrape.keyword_yield_failed", error=str(exc))


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
