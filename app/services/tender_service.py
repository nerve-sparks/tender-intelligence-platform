"""
Tender processing service.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database.repositories.tender_repo import TenderRepository
from app.processors.embedder import TextEmbedder
from app.processors.llm_extractor import TENDER_EXTRACTION_VERSION, LLMExtractor
from app.processors.gem_bid_fields import parse_gem_bid_fields
from app.processors.pdf_extractor import PDFExtractor
from app.scraper.pdf_downloader import download_with_retry
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TenderService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.repo = TenderRepository(db)
        self.pdf_extractor = PDFExtractor()
        self.embedder = TextEmbedder()
        self._llm: Optional[LLMExtractor] = None

    def _get_llm(self) -> LLMExtractor:
        if self._llm is None:
            self._llm = LLMExtractor()
        return self._llm

    async def create_or_update_from_scrape(self, bid: Dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        scraped_info = {
            "items": bid.get("items"),
            "quantity": bid.get("quantity"),
            "department": bid.get("department"),
            "department_address": bid.get("department_address"),
            "start_date": bid.get("start_date"),
            "end_date": bid.get("end_date"),
            "bid_type": bid.get("bid_type"),
            "bid_value_range": bid.get("bid_value_range"),
        }
        status = {
            "scrape_status": "completed",
            "pdf_downloaded": False,
            "llm_processed": False,
            "embedding_generated": False,
            "last_error": None,
        }
        data = {
            "bid_id": bid.get("bid_id"),
            "ra_no": bid.get("ra_no"),
            "gem_url": bid.get("gem_url"),
            "pdf_url": bid.get("pdf_url"),
            "portal": "gem",
            "scraped_info": scraped_info,
            "scraped_at": now,
            "updated_at": now,
            "is_active": True,
            "expired": False,
        }
        # Only new tenders get a fresh processing status; re-scraping an existing bid must
        # not wipe its download / LLM progress (that forced needless reprocessing).
        existing = await self.repo.get_by_bid_id(bid.get("bid_id"))
        if not existing or not existing.get("status"):
            data["status"] = status
        return await self.repo.upsert_by_bid_id(bid.get("bid_id"), data)

    async def process_tender(self, bid_id: str) -> bool:
        tender = await self.repo.get_by_bid_id(bid_id)
        if not tender:
            return False

        status = tender.get("status", {})
        if status.get("llm_processed") and tender.get("metadata_version") == TENDER_EXTRACTION_VERSION:
            return True  # already analysed with the current prompt; reprocess_tender() forces a redo
        try:
            pdf_path = tender.get("pdf_local_path")
            # (Re)download when not downloaded yet or when the stored file is missing.
            if not status.get("pdf_downloaded") or not pdf_path or not os.path.exists(pdf_path):
                pdf_path = await download_with_retry(tender.get("pdf_url"), filename_hint=bid_id)
                if not pdf_path:
                    status.update({"pdf_downloaded": False, "last_error": "PDF download failed"})
                    await self.repo.set_status(bid_id, status)
                    return False
                await self.repo.update(str(tender["_id"]), {"pdf_local_path": pdf_path})
                status["pdf_downloaded"] = True

            # Use the path just downloaded; the `tender` dict loaded above is stale.
            text = self.pdf_extractor.extract_text(pdf_path)
            if not text:
                status.update({"last_error": "PDF extraction failed"})
                await self.repo.set_status(bid_id, status)
                return False

            metadata = self._get_llm().extract_tender(text)
            if isinstance(metadata, dict):
                # Standard GeM fields read deterministically (eligibility depends on them).
                metadata.update({k: v for k, v in parse_gem_bid_fields(text).items() if v})
            status["llm_processed"] = True
            status["last_error"] = None

            summary = metadata.get("summary") if isinstance(metadata, dict) else None
            summary_embedding = self.embedder.embed(summary or "")
            status["embedding_generated"] = True

            await self.repo.update(
                str(tender["_id"]),
                {
                    "metadata": metadata,
                    "metadata_version": TENDER_EXTRACTION_VERSION,
                    "summary_embedding": summary_embedding,
                    "processed_at": datetime.now(timezone.utc),
                    "status": status,
                },
            )
            return True
        except Exception as exc:
            status.update({"last_error": str(exc)})
            await self.repo.set_status(bid_id, status)
            logger.info("tender.process_failed", bid_id=bid_id, error=str(exc))
            return False

    async def list_tenders(
        self,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        domains: Optional[List[str]] = None,
        search: Optional[str] = None,
        expired: Optional[bool] = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
    ) -> Dict[str, Any]:
        await self._update_expired_flags()
        filters = self._build_filters(status=status, domains=domains, search=search, expired=expired)
        sort_field = self._sort_field(sort_by)
        sort_order = -1 if sort_dir.lower() == "desc" else 1

        total = await self.repo.collection.count_documents(filters)
        cursor = (
            self.repo.collection.find(filters)
            .sort(sort_field, sort_order)
            .skip(skip)
            .limit(limit)
        )
        items = []
        async for tender in cursor:
            items.append(self._serialize(tender))
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    async def get_tender(self, tender_id: str) -> Optional[Dict[str, Any]]:
        tender = await self.repo.get_by_id(tender_id)
        return self._serialize(tender) if tender else None

    async def get_download(self, tender_id: str) -> Optional[Dict[str, str]]:
        """
        The original GeM bid document for a tender: {"path", "filename"}.
        If it was never downloaded (or the file is gone) it is fetched from GeM now.
        """
        tender = await self.repo.get_by_id(tender_id)
        if not tender:
            return None
        path = tender.get("pdf_local_path")
        if not path or not os.path.exists(path):
            path = await download_with_retry(tender.get("pdf_url"), filename_hint=tender.get("bid_id"))
            if not path:
                return None
            status = tender.get("status") or {}
            status["pdf_downloaded"] = True
            await self.repo.update(str(tender["_id"]), {"pdf_local_path": path, "status": status})
        bid_id = tender.get("bid_id") or tender_id
        filename = f"{bid_id.replace('/', '_')}.pdf"  # e.g. GEM_2026_B_8195819.pdf
        return {"path": path, "filename": filename}

    async def get_download_path(self, tender_id: str) -> Optional[str]:
        result = await self.get_download(tender_id)
        return result["path"] if result else None

    async def get_stats(self) -> Dict[str, Any]:
        collection = self.repo.collection
        total = await collection.count_documents({})
        processed = await collection.count_documents({"status.llm_processed": True})
        pending = await collection.count_documents({"status.llm_processed": False})
        failed = await collection.count_documents({"status.last_error": {"$ne": None}})
        return {"total": total, "processed": processed, "pending": pending, "failed": failed}

    def _serialize(self, tender: Dict[str, Any]) -> Dict[str, Any]:
        if not tender:
            return tender
        tender_id = tender.get("_id")
        if tender_id is not None:
            tender["id"] = str(tender_id)
            tender["_id"] = str(tender_id)
        return tender

    async def process_pending_tenders(self, limit: int = 50) -> int:
        processed = 0
        now = datetime.now(timezone.utc)
        # Only bids that are still open are worth an LLM call.
        cursor = self.repo.collection.find(
            {
                "status.llm_processed": False,
                "expired": {"$ne": True},
                "$or": [{"scraped_info.end_date": {"$gte": now}}, {"scraped_info.end_date": None}],
            }
        ).limit(limit)
        async for tender in cursor:
            bid_id = tender.get("bid_id")
            if bid_id and await self.process_tender(bid_id):
                processed += 1
        return processed

    async def reprocess_tender(self, tender_id: str) -> bool:
        tender = await self.repo.get_by_id(tender_id)
        if not tender:
            return False
        status = tender.get("status", {})
        status.update({"llm_processed": False, "embedding_generated": False, "last_error": None})
        await self.repo.update(tender_id, {"status": status})
        return await self.process_tender(tender.get("bid_id"))

    async def _update_expired_flags(self) -> None:
        now = datetime.now(timezone.utc)
        await self.repo.collection.update_many(
            {"scraped_info.end_date": {"$lt": now}},
            {"$set": {"expired": True}},
        )

    def _build_filters(
        self,
        status: Optional[str],
        domains: Optional[List[str]],
        search: Optional[str],
        expired: Optional[bool],
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}
        and_filters: List[Dict[str, Any]] = []

        if status == "processed":
            filters["status.llm_processed"] = True
        elif status == "pending":
            filters["status.llm_processed"] = False
        elif status == "failed":
            and_filters.append(
                {
                    "$or": [
                        {"status.last_error": {"$ne": None}},
                        {"status.scrape_status": "failed"},
                    ]
                }
            )

        if domains:
            filters["metadata.domains"] = {"$in": domains}

        if search:
            regex = {"$regex": search, "$options": "i"}
            and_filters.append(
                {
                    "$or": [
                        {"bid_id": regex},
                        {"metadata.title": regex},
                        {"scraped_info.items": regex},
                        {"scraped_info.department": regex},
                        {"metadata.department": regex},
                    ]
                }
            )

        if expired is not None:
            filters["expired"] = expired

        if and_filters:
            filters["$and"] = and_filters
        return filters

    def _sort_field(self, sort_by: str) -> str:
        mapping = {
            "created_at": "created_at",
            "updated_at": "updated_at",
            "scraped_at": "scraped_at",
            "end_date": "scraped_info.end_date",
        }
        return mapping.get(sort_by, "created_at")
