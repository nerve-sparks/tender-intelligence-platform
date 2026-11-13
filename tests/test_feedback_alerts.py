"""
Startup/MSE relaxations, learning from Save/Discard, learned keywords, match alerts,
and recommendation/score consistency.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.processors.gem_bid_fields import parse_gem_bid_fields
from app.services.match_service import MatchService
from app.services.matching_utils import check_eligibility
from app.services.relevance_service import RelevanceService, build_match

GEM_TEXT = """
व'तु (ेणी /Item Category
Artificial intelligence, Machine Learning, and Deep Learning as a Service - Development
वष%/Years of Past Experience Required for
same/similar service
3 Year (s)
टन%ओवर के िलए एमएसई को छूट :ा; है / MSE
Relaxation for Turnover
Relaxation for Turnover
Yes | Partial | Turn over value - 75 (in lakhs)
क गई है /Startup Relaxation for Years of
Experience and Turnover
Experience and Turnover
Yes
"""


def test_parse_gem_bid_fields():
    fields = parse_gem_bid_fields(GEM_TEXT)
    assert fields["item_category"].startswith("Artificial intelligence, Machine Learning")
    assert fields["mse_relaxation"] == {"experience": False, "turnover": True}
    assert fields["startup_relaxation"] == {"experience": True, "turnover": True}


def test_startup_relaxation_waives_experience():
    tender = {"min_experience_years": 3, "startup_relaxation": {"experience": True, "turnover": True}}
    startup = check_eligibility(tender, {"is_dpiit_startup": True}, profile_experience_years=2)
    assert startup["fit"] == "MET"
    assert startup["checks"][0]["status"] == "exempt"
    assert "Startup relaxation" in startup["checks"][0]["company"]

    not_startup = check_eligibility(tender, {"registrations": ["GST"]}, profile_experience_years=2)
    assert not_startup["fit"] == "NOT_MET"


def test_mse_detected_from_registrations_but_relaxation_must_cover_criterion():
    tender = {"min_experience_years": 3, "mse_relaxation": {"experience": False, "turnover": True}}
    result = check_eligibility(tender, {"registrations": ["Udyam MSME registration"]}, profile_experience_years=2)
    assert result["fit"] == "NOT_MET"  # MSE relaxation here covers turnover only


def test_recommendation_follows_score():
    tender = {"_id": "t", "metadata": {"procurement_type": "software", "title": "AI platform"}}
    profile = {"metadata": {}, "experience_years": 5}
    judgment = {"business_relevance": "HIGH", "capability_alignment": "MEDIUM", "buyer_relevance": "LOW",
                "recommendation": "SKIP"}
    match = build_match(tender, profile, 0.8, judgment)
    assert 0.7 <= match["score"] < 0.8
    assert match["recommendation"] == "CONSIDER"  # not the LLM's contradictory SKIP
    assert match["ai_recommendation"] == "SKIP"


class RecordingLLM:
    def __init__(self):
        self.companies = []

    def judge_relevance(self, company, tender):
        self.companies.append(company)
        return {"business_relevance": "HIGH", "capability_alignment": "HIGH", "buyer_relevance": "HIGH",
                "why_matches": ["fit"], "gaps": [], "recommendation": "PURSUE"}


def _tender(tid, title, category):
    now = datetime.now(timezone.utc)
    return {
        "_id": tid, "bid_id": f"GEM/{tid}", "is_active": True, "expired": False, "processed_at": "p1",
        "created_at": now, "scraped_at": now, "status": {"llm_processed": True},
        "summary_embedding": [1.0, 0.0],
        "scraped_info": {"end_date": now + timedelta(days=9)},
        "metadata": {"title": title, "procurement_type": "software", "what_is_being_bought": title,
                     "item_category": category, "summary": title},
    }


async def _seed(fake_db):
    await fake_db.get_collection("company_profiles").insert_one(
        {"company_id": "c1", "owner_user_id": "u1", "name": "AI Co", "experience_years": 5,
         "metadata_updated_at": "v1", "summary_embedding": [1.0, 0.0],
         "status": {"processing_status": "ready"},
         "metadata": {"core_capabilities": ["machine learning platform", "chatbot development"],
                      "preferred_procurement_types": ["software", "services"],
                      "tender_search_keywords": ["artificial intelligence"]}}
    )
    tenders = fake_db.get_collection("tenders")
    await tenders.insert_one(_tender("t1", "Machine learning platform development", "Data Analytics Services - Development"))
    await tenders.insert_one(_tender("t2", "Chatbot development for citizen services", "Chatbot Services"))
    await tenders.insert_one(_tender("t3", "Old machine learning platform", "Artificial intelligence, Machine Learning"))


@pytest.mark.asyncio
async def test_alerts_learned_keywords_and_feedback(fake_db):
    await _seed(fake_db)
    # The user discarded t3 earlier: it becomes a "rejected" example and disappears from the list.
    await fake_db.get_collection("tender_actions").insert_one(
        {"company_id": "c1", "tender_id": "t3", "action": "discarded", "updated_at": datetime.now(timezone.utc)}
    )

    llm = RecordingLLM()
    stats = await RelevanceService(fake_db, llm=llm).refresh_company("c1")
    assert stats["judged"] == 3
    assert stats["new_alerts"] == 3

    # B: past decisions are passed to the judge
    assert llm.companies[0]["past_decisions"]["rejected"][0].startswith("Old machine learning platform")

    # E: one in-app alert per tender, no duplicates on re-run
    notes = fake_db.get_collection("notifications")
    assert await notes.count_documents({"company_id": "c1"}) == 3
    await fake_db.get_collection("company_profiles").update_one({"company_id": "c1"}, {"$set": {"metadata_updated_at": "v2"}})
    stats = await RelevanceService(fake_db, llm=llm).refresh_company("c1")
    assert stats["new_alerts"] == 0
    assert await notes.count_documents({"company_id": "c1"}) == 3

    # C: GeM categories of good matches become search keywords (no duplicates of existing ones)
    profile = await fake_db.get_collection("company_profiles").find_one({"company_id": "c1"})
    assert profile["metadata"]["learned_search_keywords"] == ["Data Analytics Services", "Chatbot Services"]

    # Discarded tender is hidden from the matches list
    result = await MatchService(fake_db).get_company_matches(
        company_id="c1", owner_user_id="u1", q=None, time_period="latest", sort="best_match",
        min_score=0.0, page=1, limit=10,
    )
    assert {item["tender"]["_id"] for item in result["items"]} == {"t1", "t2"}
