import pytest

from app.scraper.parser import _parse_bid_text
from app.services.matching_utils import hard_gate
from app.services.search_keywords import (
    build_auto_keywords,
    collect_search_keywords,
    keyword_matches_listing,
    rank_keywords,
    record_keyword_yield,
)


def test_keyword_filter_drops_any_word_noise():
    assert keyword_matches_listing("artificial intelligence", "Artificial intelligence, Machi...")
    assert not keyword_matches_listing("artificial intelligence", "ARTIFICIAL FLOWER,FOAM SHEET,B...")
    # GeM truncates item text: a trailing partial word counts
    assert keyword_matches_listing("machine learning", "Artificial intelligence, Machine lea...")
    assert keyword_matches_listing("software", None)  # nothing to check -> analysis decides
    # generic words alone are not enough...
    assert not keyword_matches_listing("software development", "MS OFFICE,ANTIVIRUS,WINDOW 11,...")
    assert not keyword_matches_listing("system integration", "Music System with complete acc...")
    # GeM custom service bids always go to analysis
    assert keyword_matches_listing("data engineering", "Custom Bid for Services - IMPL...")
    # every meaningful word must be on the card - one shared word is not enough
    assert not keyword_matches_listing("Deployment of LLM", "Deployment of Technical Manpower for Control")
    assert not keyword_matches_listing("Learning Management System", "Supply of Learning Kit")
    assert not keyword_matches_listing("Content Management System", "Interactive Content Creation Services")
    assert not keyword_matches_listing("process automation", "Laboratory Automation Services")
    assert keyword_matches_listing("Learning Management System", "Learning Management System for training")
    assert keyword_matches_listing("Deployment of LLM", "Deployment of on-premise LLM for ...")
    # weak words ("services", "software") may be missing, plural/singular are the same
    assert keyword_matches_listing(
        "Machine Learning Services", "Artificial intelligence, Machine Learning, and Deep Learning as a Serv..."
    )
    assert keyword_matches_listing("Recruitment Software", "Online Recruitment Management Portal")
    assert keyword_matches_listing("data analytics", "Data Analytic Services")


def test_exclusion_needs_the_whole_phrase():
    profile = {"excluded_capabilities": ["Event management", "Facility management"]}
    ai_event = {"title": "Event-driven AI analytics platform", "procurement_type": "software"}
    assert hard_gate(ai_event, profile) is None
    real_event = {"title": "Event management for award ceremony", "procurement_type": "services"}
    assert hard_gate(real_event, profile) is not None


def test_parser_stops_items_at_next_label():
    text = (
        "BID NO: GEM/2026/B/1 Items: Artificial intelligence, Machi... Quantity: 1 "
        "Department Name And Address: Ministry Of Defence Start Date: 12-11-2026 3:32 PM End Date: 03-12-2026 4:00 PM"
    )
    bid = _parse_bid_text("GEM/2026/B/1", text, None)
    assert bid["items"] == "Artificial intelligence, Machi..."
    assert bid["department_address"] == "Ministry Of Defence"
    assert bid["end_date"] is not None


AI_PROFILE = {
    "offerings": ["Iris Offline GPT", "AI Language Translation System", "Model Builder"],
    "core_capabilities": ["Computer vision", "Natural Language Processing"],
    "technologies": ["TensorFlow"],
    "excluded_capabilities": ["Facility management", "Hardware manufacturing"],
}

MEDICAL_PROFILE = {
    "offerings": ["ICU ventilators", "Patient monitoring systems", "Biomedical equipment AMC"],
    "core_capabilities": ["Medical equipment supply", "Equipment maintenance"],
    "excluded_capabilities": ["Software development"],
}


