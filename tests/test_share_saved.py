"""
Emailing all saved tenders at once: one digest, real match %, bid PDFs attached up to the size limit.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.config import settings
from app.services.email_service import EmailService
from app.services.share_service import ShareService, tender_email_entry


class CapturingEmail(EmailService):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def _send(self, recipients, subject, body, attachments):
        self.sent.append({"recipients": recipients, "subject": subject, "body": body, "attachments": attachments})
        return {"sent": True, "provider": "test", "count": len(recipients)}


class FakeTenders:
    """Stands in for TenderService.get_download: a local PDF per tender (None = unavailable)."""

    def __init__(self, files):
        self.files = files

    async def get_download(self, tender_id):
        path = self.files.get(tender_id)
        return {"path": path, "filename": "x.pdf"} if path else None


def _tender(oid, bid, title, days):
    now = datetime.now(timezone.utc)
    return {
        "_id": oid, "bid_id": bid, "is_active": True, "expired": False, "created_at": now,
        "scraped_info": {"end_date": now + timedelta(days=days, hours=1), "start_date": now - timedelta(days=5)},
        "metadata": {"title": title, "buyer": "Oil India Limited", "procurement_type": "software",
                     "emd_amount": "₹70,20,000", "what_is_being_bought": title},
    }


async def _seed(fake_db, tmp_path):
    await fake_db.get_collection("company_profiles").insert_one(
        {"company_id": "c1", "owner_user_id": "u1", "name": "Nervesparks", "experience_years": 5,
         "metadata_updated_at": "v1", "metadata": {"core_capabilities": ["machine learning"],
                                                   "preferred_procurement_types": ["software"]}}
    )
    ids = [ObjectId(), ObjectId(), ObjectId()]
    tenders = fake_db.get_collection("tenders")
    await tenders.insert_one(_tender(ids[0], "GEM/2026/B/1", "AI platform", days=20))
    await tenders.insert_one(_tender(ids[1], "GEM/2026/B/2", "Chatbot development", days=3))
    await tenders.insert_one(_tender(ids[2], "GEM/2026/B/3", "Data analytics", days=10))
    actions = fake_db.get_collection("tender_actions")
    for oid in ids:
        await actions.insert_one({"company_id": "c1", "tender_id": str(oid), "action": "saved",
                                  "updated_at": datetime.now(timezone.utc)})

    files = {}
    for index, oid in enumerate(ids):
        path = tmp_path / f"{index}.pdf"
        path.write_bytes(b"%PDF" + b"0" * 600_000)  # ~0.6 MB each
        files[str(oid)] = str(path)
    return ids, files


@pytest.mark.asyncio
async def test_share_saved_sends_one_digest_with_pdfs(fake_db, tmp_path, monkeypatch):
    ids, files = await _seed(fake_db, tmp_path)
    monkeypatch.setattr(settings, "EMAIL_MAX_ATTACHMENT_MB", 1.5)  # room for 2 of the 3 PDFs
    email = CapturingEmail()

    result = await ShareService(fake_db, email=email, tenders=FakeTenders(files)).share_saved(
        "c1", "u1", ["a@example.com", "b@example.com"]
    )

    assert result == {"sent": True, "count": 2, "tenders": 3, "attached": 2, "link_only": 1}
    assert len(email.sent) == 1
    message = email.sent[0]
    assert message["subject"] == "3 saved GeM tenders - Nervesparks"
    # Same order as the match list: equal scores -> the latest closing date (most time to bid) first.
    assert [name for name, _ in message["attachments"]] == ["GEM_2026_B_1.pdf", "GEM_2026_B_3.pdf"]
    body = message["body"]
    assert body.index("AI platform") < body.index("Data analytics") < body.index("Chatbot development")
    assert "EMD: ₹70,20,000" in body
    assert "Match: " in body
    assert "not attached (email size limit)" in body
    assert "Open in app" not in body
    assert await fake_db.get_collection("share_logs").count_documents({"company_id": "c1", "bulk": True}) == 3


@pytest.mark.asyncio
async def test_share_saved_without_saved_tenders(fake_db):
    await fake_db.get_collection("company_profiles").insert_one(
        {"company_id": "c1", "owner_user_id": "u1", "name": "X", "metadata": {}}
    )
    with pytest.raises(ValueError):
        await ShareService(fake_db, email=CapturingEmail(), tenders=FakeTenders({})).share_saved("c1", "u1", ["a@b.co"])


@pytest.mark.asyncio
async def test_share_saved_other_users_company(fake_db, tmp_path):
    await _seed(fake_db, tmp_path)
    with pytest.raises(LookupError):
        await ShareService(fake_db, email=CapturingEmail(), tenders=FakeTenders({})).share_saved("c1", "intruder", ["a@b.co"])


def test_email_entry_uses_real_match():
    tender = _tender(ObjectId(), "GEM/2026/B/9", "AI/ML services", days=10)
    match = {"score": 0.92, "verified": True, "recommendation": "PURSUE",
             "why_matches": ["Core AI capability"], "gaps": ["On-site staff in Port Blair"]}
    entry = tender_email_entry(tender, match)
    assert entry["match_percent"] == 92
    assert entry["recommendation"] == "Pursue"
    assert entry["why"] == "Core AI capability"
    assert entry["gap"] == "On-site staff in Port Blair"
    assert "10 days left" in entry["close_date"]


def test_smtp_message_carries_attachments(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSMTP)
    result = EmailService()._send_with_smtp(["a@b.co"], "s", "body", [("GEM_2026_B_1.pdf", b"%PDF-1")])
    assert result["sent"] is True
    names = [part.get_filename() for part in sent["msg"].iter_attachments()]
    assert names == ["GEM_2026_B_1.pdf"]
