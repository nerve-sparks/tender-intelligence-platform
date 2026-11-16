"""
GeM Portal Scraper using Playwright.

Uses sync_playwright in a thread executor to avoid Windows asyncio subprocess issues.
"""

from __future__ import annotations

import asyncio
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Browser, Page

from app.config import settings
from app.scraper.parser import parse_bid_cards
from app.utils.logger import get_logger

logger = get_logger(__name__)



class GemScraper:
    """Scraper for GeM bid listings using sync Playwright in a thread."""

    BASE_URL = "https://bidplus.gem.gov.in/all-bids"

    FULL_PAGE = 10  # bid cards per GeM listing page
    PAGE_LOAD_TIMEOUT = 60000
    ELEMENT_TIMEOUT = 30000

    def __init__(self) -> None:
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self._initialized = False
        # Sync Playwright objects may only be used from the thread that created them, so every
        # scraper gets its own single worker thread (fresh per scrape, shut down on close).
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scraper")

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    async def initialize(self) -> None:
        """Initialize the scraper in a background thread."""
        await self._run(self._sync_initialize)

    def _sync_initialize(self) -> None:
        """Synchronous initialization - runs in thread."""
        logger.info("scraper.initializing", headless=settings.SCRAPER_HEADLESS)
        self.playwright = sync_playwright().start()
        logger.info("scraper.playwright_started")
        self.browser = self.playwright.chromium.launch(
            headless=settings.SCRAPER_HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        logger.info("scraper.browser_launched")
        self.page = self.browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        logger.info("scraper.page_created")
        self._initialized = True

    async def close(self) -> None:
        """Close the scraper in a background thread."""
        try:
            if self._initialized or self.playwright is not None:
                await self._run(self._sync_close)
        finally:
            self._executor.shutdown(wait=False)

    def _sync_close(self) -> None:
        """Synchronous close - runs in thread."""
        for closer in (
            getattr(self.page, "close", None),
            getattr(self.browser, "close", None),
            getattr(self.playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception as exc:  # never let cleanup hide the real scrape error
                logger.info("scraper.close_failed", error=str(exc))
        self.page = self.browser = self.playwright = None
        self._initialized = False

    async def scrape_bids(
        self,
        max_pages: Optional[int] = None,
        max_bids: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape bids from GeM listing pages, optionally filtered by GeM's keyword search."""
        return await self._run(self._sync_scrape_bids, max_pages, max_bids, keyword)

    def _sync_scrape_bids(
        self,
        max_pages: Optional[int] = None,
        max_bids: Optional[int] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Synchronous scraping - runs in thread."""
        if self.page is None:
            raise RuntimeError("Scraper not initialized")

        max_pages = max_pages or settings.SCRAPE_MAX_PAGES
        max_bids = max_bids or settings.SCRAPE_MAX_BIDS

        logger.info("scraper.start", max_pages=max_pages, max_bids=max_bids, keyword=keyword)
        self.page.goto(self.BASE_URL, timeout=self.PAGE_LOAD_TIMEOUT, wait_until="networkidle")
        if keyword:
            self._sync_search(keyword)

        collected: List[Dict[str, Any]] = []
        seen: set = set()

        for page_index in range(max_pages):
            self._sync_random_delay()
            html = self.page.content()
            bids = parse_bid_cards(html)

            added = 0
            for bid in bids:
                bid_id = bid.get("bid_id")
                if not bid_id or bid_id in seen:
                    continue
                seen.add(bid_id)
                collected.append(bid)
                added += 1
                if len(collected) >= max_bids:
                    logger.info("scraper.max_bids_reached", count=len(collected))
                    return collected

            logger.info(
                "scraper.page_parsed",
                page=page_index + 1,
                bids=len(bids),
                total=len(collected),
            )

            # Results ran out: a page with nothing new (GeM repeats the last page) or a part-filled
            # page means there is no next page worth waiting for.
            if added == 0 or len(bids) < self.FULL_PAGE:
                break

            if not self._sync_try_next_page():
                if not self._sync_try_scroll():
                    break

        return collected

    def _sync_search(self, keyword: str) -> None:
        """Run GeM's own bid search (the #searchBid box -> POST /all-bids-data) - runs in thread."""
        box = self.page.query_selector("#searchBid")
        if box is None:
            raise RuntimeError("GeM search box (#searchBid) not found")
        box.fill(keyword)
        with self.page.expect_response(lambda r: "all-bids-data" in r.url, timeout=self.PAGE_LOAD_TIMEOUT):
            box.press("Enter")
        self.page.wait_for_load_state("networkidle", timeout=self.PAGE_LOAD_TIMEOUT)

    def _sync_try_next_page(self) -> bool:
        """Try to navigate to next page - runs in thread."""
        if self.page is None:
            return False
        try:
            next_button = self.page.query_selector("text=Next")
            if next_button:
                next_button.click()
                self.page.wait_for_load_state("networkidle", timeout=self.PAGE_LOAD_TIMEOUT)
                return True
        except Exception as exc:
            logger.info("scraper.next_failed", error=str(exc))
        return False

    def _sync_try_scroll(self) -> bool:
        """Try to scroll for more content - runs in thread."""
        if self.page is None:
            return False
        try:
            previous_height = self.page.evaluate("document.body.scrollHeight")
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            new_height = self.page.evaluate("document.body.scrollHeight")
            return new_height > previous_height
        except Exception as exc:
            logger.info("scraper.scroll_failed", error=str(exc))
            return False

    def _sync_random_delay(self) -> None:
        """Random delay between requests - runs in thread."""
        delay = random.uniform(settings.SCRAPE_MIN_DELAY, settings.SCRAPE_MAX_DELAY)
        time.sleep(delay)
