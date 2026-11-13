import pytest

from app.scraper.parser import _parse_bid_text
from app.services.matching_utils import hard_gate
from app.services.search_keywords import collect_search_keywords, keyword_matches_listing


def test_keyword_filter_drops_any_word_noise():
    assert keyword_matches_listing("artificial intelligence", "Artificial intelligence, Machi...")
    assert not keyword_matches_listing("artificial intelligence", "ARTIFICIAL FLOWER,FOAM SHEET,B...")
    # GeM truncates item text: a trailing partial word counts
    assert keyword_matches_listing("machine learning", "Artificial intelligence, Machine lea...")
    assert keyword_matches_listing("software", None)  # nothing to check -> analysis decides
    # generic words alone are not enough...
    assert not keyword_matches_listing("software development", "MS OFFICE,ANTIVIRUS,WINDOW 11,...")
    assert not keyword_matches_listing("system integration", "Music System with complete acc...")
    # ...but a specific word is, and GeM custom service bids always go to analysis
    assert keyword_matches_listing("process automation", "Laboratory Automation Services")
    assert keyword_matches_listing("data engineering", "Custom Bid for Services - IMPL...")


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


class FakeKeywordLLM:
    def __init__(self):
        self.calls = 0

    def suggest_search_keywords(self, company):
        self.calls += 1
        return ["artificial intelligence", "software development", "a very long marketing phrase about synergy"]


@pytest.mark.asyncio
async def test_keywords_come_from_profiles_and_are_generated_once(fake_db):
    profiles = fake_db.get_collection("company_profiles")
    await profiles.insert_one(
        {"company_id": "ai", "name": "AI Co", "status": {"processing_status": "ready"},
         "metadata": {"core_capabilities": ["LLM fine-tuning"]}}
    )
    await profiles.insert_one(
        {"company_id": "build", "name": "Build Co", "status": {"processing_status": "ready"},
         "metadata": {"tender_search_keywords": ["road construction", "boundary wall"]}}
    )
    await profiles.insert_one(
        {"company_id": "draft", "name": "Draft", "status": {"processing_status": "failed"}, "metadata": {}}
    )

    llm = FakeKeywordLLM()
    keywords = await collect_search_keywords(fake_db, limit=10, llm=llm)
    # interleaved across companies, long phrases dropped, failed profiles ignored
    assert keywords == ["artificial intelligence", "road construction", "software development", "boundary wall"]
    assert llm.calls == 1

    stored = await profiles.find_one({"company_id": "ai"})
    assert stored["metadata"]["tender_search_keywords"] == ["artificial intelligence", "software development"]
    await collect_search_keywords(fake_db, limit=10, llm=llm)
    assert llm.calls == 1  # reused, not regenerated
