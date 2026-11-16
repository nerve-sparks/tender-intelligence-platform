"""
Job monitoring and triggers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.jobs.scrape_job import run_process_job, run_scrape_job
from app.jobs.scheduler import scheduler
from app.services.socket_manager import manager


class JobService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.collection = db.get_collection("scrape_logs")

    async def list_jobs(
        self,
        skip: int = 0,
        limit: int = 20,
        job_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}
        if job_type:
            filters["job_type"] = job_type
        if status:
            filters["status"] = status

        total = await self.collection.count_documents(filters)
        cursor = self.collection.find(filters).sort("started_at", -1).skip(skip).limit(limit)
        items = []
        async for job in cursor:
            items.append(self._serialize(job))
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = await self.collection.find_one({"job_id": job_id})
        return self._serialize(job) if job else None

    async def trigger_scrape(self) -> Dict[str, Any]:
        from app.jobs.scrape_job import is_scrape_running

        if is_scrape_running():
            return {"triggered": False, "already_running": True, "job_type": "scrape"}
        asyncio.create_task(run_scrape_job())
        await manager.broadcast(
            {
                "type": "JOB_PROGRESS",
                "job": "SCRAPE_TENDERS",
                "job_id": "",
                "status": "started",
                "progress": {"current": 0, "total": 1, "percent": 0},
                "message": "Scrape job triggered",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"triggered": True, "job_type": "scrape"}

    async def trigger_process(self) -> Dict[str, Any]:
        asyncio.create_task(run_process_job())
        await manager.broadcast(
            {
                "type": "JOB_PROGRESS",
                "job": "PROCESS_TENDERS",
                "job_id": "",
                "status": "started",
                "progress": {"current": 0, "total": 1, "percent": 0},
                "message": "Process job triggered",
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"triggered": True, "job_type": "process"}

    def scheduler_status(self) -> Dict[str, Any]:
        return {"running": scheduler.running}

    def _serialize(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job.get("_id")
        if job_id is not None:
            job["id"] = str(job_id)
            job["_id"] = str(job_id)
        return job
