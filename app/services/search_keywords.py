"""
GeM search keywords derived from company offering profiles.

Each ready company profile contributes search terms (its own `tender_search_keywords`,
generated once by the LLM when missing). The scraper runs GeM's keyword search with
them so relevant bids are found even when they are a tiny share of the overall listing.
Nothing here is specific to any company or industry.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.matching_utils import GENERIC_WORDS, normalize_text
from app.services.relevance_service import COMPANY_PROFILE_KEYS
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_KEYWORD_WORDS = 4


def _clean(keywords: List[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for keyword in keywords or []:
        text = " ".join(str(keyword).split())
        norm = normalize_text(text)
        if not norm or len(norm.split()) > MAX_KEYWORD_WORDS or norm in seen:
            continue
        seen.add(norm)
        out.append(text)
    return out


CUSTOM_SERVICE_PREFIX = "custom bid for service"


def keyword_matches_listing(keyword: str, listing_text: Optional[str]) -> bool:
    """
    Decide whether a GeM keyword-search hit is worth downloading and analysing.

    GeM's search matches any word anywhere in the bid documents ("artificial intelligence" also
    returns "ARTIFICIAL FLOWER"; "software development" returns MS Office licences). Keep a hit when:
      - every keyword word is on the card (GeM truncates item text, so the cut-off last word may
        match as a prefix, e.g. "Machi..." for "machine"), or
      - the card shows the keyword's head word (its last non-generic word, e.g. "automation"), or
      - it is a GeM "Custom Bid for Services", whose real scope is only in the bid document.
    """
    raw = listing_text or ""
    truncated = raw.rstrip().endswith("...")
    text = normalize_text(raw.replace("...", " "))
    if not text:
        return True  # nothing to check against; let analysis decide
    if text.startswith(CUSTOM_SERVICE_PREFIX):
        return True

    words = text.split()
    last = words[-1]
    keyword_words = normalize_text(keyword).split()

    def on_card(part: str) -> bool:
        return part in words or (truncated and len(last) >= 3 and part.startswith(last))

    if all(on_card(part) for part in keyword_words):
        return True
    # Otherwise the keyword's head word (its last specific word: "automation" in "process automation",
    # "intelligence" in "artificial intelligence") must be on the card. Generic words never count.
    specific = [p for p in keyword_words if len(p) >= 5 and p not in GENERIC_WORDS]
    return bool(specific) and on_card(specific[-1])


async def ensure_profile_keywords(db: AsyncIOMotorDatabase, profile: Dict[str, Any], llm: Any = None) -> List[str]:
    """Return the profile's GeM search keywords, generating and storing them once if missing."""
    meta = profile.get("metadata") or {}
    learned = _clean(meta.get("learned_search_keywords") or [])
    keywords = _clean(meta.get("tender_search_keywords") or [])
    if keywords:
        return _clean(keywords + learned)

    company = {k: meta.get(k) for k in COMPANY_PROFILE_KEYS if meta.get(k)}
    company.setdefault("company_name", profile.get("name"))
    try:
        if llm is None:
            from app.processors.llm_extractor import LLMExtractor

            llm = LLMExtractor()
        keywords = _clean(await asyncio.to_thread(llm.suggest_search_keywords, company))
    except Exception as exc:
        logger.info("search_keywords.generate_failed", company_id=profile.get("company_id"), error=str(exc))
        keywords = []

    if not keywords:
        # Last resort: the profile's own short capability phrases.
        keywords = _clean((meta.get("core_capabilities") or []) + (profile.get("interest_tags") or []))
    if keywords:
        # Not a change to the offering itself, so metadata_updated_at (match cache key) is untouched.
        await db.get_collection("company_profiles").update_one(
            {"company_id": profile.get("company_id")},
            {"$set": {"metadata.tender_search_keywords": keywords}},
        )
    return keywords


async def collect_search_keywords(db: AsyncIOMotorDatabase, limit: int, llm: Any = None) -> List[str]:
    """Keywords from all ready company profiles, interleaved so every company gets coverage."""
    per_company: List[List[str]] = []
    cursor = db.get_collection("company_profiles").find({"status.processing_status": "ready"})
    async for profile in cursor:
        if profile.get("metadata"):
            per_company.append(await ensure_profile_keywords(db, profile, llm=llm))

    merged: List[str] = []
    seen = set()
    index = 0
    while len(merged) < limit and any(index < len(kws) for kws in per_company):
        for kws in per_company:
            if index < len(kws):
                norm = normalize_text(kws[index])
                if norm not in seen:
                    seen.add(norm)
                    merged.append(kws[index])
                    if len(merged) >= limit:
                        break
        index += 1
    return merged
