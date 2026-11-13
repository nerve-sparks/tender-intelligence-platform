"""
Business-relevance matching between any company profile and tenders.

Pipeline per (tender, company):
  1. Hard gate (profile-driven procurement type + exclusions)      -> reject, score ~0
  2. LLM relevance judgment (cached in `tender_matches`)            -> HIGH / MEDIUM / LOW / NONE
  3. Deterministic eligibility check (turnover, experience)
  4. Weighted business-first score, clamped by relevance; no multiplicative boosts.

Tender briefs are stored once per tender (tenders.metadata). Company-specific
judgments are stored once per pair in `tender_matches`, keyed by tender/profile
versions so they are recomputed when either side changes.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.processors.llm_extractor import TENDER_EXTRACTION_VERSION
from app.services.matching_utils import (
    check_eligibility,
    normalize_procurement_type,
    normalize_text,
    normalize_vector_similarity,
    quick_match_score,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

LEVELS = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.3, "NONE": 0.0}
RECOMMENDATIONS = {"PURSUE", "CONSIDER", "SKIP"}
MAX_DECISION_EXAMPLES = 5
MAX_LEARNED_KEYWORDS = 10
PURSUE_SCORE = 0.8  # at/above: "Pursue"; MATCH_MIN_SCORE..PURSUE_SCORE: "Consider"; below: "Skip"

WEIGHTS = {
    "business_relevance": 0.45,
    "capability_alignment": 0.25,
    "eligibility": 0.10,
    "buyer_relevance": 0.10,
    "vector": 0.10,
}

TENDER_BRIEF_KEYS = [
    "title", "buyer", "department", "sector", "procurement_type", "what_is_being_bought", "scope_items",
    "scope_categories", "required_products_or_services", "required_technologies", "required_certifications",
    "min_avg_turnover", "min_experience_years", "similar_work_required", "eligibility_requirements",
    "infrastructure_notes", "contract_period", "summary",
]

COMPANY_PROFILE_KEYS = [
    "company_name", "business_type", "core_capabilities", "offerings", "capabilities", "technologies", "domains",
    "industries", "target_sectors", "preferred_procurement_types", "excluded_capabilities", "certifications",
    "government_experience", "past_clients", "summary",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _level(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if text in ("MED",):
        text = "MEDIUM"
    if text in ("NO", "NOT RELEVANT"):
        text = "NONE"
    return text if text in LEVELS else None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    vec_a = np.array(a, dtype=float)
    vec_b = np.array(b, dtype=float)
    denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
    if denom == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / denom)


def tender_version(tender: Dict[str, Any]) -> str:
    processed = tender.get("processed_at") or tender.get("updated_at") or ""
    return f"{TENDER_EXTRACTION_VERSION}:{processed}"


def profile_version(profile: Dict[str, Any]) -> str:
    return str(profile.get("metadata_updated_at") or "")


def build_match(
    tender: Dict[str, Any],
    profile: Dict[str, Any],
    vector_similarity: float,
    judgment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Combine gate, judgment, eligibility and similarity into the match payload returned to clients."""
    tender_meta = tender.get("metadata") or {}
    profile_meta = profile.get("metadata") or {}
    scraped_info = tender.get("scraped_info") or {}

    eligibility = check_eligibility(tender_meta, profile_meta, profile.get("experience_years"))
    quick_score, quick_reasons, rejection = quick_match_score(
        tender_meta,
        profile_meta,
        vector_similarity,
        interest_tags=profile.get("interest_tags") or [],
        scraped_info=scraped_info,
    )

    match: Dict[str, Any] = {
        "score": quick_score,
        "verified": False,
        "procurement_type": normalize_procurement_type(tender_meta.get("procurement_type")),
        "business_relevance": None,
        "capability_alignment": None,
        "buyer_relevance": None,
        "eligibility": eligibility,
        "why_matches": [],
        "gaps": [],
        "recommendation": None,
        "ai_recommendation": None,
        "reason": None,
        "reasons": list(quick_reasons),
    }

    if rejection:
        match.update(
            {
                "score": 0.0,
                "verified": True,
                "business_relevance": "NONE",
                "capability_alignment": "NONE",
                "recommendation": "SKIP",
                "reason": rejection,
                "gaps": [rejection],
                "reasons": [rejection],
            }
        )
        return match

    if judgment:
        relevance = _level(judgment.get("business_relevance")) or "LOW"
        capability = _level(judgment.get("capability_alignment")) or "LOW"
        buyer = _level(judgment.get("buyer_relevance")) or "MEDIUM"
        score = (
            WEIGHTS["business_relevance"] * LEVELS[relevance]
            + WEIGHTS["capability_alignment"] * LEVELS[capability]
            + WEIGHTS["eligibility"] * eligibility["score"]
            + WEIGHTS["buyer_relevance"] * LEVELS[buyer]
            + WEIGHTS["vector"] * normalize_vector_similarity(vector_similarity)
        )
        # A hard "no" overrides any amount of similarity.
        if relevance == "NONE" or capability == "NONE":
            score = min(score, 0.05)
        elif relevance == "LOW":
            score = min(score, 0.30)

        gaps = [str(g) for g in (judgment.get("gaps") or []) if g][:4]
        ai_recommendation = str(judgment.get("recommendation") or "").upper()
        if ai_recommendation not in RECOMMENDATIONS:
            ai_recommendation = None

        if eligibility["fit"] == "NOT_MET":
            score = min(score, 0.70)
            failed = [c["label"] for c in eligibility["checks"] if c["status"] == "not_met"]
            gaps.insert(0, f"Does not meet: {', '.join(failed)}")

        # The badge follows the score so the two never contradict each other;
        # failed eligibility can never be "Pursue". Gemini's own call is kept as ai_recommendation.
        if score >= PURSUE_SCORE and eligibility["fit"] != "NOT_MET":
            recommendation = "PURSUE"
        elif score >= settings.MATCH_MIN_SCORE:
            recommendation = "CONSIDER"
        else:
            recommendation = "SKIP"
        match["ai_recommendation"] = ai_recommendation

        why = [str(w) for w in (judgment.get("why_matches") or []) if w][:4]
        match.update(
            {
                "score": round(min(max(score, 0.0), 1.0), 4),
                "verified": True,
                "business_relevance": relevance,
                "capability_alignment": capability,
                "buyer_relevance": buyer,
                "why_matches": why,
                "gaps": gaps[:5],
                "recommendation": recommendation,
                "reason": judgment.get("reason"),
                "reasons": why or quick_reasons,
            }
        )
    return match