def test_auto_keywords_are_traceable_and_cover_every_offering():
    suggestions = [
        {"keyword": "offline AI chatbot", "from": "Iris Offline GPT"},
        {"keyword": "on-premise LLM", "from": "Iris Offline GPT"},
        {"keyword": "language translation software", "from": "AI Language Translation System"},
        {"keyword": "video analytics", "from": "Computer vision"},
        {"keyword": "blockchain consulting", "from": "Blockchain"},          # not in the profile -> dropped
        {"keyword": "facility management", "from": "Computer vision"},      # excluded -> dropped
        {"keyword": "software services", "from": "Model Builder"},          # generic only -> dropped
        {"keyword": "a very long marketing phrase here", "from": "Model Builder"},  # > 4 words -> dropped
    ]
    auto = build_auto_keywords(AI_PROFILE, suggestions)
    keywords = [a["keyword"] for a in auto]
    assert keywords[:4] == ["offline AI chatbot", "on-premise LLM", "language translation software", "video analytics"]
    assert "blockchain consulting" not in keywords and "facility management" not in keywords
    assert "software services" not in keywords
    # every offering / core capability is searched; uncovered ones fall back to their own name
    sources = {a["from"] for a in auto}
    for entry in AI_PROFILE["offerings"] + AI_PROFILE["core_capabilities"]:
        assert entry in sources
    assert {"keyword": "Model Builder", "from": "Model Builder"} in auto


def test_non_it_company_gets_no_it_keywords():
    # Even if the LLM drifts towards IT terms, nothing untraceable or excluded survives.
    suggestions = [
        {"keyword": "ventilator", "from": "ICU ventilators"},
        {"keyword": "patient monitor", "from": "Patient monitoring systems"},
        {"keyword": "artificial intelligence", "from": "AI platform"},
        {"keyword": "software development", "from": "Equipment maintenance"},
    ]
    keywords = [a["keyword"].lower() for a in build_auto_keywords(MEDICAL_PROFILE, suggestions)]
    assert "ventilator" in keywords and "patient monitor" in keywords
    for keyword in keywords:
        words = keyword.split()
        assert "ai" not in words and "llm" not in words
        assert "software" not in keyword and "artificial" not in keyword and "machine learning" not in keyword


def test_old_plain_list_format_still_accepted():
    auto = build_auto_keywords(AI_PROFILE, ["computer vision", "translation", "unrelated topic"])
    keywords = [a["keyword"] for a in auto]
    assert "computer vision" in keywords
    assert "unrelated topic" not in keywords


def test_rank_keywords_productive_first_dead_last():
    stats = [
        {"keyword": "dead one", "runs": 3, "matched_bids": [], "last_run": "2026-11-10"},
        {"keyword": "winner", "runs": 2, "matched_bids": ["b1", "b2"]},
        {"keyword": "tried once", "runs": 1, "matched_bids": [], "last_run": "2026-11-16"},
    ]
    ranked = rank_keywords(["dead one", "tried once", "brand new", "winner"], stats)
    assert ranked == ["winner", "brand new", "tried once", "dead one"]


class FakeKeywordLLM:
    def __init__(self, suggestions):
        self.calls = 0
        self.suggestions = suggestions

    def suggest_search_keywords(self, company):
        self.calls += 1
        return self.suggestions


def _profile(company_id, meta, version="v1"):
    return {"company_id": company_id, "name": company_id, "status": {"processing_status": "ready"},
            "metadata_updated_at": version, "metadata": dict(meta)}


