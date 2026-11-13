"""
Search and matching service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database.repositories.company_repo import CompanyRepository
from app.database.repositories.tender_repo import TenderRepository
from app.processors.embedder import TextEmbedder
from app.services.matching_utils import calculate_enhanced_match_score
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SearchService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.company_repo = CompanyRepository(db)
        self.tender_repo = TenderRepository(db)
        self.embedder = TextEmbedder()
        self.db = db

    async def search_tenders(
        self,
        company_id: str,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        profile = await self.company_repo.get_by_id(company_id)
        if not profile:
            raise ValueError("Company profile not found")

        query_embedding = self._get_query_embedding(profile, query)

        candidate_filter = {"is_active": True, "expired": False}
        if filters:
            if filters.get("domains"):
                candidate_filter["metadata.domains"] = {"$in": filters["domains"]}
            if filters.get("required_certifications"):
                candidate_filter["metadata.required_certifications"] = {"$in": filters["required_certifications"]}

        candidates = await self.tender_repo.list(skip=0, limit=500, filters=candidate_filter)

        scored: List[Tuple[Dict[str, Any], float, List[str]]] = []
        for tender in candidates:
            tender_embedding = tender.get("summary_embedding") or []
            vector_score = self._cosine_similarity(query_embedding, tender_embedding)

            # Use enhanced matching algorithm
            tender_meta = tender.get("metadata") or {}
            profile_meta = profile.get("metadata") or {}
            final_score, reasons = calculate_enhanced_match_score(
                tender_meta, profile_meta, vector_score
            )
            scored.append((tender, final_score, reasons))

        scored.sort(key=lambda item: (-item[1], self._end_date_sort(item[0])))
        top = scored[:limit]

        results = []
        for tender, score, reasons in top:
            results.append(
                {
                    "tender_id": str(tender.get("_id")),
                    "bid_id": tender.get("bid_id"),
                    "score": round(score, 4),
                    "match_reasons": reasons,
                }
            )

        await self._log_search(company_id, query, filters, results)

        return {
            "company_id": company_id,
            "results": results,
            "total": len(results),
            "generated_at": datetime.now(timezone.utc),
            "query": query,
            "filters_applied": filters,
        }

    async def match_companies_for_tender(self, tender_id: str, limit: int = 5) -> Dict[str, Any]:
        tender = await self.tender_repo.get_by_id(tender_id)
        if not tender:
            raise ValueError("Tender not found")

        tender_embedding = tender.get("summary_embedding") or []
        companies = await self.company_repo.list(skip=0, limit=500)

        scored: List[Tuple[Dict[str, Any], float, List[str]]] = []
        for company in companies:
            company_embedding = company.get("summary_embedding") or []
            vector_score = self._cosine_similarity(tender_embedding, company_embedding)

            # Use enhanced matching algorithm
            tender_meta = tender.get("metadata") or {}
            company_meta = company.get("metadata") or {}
            final_score, reasons = calculate_enhanced_match_score(
                tender_meta, company_meta, vector_score
            )
            scored.append((company, final_score, reasons))

        scored.sort(key=lambda item: item[1], reverse=True)
        top = scored[:limit]

        results = []
        for company, score, reasons in top:
            results.append(
                {
                    "company_id": company.get("company_id"),
                    "name": company.get("name") or (company.get("metadata") or {}).get("company_name"),
                    "score": round(score, 4),
                    "match_reasons": reasons,
                }
            )

        return {"tender_id": tender_id, "results": results, "total": len(results)}

    def _get_query_embedding(self, profile: Dict[str, Any], query: Optional[str]) -> List[float]:
        if query:
            return self.embedder.embed(query)
        embedding = profile.get("summary_embedding")
        if embedding:
            return embedding
        summary = (profile.get("metadata") or {}).get("summary") or ""
        return self.embedder.embed(summary)

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        vec_a = np.array(a, dtype=float)
        vec_b = np.array(b, dtype=float)
        denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
        if denom == 0:
            return 0.0
        return float(np.dot(vec_a, vec_b) / denom)

    def _metadata_score(self, tender: Dict[str, Any], profile: Dict[str, Any]) -> Tuple[float, List[str]]:
        tender_meta = tender.get("metadata") or {}
        profile_meta = profile.get("metadata") or {}

        reasons = []
        score = 0.0

        score += self._overlap_score(
            tender_meta.get("domains"), profile_meta.get("domains"), "Domain", reasons
        )
        score += self._overlap_score(
            tender_meta.get("required_certifications"), profile_meta.get("certifications"), "Certifications", reasons
        )
        score += self._overlap_score(
            tender_meta.get("required_technologies"), profile_meta.get("technologies"), "Technologies", reasons
        )

        if tender_meta.get("sector") and profile_meta.get("government_experience"):
            score += 0.1
            reasons.append("Government sector experience")

        return min(score, 1.0), reasons

    def _overlap_score(self, tender_values, profile_values, label: str, reasons: List[str]) -> float:
        tender_set = set((tender_values or []))
        profile_set = set((profile_values or []))
        if not tender_set or not profile_set:
            return 0.0
        overlap = tender_set.intersection(profile_set)
        if not overlap:
            return 0.0
        reasons.append(f"{label}: {', '.join(sorted(overlap))}")
        return min(len(overlap) / max(len(tender_set), 1), 1.0) * 0.3

    def _end_date_sort(self, tender: Dict[str, Any]) -> float:
        end_date = ((tender.get("scraped_info") or {}).get("end_date"))
        if not end_date:
            return 0.0
        if isinstance(end_date, datetime):
            return end_date.timestamp()
        return 0.0

    async def _log_search(
        self,
        company_id: str,
        query: Optional[str],
        filters: Optional[Dict[str, Any]],
        results: List[Dict[str, Any]],
    ) -> None:
        payload = {
            "company_id": company_id,
            "search_query": query,
            "filters_applied": filters or {},
            "results_count": len(results),
            "top_results": results[:5],
            "searched_at": datetime.now(timezone.utc),
        }
        await self.db.get_collection("search_history").insert_one(payload)
