"""
Fast scrape pipeline: parallel search + parallel analysis, matches judged while analysing,
general listing filtered to company keywords, quiet keywords searched one page deep.
"""

import asyncio

import pytest

from app.config import settings
from app.jobs import scrape_job
from app.services.search_keywords import listing_relevant, quiet_keywords


def test_listing_filter_keeps_only_relevant_cards():
    keywords = ["machine learning", "learning management system"]
    assert listing_relevant("Artificial intelligence, Machine Learning...", keywords)
    assert listing_relevant("Custom Bid for Services - Hiring of agency", keywords)
    assert listing_relevant(None, keywords)  # nothing to judge from -> analyse
    assert not listing_relevant("Office Chair, Steel Almirah", keywords)
    assert not listing_relevant("Toner Cartridge", keywords)


@pytest.mark.asyncio
async def test_quiet_keywords(fake_db):
    await fake_db.get_collection("company_profiles").insert_one({
        "company_id": "c", "status": {"processing_status": "ready"},
        "metadata": {"keyword_stats": [
            {"keyword": "Dead Term", "runs": 2, "found": 0, "matched_bids": []},
            {"keyword": "new term", "runs": 1, "found": 0, "matched_bids": []},
            {"keyword": "winner", "runs": 5, "found": 0, "matched_bids": ["b1"]},
        ]},
    })
    assert await quiet_keywords(fake_db) == {"dead term"}


class FakeScraper:
    calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def scrape_bids(self, max_pages=None, max_bids=None, keyword=None):
        FakeScraper.calls.append((keyword, max_pages))
        await asyncio.sleep(0.01)
        if keyword is None:  # general listing
            return [{"bid_id": "L1", "items": "Office Chair"}, {"bid_id": "L2", "items": "Machine Learning platform"},
                    {"bid_id": "K1", "items": "Machine learning service"}]  # K1 duplicates a keyword hit
        return [{"bid_id": f"K{i}", "items": f"{keyword} service"} for i in range(1, 6)]


class FakeService:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.processed = []

    async def create_or_update_from_scrape(self, bid):
        return None

    async def process_tender(self, bid_id):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        self.processed.append(bid_id)
        return True


@pytest.mark.asyncio
async def test_pipeline_parallel_and_judges_while_analysing(fake_db, monkeypatch):
    service = FakeService()
    refreshes = []

    async def fake_refresh(db):
        refreshes.append(len(service.processed))

    async def no_yield(*args, **kwargs):
        return None

    FakeScraper.calls = []
    monkeypatch.setattr(scrape_job, "GemScraper", FakeScraper)
    monkeypatch.setattr(scrape_job, "TenderService", lambda db: service)
    monkeypatch.setattr(scrape_job, "get_database", lambda: fake_db)
    monkeypatch.setattr(scrape_job, "_refresh_matches", fake_refresh)
    monkeypatch.setattr(scrape_job, "_record_keyword_yield", no_yield)
    monkeypatch.setattr(settings, "SCRAPE_PROCESS_CONCURRENCY", 3)
    monkeypatch.setattr(settings, "SCRAPE_SEARCH_WORKERS", 2)
    monkeypatch.setattr(settings, "MATCH_REFRESH_EVERY", 2)
    monkeypatch.setattr(settings, "SCRAPE_LISTING_FILTER", True)

    await scrape_job.run_scrape_job(keywords=["machine learning", "chatbot"])

    # 5 keyword bids (K1-K5, shared by both keywords) + L2 from the listing; L1 (chair) skipped, K1 not twice
    assert sorted(service.processed) == ["K1", "K2", "K3", "K4", "K5", "L2"]
    assert service.max_active > 1  # analysed in parallel
    assert len(refreshes) >= 2  # judged during analysis, plus the final pass
    log = await fake_db.get_collection("scrape_logs").find_one({})
    assert log["status"] == "completed"
    assert log["stats"]["listing_skipped"] == 1
    assert log["stats"]["llm_processed"] == 6
    assert not scrape_job.is_scrape_running()
