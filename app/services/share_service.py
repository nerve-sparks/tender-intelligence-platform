"""
Sharing tenders by email: one tender, or all of a company's saved tenders in one email
with the original GeM bid PDFs attached.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.services.email_service import Attachment, EmailService
from app.services.match_service import MatchService
from app.services.tender_service import TenderService
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_SAVED_TENDERS = 100
RECOMMENDATION_LABELS = {"PURSUE": "Pursue", "CONSIDER": "Consider", "SKIP": "Do not pursue"}


def _date(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    return str(value)[:10] if value else None


def _days_left(value: Any) -> Optional[int]:
    if not isinstance(value, datetime):
        return None
    end = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0, (end - datetime.now(timezone.utc)).days)


def _sort_key(value: Any) -> float:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    return float("inf")


def document_filename(tender: Dict[str, Any]) -> str:
    bid_id = str(tender.get("bid_id") or tender.get("_id") or "tender")
    return f"{bid_id.replace('/', '_')}.pdf"  # e.g. GEM_2026_B_8195819.pdf


def tender_email_entry(tender: Dict[str, Any], match: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The fields an email shows for one tender, using its real match result."""
    meta = tender.get("metadata") or {}
    scraped = tender.get("scraped_info") or {}
    end_date = scraped.get("end_date")
    close = _date(end_date)
    days = _days_left(end_date)
    if close and days is not None:
        close = f"{close} ({days} day{'s' if days != 1 else ''} left)"

    entry: Dict[str, Any] = {
        "id": str(tender.get("_id") or tender.get("id")),
        "title": meta.get("title") or scraped.get("items") or tender.get("bid_id"),
        "department": meta.get("buyer") or meta.get("department") or scraped.get("department"),
        "location": meta.get("location"),
        "bid_id": tender.get("bid_id"),
        "gem_url": tender.get("gem_url") or tender.get("pdf_url"),
        "summary": meta.get("what_is_being_bought") or meta.get("summary"),
        "published_date": _date(scraped.get("start_date")),
        "close_date": close,
        "emd_amount": meta.get("emd_amount"),
        "bid_value": scraped.get("bid_value_range") or meta.get("estimated_value"),
        "match_percent": None,
        "recommendation": None,
        "why": None,
        "gap": None,
    }
    if match:
        entry["match_percent"] = round((match.get("score") or 0) * 100)
        if match.get("verified"):
            entry["recommendation"] = RECOMMENDATION_LABELS.get(match.get("recommendation") or "")
        entry["why"] = (match.get("why_matches") or [None])[0]
        entry["gap"] = (match.get("gaps") or [None])[0]
    return entry


class ShareService:
    def __init__(self, db: AsyncIOMotorDatabase, email: Optional[EmailService] = None,
                 tenders: Optional[TenderService] = None) -> None:
        self.db = db
        self.email = email or EmailService()
        self._tenders = tenders

    @property
    def tenders(self) -> TenderService:
        if self._tenders is None:
            self._tenders = TenderService(self.db)
        return self._tenders

    async def share_saved(self, company_id: str, owner_user_id: str, recipients: List[str],
                          note: Optional[str] = None) -> Dict[str, Any]:
        """Email every saved tender of the company in one message, bid PDFs attached while they fit."""
        profile = await self.db.get_collection("company_profiles").find_one(
            {"company_id": company_id, "owner_user_id": owner_user_id}
        )
        if not profile:
            raise LookupError("Company profile not found")

        saved = await MatchService(self.db).get_my_list(company_id, "saved", 1, MAX_SAVED_TENDERS)
        items = [item for item in saved.get("items") or [] if item.get("tender")]
        if not items:
            raise ValueError("No saved tenders to send")
        # Nearest closing date first: those PDFs get attached before the size limit is reached.
        items.sort(key=lambda item: _sort_key((item["tender"].get("scraped_info") or {}).get("end_date")))

        budget = int(settings.EMAIL_MAX_ATTACHMENT_MB * 1024 * 1024)
        used = 0
        attachments: List[Attachment] = []
        entries: List[Dict[str, Any]] = []
        link_only = 0
        for item in items:
            tender = item["tender"]
            entry = tender_email_entry(tender, item.get("match"))
            data = await self._read_document(entry["id"])
            if data is not None and used + len(data) <= budget:
                filename = document_filename(tender)
                attachments.append((filename, data))
                used += len(data)
                entry["document"] = f"attached ({filename})"
            else:
                link_only += 1
                entry["document"] = (
                    "not attached (email size limit) - use the GeM link"
                    if data is not None
                    else "not available right now - use the GeM link"
                )
            entries.append(entry)

        company_name = profile.get("name") or (profile.get("metadata") or {}).get("company_name") or "Your company"
        result = await self.email.send_tender_digest(
            recipients=recipients, company_name=company_name, entries=entries, attachments=attachments, note=note,
        )

        now = datetime.now(timezone.utc)
        logs = [
            {
                "company_id": company_id,
                "tender_id": entry["id"],
                "sent_to": recipients,
                "bulk": True,
                "status": "sent" if result.get("sent") else "failed",
                "provider": result.get("provider"),
                "error": result.get("error"),
                "created_at": now,
            }
            for entry in entries
        ]
        await self.db.get_collection("share_logs").insert_many(logs)

        return {
            "sent": bool(result.get("sent")),
            "count": result.get("count", 0),
            "tenders": len(entries),
            "attached": len(attachments),
            "link_only": link_only,
        }

    async def _read_document(self, tender_id: str) -> Optional[bytes]:
        try:
            document = await self.tenders.get_download(tender_id)
            if not document or not os.path.exists(document["path"]):
                return None
            with open(document["path"], "rb") as handle:
                return handle.read()
        except Exception as exc:
            logger.info("share.document_failed", tender_id=tender_id, error=str(exc))
            return None