@pytest.mark.asyncio
async def test_keywords_refresh_with_profile_and_keep_own_list(fake_db):
    profiles = fake_db.get_collection("company_profiles")
    meta = dict(AI_PROFILE, tender_search_keywords=["my own term"])
    await profiles.insert_one(_profile("ai", meta))
    llm = FakeKeywordLLM([{"keyword": "offline AI chatbot", "from": "Iris Offline GPT"}])

    keywords = await collect_search_keywords(fake_db, limit=40, llm=llm, per_company=15)
    assert "my own term" in keywords and "offline AI chatbot" in keywords
    assert llm.calls == 1
    await collect_search_keywords(fake_db, limit=40, llm=llm, per_company=15)
    assert llm.calls == 1  # profile unchanged -> reused

    # Offering profile changed -> auto keywords regenerate; the company's own list is untouched.
    await profiles.update_one({"company_id": "ai"}, {"$set": {"metadata_updated_at": "v2"}})
    await collect_search_keywords(fake_db, limit=40, llm=llm, per_company=15)
    assert llm.calls == 2
    stored = await profiles.find_one({"company_id": "ai"})
    assert stored["metadata"]["tender_search_keywords"] == ["my own term"]
    assert stored["metadata"]["auto_search_keywords_version"] == "v2"


@pytest.mark.asyncio
async def test_each_company_gets_its_own_budget(fake_db):
    profiles = fake_db.get_collection("company_profiles")
    await profiles.insert_one(_profile("a", {"tender_search_keywords": [f"alpha {i}" for i in range(10)],
                                             "auto_search_keywords": [{"keyword": "x ray", "from": "x"}],
                                             "auto_search_keywords_version": "v1"}))
    await profiles.insert_one(_profile("b", {"tender_search_keywords": [f"beta {i}" for i in range(10)],
                                             "auto_search_keywords": [{"keyword": "y ray", "from": "y"}],
                                             "auto_search_keywords_version": "v1"}))
    await profiles.insert_one({"company_id": "draft", "status": {"processing_status": "failed"}, "metadata": {}})

    keywords = await collect_search_keywords(fake_db, limit=40, llm=FakeKeywordLLM([]), per_company=5)
    assert len([k for k in keywords if k.startswith("alpha")]) == 5
    assert len([k for k in keywords if k.startswith("beta")]) == 5
    assert len(keywords) == 10


@pytest.mark.asyncio
async def test_keyword_yield_is_recorded(fake_db):
    from datetime import datetime, timedelta, timezone

    profiles = fake_db.get_collection("company_profiles")
    await profiles.insert_one(_profile("ai", {
        "core_capabilities": ["machine learning platform"], "preferred_procurement_types": ["software"],
        "tender_search_keywords": ["machine learning", "printing"],
    }))
    now = datetime.now(timezone.utc)
    await fake_db.get_collection("tenders").insert_one({
        "_id": "t1", "bid_id": "GEM/1", "processed_at": "p1", "is_active": True, "created_at": now,
        "scraped_info": {"end_date": now + timedelta(days=9)}, "summary_embedding": [1.0, 0.0],
        "metadata": {"title": "Machine learning platform", "procurement_type": "software"},
    })
    from app.services.relevance_service import RelevanceService, tender_version

    tender = await fake_db.get_collection("tenders").find_one({"_id": "t1"})
    profile = await profiles.find_one({"company_id": "ai"})
    await RelevanceService(fake_db)._store("ai", tender, profile, {
        "business_relevance": "HIGH", "capability_alignment": "HIGH", "buyer_relevance": "HIGH",
    })

    await record_keyword_yield(fake_db, {"machine learning": ["GEM/1"], "printing": []})
    stats = {s["keyword"]: s for s in (await profiles.find_one({"company_id": "ai"}))["metadata"]["keyword_stats"]}
    assert stats["machine learning"]["matched_bids"] == ["GEM/1"]
    assert stats["machine learning"]["runs"] == 1
    assert stats["printing"]["matched"] == 0 and stats["printing"]["runs"] == 1

    # The end-of-scrape update refreshes "matched" without counting another run.
    await record_keyword_yield(fake_db, {"machine learning": ["GEM/1"], "printing": []}, new_run=False)
    stats = {s["keyword"]: s for s in (await profiles.find_one({"company_id": "ai"}))["metadata"]["keyword_stats"]}
    assert stats["machine learning"]["runs"] == 1 and stats["machine learning"]["matched"] == 1
