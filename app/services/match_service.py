"""
Company-to-tender matching service with hybrid scoring and actions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database.repositories.company_repo import CompanyRepository
from app.database.repositories.tender_action_repo import TenderActionRepository
from app.database.repositories.tender_repo import TenderRepository
from app.processors.embedder import TextEmbedder
from app.services.relevance_service import RelevanceService, build_match, cosine_similarity


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MatchService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db
        self.company_repo = CompanyRepository(db)
        self.tender_repo = TenderRepository(db)
        self.action_repo = TenderActionRepository(db)
        self.embedder = TextEmbedder()
        self.relevance = RelevanceService(db)

    async def get_company_matches(
        self,
        company_id: str,
        owner_user_id: str,
        q: Optional[str],
        time_period: str,
        sort: str,
        min_score: float,
        page: int,
        limit: int,
    ) -> Dict[str, Any]:
        profile = await self.company_repo.get_by_id(company_id)
        if not profile or profile.get("owner_user_id") != owner_user_id:
            raise ValueError("Company profile not found")

        candidate_filter = {
            "is_active": True,
            "expired": False,
            "status.llm_processed": True,
        }

        now = utcnow()
        period = (time_period or "latest").lower()
        if period == "7d":
            candidate_filter["scraped_at"] = {"$gte": now - timedelta(days=7)}
        elif period == "30d":
            candidate_filter["scraped_at"] = {"$gte": now - timedelta(days=30)}

        if q:
            regex = {"$regex": q, "$options": "i"}
            candidate_filter["$or"] = [
                {"metadata.title": regex},
                {"metadata.summary": regex},
                {"scraped_info.department": regex},
                {"metadata.department": regex},
                {"bid_id": regex},
            ]

        candidates = await self.tender_repo.list(skip=0, limit=1000, filters=candidate_filter)
        profile_embedding = profile.get("summary_embedding") or []
        if not profile_embedding:
            profile_summary = (profile.get("metadata") or {}).get("summary") or ""
            profile_embedding = self.embedder.embed(profile_summary)

        judgments = await self.relevance.get_judgments(company_id, profile)
        discarded = {
            item.get("tender_id")
            async for item in self.action_repo.collection.find({"company_id": company_id, "action": "discarded"})
        }

        scored: List[Tuple[Dict[str, Any], float, Dict[str, Any]]] = []
        for tender in candidates:
            if str(tender.get("_id")) in discarded:
                continue  # discarded tenders live in My List > Discarded
            vector_similarity = cosine_similarity(profile_embedding, tender.get("summary_embedding") or [])
            judgment = self.relevance.valid_judgment(judgments.get(str(tender.get("_id"))), tender)
            match = build_match(tender, profile, vector_similarity, judgment)
            if match["score"] >= min_score:
                scored.append((tender, match["score"], match))

        sort_key = (sort or "best_match").lower()
        if "latest" in sort_key:
            scored.sort(key=lambda item: self._timestamp(item[0].get("scraped_at")), reverse=True)
        else:
            scored.sort(
                key=lambda item: (
                    item[1],
                    self._timestamp((item[0].get("scraped_info") or {}).get("end_date")),
                ),
                reverse=True,
            )

        total = len(scored)
        start = max(page - 1, 0) * limit
        end = start + limit
        page_items = scored[start:end]

        action_map = await self.action_repo.get_action_map(
            company_id,
            [str(item[0].get("_id")) for item in page_items],
        )

        items = []
        for tender, _score, match in page_items:
            serialized = self._serialize_tender(tender)
            serialized.pop("summary_embedding", None)
            tender_id = serialized.get("id")
            items.append(
                {
                    "tender": serialized,
                    "match": match,
                    "action": action_map.get(tender_id),
                }
            )

        return {
            "items": items,
            "page": page,
            "limit": limit,
            "total": total,
        }

    async def update_action(
        self,
        company_id: str,
        user_id: str,
        tender_id: str,
        action: Optional[str],
    ) -> Dict[str, Any]:
        if action not in {"saved", "applied", "discarded", None}:
            raise ValueError("Invalid action")

        tender = await self.tender_repo.get_by_id(tender_id)
        if not tender:
            raise ValueError("Tender not found")

        await self.action_repo.set_action(
            company_id=company_id,
            user_id=user_id,
            tender_id=tender_id,
            action=action,
        )
        return {"updated": True, "action": action}

    async def get_my_list(
        self,
        company_id: str,
        tab: Optional[str],
        page: int,
        limit: int,
    ) -> Dict[str, Any]:
        action = tab if tab in {"saved", "applied", "discarded"} else None
        skip = max(page - 1, 0) * limit
        total = await self.action_repo.count_by_company(company_id=company_id, action=action)
        records = await self.action_repo.list_by_company(company_id=company_id, action=action, skip=skip, limit=limit)

        profile = await self.company_repo.get_by_id(company_id) or {}
        judgments = await self.relevance.get_judgments(company_id, profile) if profile else {}

        tender_ids = [record.get("tender_id") for record in records if record.get("tender_id")]
        tender_map: Dict[str, Dict[str, Any]] = {}
        match_map: Dict[str, Dict[str, Any]] = {}
        for tender_id in tender_ids:
            tender = await self.tender_repo.get_by_id(tender_id)
            if tender:
                if profile:
                    vector = cosine_similarity(profile.get("summary_embedding") or [], tender.get("summary_embedding") or [])
                    judgment = self.relevance.valid_judgment(judgments.get(str(tender.get("_id"))), tender)
                    match_map[tender_id] = build_match(tender, profile, vector, judgment)
                serialized = self._serialize_tender(tender)
                serialized.pop("summary_embedding", None)
                tender_map[tender_id] = serialized

        items = []
        for record in records:
            tender_id = record.get("tender_id")
            items.append(
                {
                    "action": record.get("action"),
                    "updated_at": record.get("updated_at"),
                    "tender": tender_map.get(tender_id),
                    "match": match_map.get(tender_id),
                }
            )

        return {"items": items, "page": page, "limit": limit, "total": total}

    async def get_tender_match(self, company_id: str, owner_user_id: str, tender_id: str) -> Dict[str, Any]:
        """Match details of one tender for one company (used by the tender detail page)."""
        profile = await self.company_repo.get_by_id(company_id)
        if not profile or profile.get("owner_user_id") != owner_user_id:
            raise ValueError("Company profile not found")
        tender = await self.tender_repo.get_by_id(tender_id)
        if not tender:
            raise ValueError("Tender not found")

        judgments = await self.relevance.get_judgments(company_id, profile)
        judgment = self.relevance.valid_judgment(judgments.get(str(tender.get("_id"))), tender)
        if judgment is None:
            try:
                judgment = await self.relevance.judge(tender, profile)
                await self.relevance._store(company_id, tender, profile, judgment)
            except Exception:
                judgment = None
        vector_similarity = cosine_similarity(profile.get("summary_embedding") or [], tender.get("summary_embedding") or [])
        return build_match(tender, profile, vector_similarity, judgment)

    def _serialize_tender(self, tender: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(tender)
        data["id"] = str(data["_id"])
        data["_id"] = str(data["_id"])
        return data

    def _timestamp(self, value: Any) -> float:
        if isinstance(value, datetime):
            return value.timestamp()
        return 0.0
