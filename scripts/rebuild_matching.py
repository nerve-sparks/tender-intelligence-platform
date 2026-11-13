"""
One-off migration for business-relevance matching.

  1. Re-extract every processed tender with the new tender-brief prompt
     (procurement type, scope categories, key dates, bidder criteria...).
  2. Re-run offering extraction for every company profile
     (core capabilities, preferred procurement types, exclusions, turnover...).
  3. Compute LLM relevance judgments for every ready company.

Usage:
    python -m scripts.rebuild_matching                 # everything
    python -m scripts.rebuild_matching --skip-tenders  # only companies + matches
    python -m scripts.rebuild_matching --matches-only
    python -m scripts.rebuild_matching --gem-fields      # backfill GeM item category / relaxations + alerts (no LLM)
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import settings
from app.database.mongodb import create_indexes, get_database
from app.processors.llm_extractor import TENDER_EXTRACTION_VERSION
from app.services.company_service import CompanyService
from app.services.relevance_service import RelevanceService
from app.services.tender_service import TenderService


async def reextract_tenders(db) -> None:
    service = TenderService(db)
    cursor = db.get_collection("tenders").find(
        {"status.pdf_downloaded": True, "expired": False},
        {"_id": 1, "bid_id": 1, "metadata_version": 1},
    )
    tenders = [t async for t in cursor if t.get("metadata_version") != TENDER_EXTRACTION_VERSION]
    print(f"Re-extracting {len(tenders)} tenders")
    for index, tender in enumerate(tenders, start=1):
        ok = await service.reprocess_tender(str(tender["_id"]))
        print(f"  [{index}/{len(tenders)}] {tender.get('bid_id')}: {'ok' if ok else 'failed'}")


async def reprocess_companies(db) -> None:
    service = CompanyService(db)
    cursor = db.get_collection("company_profiles").find({"status.processing_status": "ready"})
    profiles = [p async for p in cursor]
    print(f"Re-extracting {len(profiles)} company profiles")
    for profile in profiles:
        await service._process_company_profile(
            company_id=profile["company_id"],
            owner_user_id=profile["owner_user_id"],
            job_id="rebuild-matching",
        )
        print(f"  {profile.get('name')}: done")


async def backfill_gem_fields(db) -> None:
    """Read standard GeM fields (item category, Startup/MSE relaxations) from already downloaded PDFs."""
    import os

    from app.processors.gem_bid_fields import parse_gem_bid_fields
    from app.processors.pdf_extractor import PDFExtractor

    extractor = PDFExtractor()
    cursor = db.get_collection("tenders").find({"status.llm_processed": True}, {"_id": 1, "pdf_local_path": 1})
    updated = 0
    async for tender in cursor:
        path = tender.get("pdf_local_path")
        if not path or not os.path.exists(path):
            continue
        fields = parse_gem_bid_fields(extractor.extract_text(path) or "")
        update = {f"metadata.{k}": v for k, v in fields.items() if v}
        if update:
            await db.get_collection("tenders").update_one({"_id": tender["_id"]}, {"$set": update})
            updated += 1
    print(f"GeM fields backfilled on {updated} tenders")

    service = RelevanceService(db)
    cursor = db.get_collection("company_profiles").find({"status.processing_status": "ready"})
    async for profile in cursor:
        alerts = await service.alerts_for_existing(profile["company_id"])
        print(f"  {profile.get('name')}: {alerts} new match alerts")


async def main(skip_tenders: bool, matches_only: bool, gem_fields: bool = False) -> None:
    settings.MATCH_AUTO_REFRESH = False  # matches are rebuilt explicitly below
    db = get_database()
    await create_indexes(db)
    if gem_fields:
        await backfill_gem_fields(db)
        return
    if not matches_only:
        if not skip_tenders:
            await reextract_tenders(db)
        await reprocess_companies(db)
    results = await RelevanceService(db).refresh_all_companies()
    for company_id, stats in results.items():
        print(f"Matches for {company_id}: {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tenders", action="store_true")
    parser.add_argument("--matches-only", action="store_true")
    parser.add_argument("--gem-fields", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(skip_tenders=args.skip_tenders, matches_only=args.matches_only, gem_fields=args.gem_fields))
