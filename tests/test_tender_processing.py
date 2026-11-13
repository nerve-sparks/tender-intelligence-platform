"""
Regression tests for tender analysis (download -> extract -> LLM -> embed).
"""

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from app.processors.llm_extractor import TENDER_EXTRACTION_VERSION
from app.services import tender_service as tender_service_module
from app.services.tender_service import TenderService


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def extract_tender(self, text):
        self.calls += 1
        return {"title": "AI platform", "procurement_type": "software", "summary": text[:40]}


class PathCheckingExtractor:
    """Returns text only for a real path - fitz.open(None) silently yields an empty PDF."""

    def __init__(self):
        self.paths = []

    def extract_text(self, path):
        self.paths.append(path)
        return "Development of an AI analytics platform" if path else None


def _service(fake_db, monkeypatch, tmp_path):
    pdf = tmp_path / "bid.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    async def fake_download(url, filename_hint=None):
        return str(pdf)

    monkeypatch.setattr(tender_service_module, "download_with_retry", fake_download)
    service = TenderService(fake_db)
    service.pdf_extractor = PathCheckingExtractor()
    service._llm = FakeLLM()
    monkeypatch.setattr(service.embedder, "embed", lambda _text: [0.1, 0.2])
    return service, str(pdf)


async def _insert_new_tender(fake_db):
    await fake_db.get_collection("tenders").insert_one(
        {
            "_id": ObjectId(),
            "bid_id": "GEM/2026/B/1",
            "pdf_url": "https://bidplus.gem.gov.in/showbidDocument/1",
            "status": {"pdf_downloaded": False, "llm_processed": False, "last_error": None},
            "created_at": datetime.now(timezone.utc),
        }
    )


@pytest.mark.asyncio
async def test_first_pass_uses_freshly_downloaded_path(fake_db, monkeypatch, tmp_path):
    service, pdf_path = _service(fake_db, monkeypatch, tmp_path)
    await _insert_new_tender(fake_db)

    assert await service.process_tender("GEM/2026/B/1") is True
    assert service.pdf_extractor.paths == [pdf_path]  # not None

    saved = await fake_db.get_collection("tenders").find_one({"bid_id": "GEM/2026/B/1"})
    assert saved["status"]["llm_processed"] is True
    assert saved["status"]["last_error"] is None
    assert saved["metadata_version"] == TENDER_EXTRACTION_VERSION
    assert saved["metadata"]["procurement_type"] == "software"


@pytest.mark.asyncio
async def test_already_processed_tender_is_not_sent_to_llm_again(fake_db, monkeypatch, tmp_path):
    service, _ = _service(fake_db, monkeypatch, tmp_path)
    await _insert_new_tender(fake_db)
    await service.process_tender("GEM/2026/B/1")
    await service.process_tender("GEM/2026/B/1")
    assert service._llm.calls == 1


@pytest.mark.asyncio
async def test_rescrape_keeps_processing_status(fake_db, monkeypatch, tmp_path):
    service, _ = _service(fake_db, monkeypatch, tmp_path)
    await _insert_new_tender(fake_db)
    await service.process_tender("GEM/2026/B/1")

    await service.create_or_update_from_scrape({"bid_id": "GEM/2026/B/1", "items": "AI platform"})
    saved = await fake_db.get_collection("tenders").find_one({"bid_id": "GEM/2026/B/1"})
    assert saved["status"]["llm_processed"] is True