class RelevanceService:
    def __init__(self, db: AsyncIOMotorDatabase, llm: Any = None) -> None:
        self.db = db
        self.collection = db.get_collection("tender_matches")
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from app.processors.llm_extractor import LLMExtractor

            self._llm = LLMExtractor()
        return self._llm

    # ------------------------------------------------------------------ cache

    async def get_judgments(self, company_id: str, profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Return {tender_id: cached_doc} for judgments valid for the current profile version."""
        current_profile_version = profile_version(profile)
        cursor = self.collection.find({"company_id": company_id})
        result: Dict[str, Dict[str, Any]] = {}
        async for doc in cursor:
            if doc.get("profile_version") == current_profile_version:
                result[str(doc.get("tender_id"))] = doc
        return result

    @staticmethod
    def valid_judgment(doc: Optional[Dict[str, Any]], tender: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not doc or doc.get("tender_version") != tender_version(tender):
            return None
        return doc.get("judgment")

    async def _store(self, company_id: str, tender: Dict[str, Any], profile: Dict[str, Any], judgment: Dict[str, Any]) -> None:
        await self.collection.update_one(
            {"company_id": company_id, "tender_id": str(tender.get("_id"))},
            {
                "$set": {
                    "judgment": judgment,
                    "tender_version": tender_version(tender),
                    "profile_version": profile_version(profile),
                    "judged_at": utcnow(),
                }
            },
            upsert=True,
        )

    # --------------------------------------------------------------- judging

    async def _find_tender(self, tender_id: Any) -> Optional[Dict[str, Any]]:
        tenders = self.db.get_collection("tenders")
        try:
            found = await tenders.find_one({"_id": ObjectId(str(tender_id))})
            if found:
                return found
        except Exception:
            pass
        return await tenders.find_one({"_id": tender_id})

    async def past_decisions(self, company_id: str) -> Dict[str, List[str]]:
        """
        The company's own bid decisions (Save / Apply = pursued, Discard = rejected) as short
        examples, so the LLM judgment learns its real preferences over time.
        """
        cursor = (
            self.db.get_collection("tender_actions")
            .find({"company_id": company_id, "action": {"$in": ["saved", "applied", "discarded"]}})
            .sort("updated_at", -1)
            .limit(40)
        )
        pursued: List[str] = []
        rejected: List[str] = []
        async for action in cursor:
            bucket = rejected if action.get("action") == "discarded" else pursued
            if len(bucket) >= MAX_DECISION_EXAMPLES:
                continue
            tender = await self._find_tender(action.get("tender_id"))
            meta = (tender or {}).get("metadata") or {}
            title = meta.get("title") or meta.get("item_category")
            if not title:
                continue
            detail = meta.get("what_is_being_bought") or meta.get("item_category") or ""
            text = f"{title}: {detail}" if detail and detail != title else str(title)
            bucket.append(text[:220])
        if not pursued and not rejected:
            return {}
        return {"pursued": pursued, "rejected": rejected}

    async def judge(
        self,
        tender: Dict[str, Any],
        profile: Dict[str, Any],
        decisions: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        tender_meta = tender.get("metadata") or {}
        profile_meta = profile.get("metadata") or {}
        tender_brief = {k: tender_meta.get(k) for k in TENDER_BRIEF_KEYS if tender_meta.get(k)}
        if not tender_brief.get("what_is_being_bought") and (tender.get("scraped_info") or {}).get("items"):
            tender_brief["items"] = tender["scraped_info"]["items"]
        company = {k: profile_meta.get(k) for k in COMPANY_PROFILE_KEYS if profile_meta.get(k)}
        company.setdefault("company_name", profile.get("name"))
        if profile.get("interest_tags"):
            company["interest_tags"] = profile["interest_tags"]
        if decisions is None:
            decisions = await self.past_decisions(profile.get("company_id"))
        if decisions:
            company["past_decisions"] = decisions
        return await asyncio.to_thread(self._get_llm().judge_relevance, company, tender_brief)

    async def refresh_company(self, company_id: str, max_llm: Optional[int] = None) -> Dict[str, int]:
        """
        Compute missing / stale LLM judgments for one company. Only tenders that pass
        the hard gate and the quick pre-score are sent to the LLM, best first.
        """
        stats = {"candidates": 0, "judged": 0, "cached": 0, "errors": 0}
        profile = await self.db.get_collection("company_profiles").find_one({"company_id": company_id})
        if not profile or not profile.get("metadata"):
            return stats

        max_llm = settings.MATCH_LLM_MAX_PER_RUN if max_llm is None else max_llm
        cache = await self.get_judgments(company_id, profile)
        profile_embedding = profile.get("summary_embedding") or []

        cursor = self.db.get_collection("tenders").find(
            {"is_active": True, "expired": False, "status.llm_processed": True}
        )
        ranked: List[Tuple[float, Dict[str, Any]]] = []
        async for tender in cursor:
            stats["candidates"] += 1
            if self.valid_judgment(cache.get(str(tender.get("_id"))), tender):
                stats["cached"] += 1
                continue
            vector = cosine_similarity(profile_embedding, tender.get("summary_embedding") or [])
            score, _, rejection = quick_match_score(
                tender.get("metadata") or {},
                profile.get("metadata") or {},
                vector,
                interest_tags=profile.get("interest_tags") or [],
                scraped_info=tender.get("scraped_info") or {},
            )
            if rejection or score < settings.MATCH_LLM_MIN_QUICK_SCORE:
                continue
            ranked.append((score, tender))

        ranked.sort(key=lambda item: item[0], reverse=True)
        decisions = await self.past_decisions(company_id) if ranked else {}
        judged: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for _, tender in ranked[:max_llm]:
            try:
                judgment = await self.judge(tender, profile, decisions=decisions)
                await self._store(company_id, tender, profile, judgment)
                judged.append((tender, judgment))
                stats["judged"] += 1
            except Exception as exc:  # LLM unavailable / bad output: keep the quick score
                stats["errors"] += 1
                logger.info("relevance.judge_failed", company_id=company_id, tender_id=str(tender.get("_id")), error=str(exc))

        if judged:
            try:
                stats["new_alerts"] = await self._after_judging(profile, judged)
            except Exception as exc:
                logger.info("relevance.after_judging_failed", company_id=company_id, error=str(exc))

        logger.info("relevance.refresh_company", company_id=company_id, **stats)
        return stats

    async def _after_judging(self, profile: Dict[str, Any], judged: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> int:
        """Alert on newly found good matches and learn GeM search keywords from them."""
        company_id = profile.get("company_id")
        notifications = self.db.get_collection("notifications")
        new_alerts: List[Dict[str, Any]] = []
        categories: List[str] = []
        for tender, judgment in judged:
            vector = cosine_similarity(profile.get("summary_embedding") or [], tender.get("summary_embedding") or [])
            match = build_match(tender, profile, vector, judgment)
            if match["score"] < settings.MATCH_MIN_SCORE:
                continue
            meta = tender.get("metadata") or {}
            if meta.get("item_category"):
                categories.append(meta["item_category"])
            alert = {
                "company_id": company_id,
                "owner_user_id": profile.get("owner_user_id"),
                "tender_id": str(tender.get("_id")),
                "bid_id": tender.get("bid_id"),
                "title": meta.get("title") or (tender.get("scraped_info") or {}).get("items"),
                "score": match["score"],
                "recommendation": match["recommendation"],
                "end_date": (tender.get("scraped_info") or {}).get("end_date"),
                "read": False,
                "created_at": utcnow(),
            }
            result = await notifications.update_one(
                {"company_id": company_id, "tender_id": alert["tender_id"]},
                {"$setOnInsert": alert},
                upsert=True,
            )
            if result.upserted_id:
                new_alerts.append(alert)

        await self._learn_keywords(profile, categories)
        if new_alerts:
            await manager_broadcast_new_matches(company_id, len(new_alerts))
        return len(new_alerts)

    async def _learn_keywords(self, profile: Dict[str, Any], categories: List[str]) -> None:
        """GeM item categories of high-scoring tenders become extra search keywords for the company."""
        meta = profile.get("metadata") or {}
        current = list(meta.get("learned_search_keywords") or [])
        learned = list(current)
        known = {normalize_text(k) for k in learned + list(meta.get("tender_search_keywords") or [])}
        for category in categories:
            head = re.split(r",|;| - | / |\(", category)[0].strip()
            norm = normalize_text(head)
            if norm and len(norm.split()) <= 4 and norm not in known:
                known.add(norm)
                learned.append(head)
        learned = learned[-MAX_LEARNED_KEYWORDS:]
        if learned != current:
            await self.db.get_collection("company_profiles").update_one(
                {"company_id": profile.get("company_id")},
                {"$set": {"metadata.learned_search_keywords": learned}},
            )

    async def alerts_for_existing(self, company_id: str) -> int:
        """Run alerts + keyword learning over judgments that already exist (one-off backfill)."""
        profile = await self.db.get_collection("company_profiles").find_one({"company_id": company_id})
        if not profile:
            return 0
        judged: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for tender_id, doc in (await self.get_judgments(company_id, profile)).items():
            tender = await self._find_tender(tender_id)
            judgment = self.valid_judgment(doc, tender) if tender else None
            if tender and judgment and not tender.get("expired"):
                judged.append((tender, judgment))
        return await self._after_judging(profile, judged) if judged else 0

    async def refresh_all_companies(self, company_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        if company_ids is None:
            cursor = self.db.get_collection("company_profiles").find({"status.processing_status": "ready"})
            company_ids = [doc.get("company_id") async for doc in cursor]
        results = {}
        for company_id in company_ids:
            if company_id:
                results[company_id] = await self.refresh_company(company_id)
        return results


async def manager_broadcast_new_matches(company_id: Optional[str], count: int) -> None:
    """Push a live 'new matches' event to open browser tabs (best effort)."""
    try:
        from app.services.socket_manager import manager

        await manager.broadcast(
            {"type": "NEW_MATCHES", "company_id": company_id, "count": count, "ts": utcnow().isoformat()}
        )
    except Exception:
        return


def schedule_refresh(db: AsyncIOMotorDatabase, company_id: Optional[str] = None) -> None:
    """Fire-and-forget background refresh (no-op when disabled)."""
    if not settings.MATCH_AUTO_REFRESH:
        return

    async def _run() -> None:
        try:
            service = RelevanceService(db)
            if company_id:
                await service.refresh_company(company_id)
            else:
                await service.refresh_all_companies()
        except Exception as exc:
            logger.info("relevance.background_refresh_failed", company_id=company_id, error=str(exc))

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        return
