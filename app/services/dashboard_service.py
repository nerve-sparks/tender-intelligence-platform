"""
Dashboard aggregation service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.services.match_service import MatchService


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DashboardService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db
        self.tenders = db.get_collection("tenders")
        self.companies = db.get_collection("company_profiles")
        self.searches = db.get_collection("search_history")
        self.jobs = db.get_collection("scrape_logs")
        self.actions = db.get_collection("tender_actions")
        self.match_service = MatchService(db)

    async def get_stats(self, owner_user_id: str, company_id: str | None = None) -> Dict[str, Any]:
        profile = None
        if company_id:
            profile = await self.companies.find_one({"company_id": company_id, "owner_user_id": owner_user_id})
        if not profile:
            profile = await self.companies.find_one({"owner_user_id": owner_user_id}, sort=[("updated_at", -1)])

        active_company_id = profile.get("company_id") if profile else None
        company_name = (profile or {}).get("name") or "Company"

        tenders_analyzed = await self.tenders.count_documents({"status.llm_processed": True, "expired": False})

        best_tenders_found = 0
        if active_company_id:
            matches = await self.match_service.get_company_matches(
                company_id=active_company_id,
                owner_user_id=owner_user_id,
                q=None,
                time_period="30d",
                sort="best_match",
                min_score=settings.MATCH_MIN_SCORE,
                page=1,
                limit=500,
            )
            best_tenders_found = matches.get("total", 0)

        saved_count = 0
        applied_count = 0
        if active_company_id:
            saved_count = await self.actions.count_documents({"company_id": active_company_id, "action": "saved"})
            applied_count = await self.actions.count_documents({"company_id": active_company_id, "action": "applied"})

        seven_days_ago = utcnow() - timedelta(days=7)
        gathering = await self.tenders.count_documents({"scraped_at": {"$gte": seven_days_ago}})
        analyzed = await self.tenders.count_documents({"processed_at": {"$gte": seven_days_ago}})
        saved_recent = await self.actions.count_documents(
            {"company_id": active_company_id, "action": "saved", "updated_at": {"$gte": seven_days_ago}}
        ) if active_company_id else 0
        applied_recent = await self.actions.count_documents(
            {"company_id": active_company_id, "action": "applied", "updated_at": {"$gte": seven_days_ago}}
        ) if active_company_id else 0

        return {
            "company_id": active_company_id,
            "company_name": company_name,
            "totals": {
                "tenders_analyzed": tenders_analyzed,
                "best_tenders_found": best_tenders_found,
                "tenders_saved": saved_count,
                "tenders_applied": applied_count,
            },
            "overview_last_7_days": {
                "gathering": gathering,
                "analyzed": analyzed,
                "saved": saved_recent,
                "applied": applied_recent,
            },
        }

    async def get_report(
        self,
        owner_user_id: str,
        company_id: str | None,
        range_key: str,
    ) -> Dict[str, Any]:
        profile = None
        if company_id:
            profile = await self.companies.find_one({"company_id": company_id, "owner_user_id": owner_user_id})
        if not profile:
            profile = await self.companies.find_one({"owner_user_id": owner_user_id}, sort=[("updated_at", -1)])

        company = profile.get("company_id") if profile else None
        labels, bucket_starts = self._build_buckets(range_key)

        gathering_series: List[int] = []
        analyzed_series: List[int] = []
        saved_series: List[int] = []

        for i, start in enumerate(bucket_starts):
            end = bucket_starts[i + 1] if i + 1 < len(bucket_starts) else utcnow() + timedelta(seconds=1)

            gathering = await self.tenders.count_documents({"scraped_at": {"$gte": start, "$lt": end}})
            analyzed = await self.tenders.count_documents({"processed_at": {"$gte": start, "$lt": end}})
            if company:
                saved = await self.actions.count_documents(
                    {
                        "company_id": company,
                        "action": "saved",
                        "updated_at": {"$gte": start, "$lt": end},
                    }
                )
            else:
                saved = 0

            gathering_series.append(gathering)
            analyzed_series.append(analyzed)
            saved_series.append(saved)

        return {
            "range": range_key,
            "labels": labels,
            "series": {
                "gathering": gathering_series,
                "analyze": analyzed_series,
                "saved": saved_series,
            },
        }

    async def get_activity(self, limit: int = 10) -> List[Dict[str, Any]]:
        activity: List[Dict[str, Any]] = []

        async for tender in self.tenders.find().sort("created_at", -1).limit(limit):
            activity.append(
                {
                    "type": "tender",
                    "message": f"Tender scraped: {tender.get('bid_id')}",
                    "timestamp": tender.get("scraped_at") or tender.get("created_at"),
                    "data": {"bid_id": tender.get("bid_id")},
                }
            )

        async for job in self.jobs.find().sort("started_at", -1).limit(limit):
            activity.append(
                {
                    "type": "job",
                    "message": f"Job {job.get('job_type')} {job.get('status')}",
                    "timestamp": job.get("completed_at") or job.get("started_at"),
                    "data": {"job_id": job.get("job_id")},
                }
            )

        activity = [item for item in activity if item.get("timestamp")]
        activity.sort(key=lambda item: item["timestamp"], reverse=True)
        return activity[:limit]

    async def get_queue(self, limit: int = 20) -> List[Dict[str, Any]]:
        cursor = self.tenders.find({"status.llm_processed": False}).sort("created_at", -1).limit(limit)
        items: List[Dict[str, Any]] = []
        async for tender in cursor:
            items.append(
                {
                    "bid_id": tender.get("bid_id"),
                    "status": tender.get("status"),
                    "scraped_info": tender.get("scraped_info"),
                    "created_at": tender.get("created_at"),
                }
            )
        return items

    def _build_buckets(self, range_key: str) -> Tuple[List[str], List[datetime]]:
        now = utcnow()
        normalized = (range_key or "12m").lower()

        if normalized == "7d":
            starts = [(now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0) for i in range(6, -1, -1)]
            labels = [start.strftime("%d %b") for start in starts]
            return labels, starts

        if normalized == "10d":
            starts = [(now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0) for i in range(9, -1, -1)]
            labels = [start.strftime("%d %b") for start in starts]
            return labels, starts

        if normalized == "6m":
            starts = []
            labels = []
            for i in range(5, -1, -1):
                month = (now.replace(day=1) - timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                starts.append(month)
                labels.append(month.strftime("%b"))
            return labels, starts

        starts = []
        labels = []
        for i in range(11, -1, -1):
            month = (now.replace(day=1) - timedelta(days=30 * i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            starts.append(month)
            labels.append(month.strftime("%b"))
        return labels, starts
