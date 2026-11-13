"""
Business-relevance matching tests.

The same labelled tenders are evaluated against two very different company
profiles to prove that nothing in the matcher is specific to one company.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.match_service import MatchService
from app.services.matching_utils import check_eligibility, hard_gate, parse_inr, quick_match_score
from app.services.relevance_service import RelevanceService, build_match

AI_COMPANY = {
    "company_id": "ai-co",
    "owner_user_id": "u1",
    "name": "AI Services Co",
    "experience_years": 6,
    "interest_tags": [],
    "metadata_updated_at": "v1",
    "summary_embedding": [1.0, 0.0],
    "metadata": {
        "business_type": "AI services and software",
        "core_capabilities": ["LLM fine-tuning", "AI consulting", "document intelligence", "enterprise automation", "speech recognition"],
        "technologies": ["large language models", "OCR", "MLOps"],
        "preferred_procurement_types": ["software", "services", "consulting"],
        "excluded_capabilities": ["veterinary medicine", "pharmaceutical supply", "fencing", "civil construction", "hardware supply"],
        "annual_turnover_inr": 6e7,  # ₹6 Cr
    },
}

FENCING_COMPANY = {
    "company_id": "fence-co",
    "owner_user_id": "u2",
    "name": "Perimeter Works Ltd",
    "experience_years": 12,
    "interest_tags": [],
    "metadata_updated_at": "v1",
    "summary_embedding": [0.0, 1.0],
    "metadata": {
        "business_type": "Fencing and civil works contractor",
        "core_capabilities": ["perimeter fencing", "chain link fencing", "boundary wall construction"],
        "preferred_procurement_types": ["works", "goods"],
        "excluded_capabilities": ["software development", "AI"],
        "annual_turnover_inr": 2e8,
    },
}


def _tender(tid, title, ptype, bought, scope, emb, **extra):
    meta = {
        "title": title,
        "procurement_type": ptype,
        "what_is_being_bought": bought,
        "scope_items": scope,
        "summary": bought,
    }
    meta.update(extra)
    return {
        "_id": tid,
        "bid_id": f"GEM/{tid}",
        "is_active": True,
        "expired": False,
        "processed_at": "p1",
        "scraped_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "status": {"llm_processed": True},
        "summary_embedding": emb,
        "scraped_info": {"end_date": datetime.now(timezone.utc) + timedelta(days=10)},
        "metadata": meta,
    }


NAVY_LLM = _tender(
    "navy-llm",
    "Sovereign Large Language Model for Indian Navy",
    "software",
    "Development and on-premise deployment of sovereign LLMs with speech recognition and document intelligence (OCR)",
    ["Two sovereign LLMs fine-tuned on Navy corpus", "ASR and speaker diarization", "Document intelligence OCR"],
    [0.95, 0.1],
    buyer="Indian Navy",
    min_avg_turnover="₹4.93 Crore",
    min_experience_years=3,
)
VET_MEDICINE = _tender(
    "vet-med", "Supply of veterinary medicines", "goods",
    "Supply of veterinary medicine with drug licence and cold chain", ["Veterinary medicine supply"], [0.6, 0.6],
)
FENCING = _tender(
    "fencing", "Perimeter fencing", "works",
    "Supply and installation of perimeter fencing", ["Chain link fencing installation"], [0.1, 0.95],
)
CNC_SOFTWARE = _tender(
    "cnc", "CNC lathe programming software", "goods",
    "Machine-specific CNC lathe interface software and tooling", ["CNC interface tooling"], [0.6, 0.5],
)
VC_FACILITY = _tender(
    "vc", "Video conferencing facility", "goods",
    "Cameras, microphones, displays, cabling and installation", ["AV hardware installation"], [0.6, 0.4],
)
ALL_TENDERS = [NAVY_LLM, VET_MEDICINE, FENCING, CNC_SOFTWARE, VC_FACILITY]


class FakeJudgeLLM:
    """Stands in for the LLM: relevant only when the company's capabilities name what's bought."""

    def __init__(self):
        self.calls = 0

    def judge_relevance(self, company, tender):
        self.calls += 1
        text = f"{tender.get('title')} {tender.get('what_is_being_bought')}".lower()
        caps = " ".join(company.get("core_capabilities") or []).lower()
        relevant = any(word in text for word in caps.split() if len(word) > 4)
        level = "HIGH" if relevant else "NONE"
        return {
            "business_relevance": level,
            "capability_alignment": level,
            "buyer_relevance": "MEDIUM",
            "why_matches": ["Scope is within core capabilities"] if relevant else [],
            "gaps": [] if relevant else ["Outside business"],
            "recommendation": "PURSUE" if relevant else "SKIP",
            "reason": "test",
        }


# --------------------------------------------------------------------- gates


@pytest.mark.parametrize("tender", [VET_MEDICINE, FENCING, CNC_SOFTWARE, VC_FACILITY])
def test_hard_gate_rejects_irrelevant_tenders_for_ai_company(tender):
    assert hard_gate(tender["metadata"], AI_COMPANY["metadata"]) is not None


def test_hard_gate_passes_navy_llm_for_ai_company():
    assert hard_gate(NAVY_LLM["metadata"], AI_COMPANY["metadata"]) is None


def test_gate_is_profile_driven_fencing_company_keeps_fencing():
    assert hard_gate(FENCING["metadata"], FENCING_COMPANY["metadata"]) is None
    assert hard_gate(NAVY_LLM["metadata"], FENCING_COMPANY["metadata"]) is not None


def test_no_preferences_means_no_procurement_rejection():
    assert hard_gate(VET_MEDICINE["metadata"], {"core_capabilities": ["anything"]}) is None


def test_generic_words_alone_do_not_match():
    profile = {"core_capabilities": ["software services", "government technology solutions"]}
    score, reasons, _ = quick_match_score(CNC_SOFTWARE["metadata"], profile, vector_similarity=0.3)
    assert score == 0.0
    assert reasons == []


def test_quick_score_is_capped_without_judgment():
    score, _, _ = quick_match_score(NAVY_LLM["metadata"], AI_COMPANY["metadata"], vector_similarity=0.99)
    assert 0 < score <= 0.6


# ----------------------------------------------------------------- scoring


def test_navy_llm_scores_high_with_positive_judgment():
    judgment = FakeJudgeLLM().judge_relevance(AI_COMPANY["metadata"], NAVY_LLM["metadata"])
    match = build_match(NAVY_LLM, AI_COMPANY, vector_similarity=0.8, judgment=judgment)
    assert match["score"] >= 0.8
    assert match["recommendation"] == "PURSUE"
    assert match["eligibility"]["fit"] == "MET"


def test_none_relevance_overrides_high_similarity():
    judgment = {"business_relevance": "NONE", "capability_alignment": "HIGH", "buyer_relevance": "HIGH"}
    match = build_match(NAVY_LLM, AI_COMPANY, vector_similarity=0.99, judgment=judgment)
    assert match["score"] <= 0.05
    assert match["recommendation"] == "SKIP"


def test_low_relevance_is_clamped():
    judgment = {"business_relevance": "LOW", "capability_alignment": "HIGH", "buyer_relevance": "HIGH"}
    match = build_match(NAVY_LLM, AI_COMPANY, vector_similarity=0.99, judgment=judgment)
    assert match["score"] <= 0.30


def test_failed_eligibility_downgrades_recommendation():
    small = {**AI_COMPANY, "metadata": {**AI_COMPANY["metadata"], "annual_turnover_inr": 1e7}}
    judgment = FakeJudgeLLM().judge_relevance(small["metadata"], NAVY_LLM["metadata"])
    match = build_match(NAVY_LLM, small, vector_similarity=0.8, judgment=judgment)
    assert match["eligibility"]["fit"] == "NOT_MET"
    assert match["score"] <= 0.70
    assert match["recommendation"] == "CONSIDER"


def test_parse_inr_and_eligibility():
    assert parse_inr("₹4.93 Crore") == pytest.approx(4.93e7)
    assert parse_inr("50 Lakh") == pytest.approx(5e6)
    assert parse_inr("₹1,84,93,108") == pytest.approx(18493108)
    result = check_eligibility({"min_avg_turnover": "₹4.93 Cr", "min_experience_years": 3}, {"annual_turnover": "₹6 Crore"}, 6)
    assert result["fit"] == "MET"
    assert [c["status"] for c in result["checks"]] == ["met", "met"]


# ------------------------------------------------------- end-to-end via DB


async def _seed(fake_db):
    for profile in (AI_COMPANY, FENCING_COMPANY):
        await fake_db.get_collection("company_profiles").insert_one({**profile, "status": {"processing_status": "ready"}})
    for tender in ALL_TENDERS:
        await fake_db.get_collection("tenders").insert_one(tender)


async def _matches(fake_db, company):
    return await MatchService(fake_db).get_company_matches(
        company_id=company["company_id"], owner_user_id=company["owner_user_id"], q=None,
        time_period="latest", sort="best_match", min_score=0.0, page=1, limit=10,
    )


@pytest.mark.asyncio
async def test_end_to_end_rankings_for_two_different_companies(fake_db):
    await _seed(fake_db)
    ai_llm, fence_llm = FakeJudgeLLM(), FakeJudgeLLM()
    await RelevanceService(fake_db, llm=ai_llm).refresh_company("ai-co")
    await RelevanceService(fake_db, llm=fence_llm).refresh_company("fence-co")
    # For the AI company every irrelevant tender is gate-rejected, so only the Navy LLM tender reaches the LLM.
    assert ai_llm.calls == 1

    ai = await _matches(fake_db, AI_COMPANY)
    ai_scores = {item["tender"]["_id"]: item["match"]["score"] for item in ai["items"]}
    assert ai["items"][0]["tender"]["_id"] == "navy-llm"
    assert ai_scores["navy-llm"] >= 0.8
    for tid in ("vet-med", "fencing", "cnc", "vc"):
        assert ai_scores[tid] <= 0.1

    fence = await _matches(fake_db, FENCING_COMPANY)
    fence_scores = {item["tender"]["_id"]: item["match"]["score"] for item in fence["items"]}
    assert fence["items"][0]["tender"]["_id"] == "fencing"
    assert fence_scores["fencing"] >= 0.8
    assert fence_scores["navy-llm"] <= 0.1


@pytest.mark.asyncio
async def test_profile_change_invalidates_cached_judgments(fake_db):
    await _seed(fake_db)
    llm = FakeJudgeLLM()
    relevance = RelevanceService(fake_db, llm=llm)
    await relevance.refresh_company("ai-co")
    await relevance.refresh_company("ai-co")
    assert llm.calls == 1  # second run uses the cache

    await fake_db.get_collection("company_profiles").update_one(
        {"company_id": "ai-co"}, {"$set": {"metadata_updated_at": "v2"}}
    )
    await relevance.refresh_company("ai-co")
    assert llm.calls == 2
