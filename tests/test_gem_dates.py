"""
GeM closing dates: read from the bid PDF, keep the listing's later (extended) deadline, treat Reverse
Auction cards as closed, compare in India time, and order matches by band then most time left.
"""

from datetime import datetime, timedelta

import pytest

from app.processors.gem_bid_fields import parse_gem_bid_dates, parse_gem_bid_fields
from app.services.match_service import match_order_key
from app.services.share_service import tender_email_entry
from app.services.tender_service import TenderService, is_reverse_auction, update_expired_flags
from app.utils.gem_dates import days_left, extended_from, gem_now

# Text as PyMuPDF extracts it from a real GeM bid PDF (GEM/2026/B/8135269).
PDF_TEXT = """बड ववरण/Bid Details
बड बंद होने क तारख/समय /Bid End Date/Time 04-11-2026 12:00:00
बड खुलने क तारख/समय /Bid Opening
Date/Time
05-11-2026 12:00:00
बड पेशकश वैधता (बंद होने क तारख से) /Bid Offer
Validity (From End Date)
180 (Days)
बड सं<या/Bid Number
: GEM/2026/B/8135269
=दनांक /Dated: 14-10-2026
"""


def test_pdf_dates_are_read_exactly():
    dates = parse_gem_bid_dates(PDF_TEXT)
    assert dates == {
        "pdf_bid_end_date": datetime(2026, 11, 4, 12, 0),
        "pdf_bid_opening_date": datetime(2026, 11, 5, 12, 0),
        "pdf_bid_published_date": datetime(2026, 10, 14),
    }
    assert parse_gem_bid_fields(PDF_TEXT)["pdf_bid_end_date"] == datetime(2026, 11, 4, 12, 0)
    assert parse_gem_bid_dates("no dates here") == {
        "pdf_bid_end_date": None, "pdf_bid_opening_date": None, "pdf_bid_published_date": None,
    }


def test_extended_deadline_keeps_listing_date_and_notes_the_original():
    now = gem_now()
    tender = {
        "bid_id": "GEM/2026/B/8135269",
        "scraped_info": {"end_date": now + timedelta(days=12, hours=1)},
        "metadata": {"title": "WhatsApp Chatbot", "pdf_bid_end_date": now - timedelta(days=1)},
    }
    assert extended_from(tender) == tender["metadata"]["pdf_bid_end_date"]
    tender["metadata"]["pdf_bid_opening_date"] = datetime(2026, 8, 25, 14, 30)
    entry = tender_email_entry(tender, None)
    assert "(12 days left)" in entry["close_date"]
    assert "extended by the buyer" in entry["close_date"]
    assert entry["pdf_dates"].startswith("closes ")
    assert "opens 25 Aug 2026, 2:30 PM" in entry["pdf_dates"]
    # Not extended -> no note
    tender["metadata"]["pdf_bid_end_date"] = tender["scraped_info"]["end_date"]
    assert extended_from(tender) is None
    assert "extended" not in tender_email_entry(tender, None)["close_date"]


def test_days_left_uses_india_time():
    # 3 hours from now in India time is still today: 0 days left, not closed.
    assert days_left(gem_now() + timedelta(hours=3)) == 0
    assert days_left(gem_now() + timedelta(days=5, hours=1)) == 5
    assert days_left(gem_now() - timedelta(hours=1)) == 0


def test_reverse_auction_detection():
    assert is_reverse_auction({"bid_id": "GEM/2026/B/7999797", "ra_no": "GEM/2026/R/769079"})
    assert is_reverse_auction({"bid_id": "GEM/2026/R/770419"})
    assert not is_reverse_auction({"bid_id": "GEM/2026/B/8224484", "ra_no": None})


@pytest.mark.asyncio
async def test_reverse_auction_card_is_closed_and_keeps_bid_dates(fake_db):
    service = TenderService(fake_db)
    tenders = fake_db.get_collection("tenders")
    pdf_end = gem_now() - timedelta(days=40)
    await tenders.insert_one({"bid_id": "GEM/2026/B/7999797", "status": {"llm_processed": True},
                              "metadata": {"pdf_bid_end_date": pdf_end}})
    ra_end = gem_now() + timedelta(days=1)
    await service.create_or_update_from_scrape({
        "bid_id": "GEM/2026/B/7999797", "ra_no": "GEM/2026/R/769079",
        "start_date": gem_now() - timedelta(days=1), "end_date": ra_end,
    })
    stored = await tenders.find_one({"bid_id": "GEM/2026/B/7999797"})
    assert stored["expired"] is True
    assert stored["scraped_info"]["end_date"] == pdf_end
    assert stored["scraped_info"]["ra_end_date"] == ra_end
    assert stored["scraped_info"]["in_reverse_auction"] is True


@pytest.mark.asyncio
async def test_expired_flags_follow_india_time_and_extensions(fake_db):
    tenders = fake_db.get_collection("tenders")
    now = gem_now()
    await tenders.insert_one({"bid_id": "closed", "expired": False, "scraped_info": {"end_date": now - timedelta(hours=1)}})
    await tenders.insert_one({"bid_id": "open", "expired": False, "scraped_info": {"end_date": now + timedelta(hours=2)}})
    await tenders.insert_one({"bid_id": "extended", "expired": True, "scraped_info": {"end_date": now + timedelta(days=5)}})
    await tenders.insert_one({"bid_id": "ra", "expired": False,
                              "scraped_info": {"end_date": now + timedelta(days=5), "in_reverse_auction": True}})
    await update_expired_flags(fake_db)
    state = {t["bid_id"]: t["expired"] async for t in tenders.find({})}
    assert state == {"closed": True, "open": False, "extended": False, "ra": True}


def test_match_order_band_then_score():
    now = gem_now()

    def tender(days):
        return {"scraped_info": {"end_date": now + timedelta(days=days)}}

    items = [
        ("pursue_soon", 0.95, tender(1)),     # closes within 2 days -> bottom of its band
        ("pursue_a", 0.82, tender(5)),
        ("pursue_b", 0.88, tender(25)),
        ("tie_later", 0.82, tender(20)),      # same score -> later closing date first
        ("consider_low", 0.74, tender(40)),
        ("consider_high", 0.79, tender(4)),
    ]
    ordered = [name for name, score, t in sorted(items, key=lambda i: match_order_key(i[1], i[2]))]
    assert ordered == ["pursue_b", "tie_later", "pursue_a", "pursue_soon", "consider_high", "consider_low"]
