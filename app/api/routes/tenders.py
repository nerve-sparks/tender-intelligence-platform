"""
Tender API routes.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.dependencies import get_current_user, get_db
from app.services.ai_chat_service import AIChatService
from app.services.email_service import EmailService
from app.services.match_service import MatchService
from app.services.search_service import SearchService
from app.services.tender_service import TenderService

router = APIRouter(prefix="/tenders", tags=["tenders"])


class TenderActionRequest(BaseModel):
    company_id: str
    action: Optional[str] = Field(default=None)


class AIChatRequest(BaseModel):
    company_id: str
    message: str
    chat_id: Optional[str] = None


class ShareRequest(BaseModel):
    company_id: str
    recipients: List[str] = Field(default_factory=list)
    note: Optional[str] = None


@router.get("/", response_model=Dict[str, Any])
async def list_tenders(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    domains: Optional[str] = Query(None, description="Comma-separated domains"),
    search: Optional[str] = None,
    expired: Optional[bool] = None,
    sort_by: str = "created_at",
    sort_dir: str = "desc",
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = TenderService(db)
    domain_list = [item.strip() for item in domains.split(",") if item.strip()] if domains else None
    return await service.list_tenders(
        skip=skip,
        limit=limit,
        status=status,
        domains=domain_list,
        search=search,
        expired=expired,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get("/stats/summary", response_model=Dict[str, Any])
async def tender_stats(
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = TenderService(db)
    return await service.get_stats()


@router.get("/{tender_id}", response_model=Dict[str, Any])
async def get_tender(
    tender_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = TenderService(db)
    tender = await service.get_tender(tender_id)
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")
    return tender


@router.get("/{tender_id}/download")
async def download_tender_document(
    tender_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = TenderService(db)
    document = await service.get_download(tender_id)
    if not document or not os.path.exists(document["path"]):
        raise HTTPException(status_code=404, detail="Tender document unavailable on GeM")
    return FileResponse(path=document["path"], filename=document["filename"], media_type="application/pdf")


@router.post("/{tender_id}/reprocess", response_model=Dict[str, Any])
async def reprocess_tender(
    tender_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = TenderService(db)
    success = await service.reprocess_tender(tender_id)
    if not success:
        raise HTTPException(status_code=404, detail="Tender not found")
    return {"reprocessed": True}


@router.get("/{tender_id}/matches", response_model=Dict[str, Any])
async def match_companies(
    tender_id: str,
    limit: int = 5,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = SearchService(db)
    try:
        return await service.match_companies_for_tender(tender_id=tender_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{tender_id}/action", response_model=Dict[str, Any])
async def update_tender_action(
    tender_id: str,
    payload: TenderActionRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    profile = await db.get_collection("company_profiles").find_one(
        {"company_id": payload.company_id, "owner_user_id": current_user["id"]}
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Company profile not found")

    service = MatchService(db)
    try:
        return await service.update_action(
            company_id=payload.company_id,
            user_id=current_user["id"],
            tender_id=tender_id,
            action=payload.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{tender_id}/ai/suggestions", response_model=Dict[str, Any])
async def tender_ai_suggestions(
    tender_id: str,
    _db: AsyncIOMotorDatabase = Depends(get_db),
    _current_user: Dict[str, Any] = Depends(get_current_user),
):
    service = AIChatService(_db)
    return {"tender_id": tender_id, "items": service.get_suggestions()}


@router.post("/{tender_id}/ai/chat", response_model=Dict[str, Any])
async def tender_ai_chat(
    tender_id: str,
    payload: AIChatRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    profile = await db.get_collection("company_profiles").find_one(
        {"company_id": payload.company_id, "owner_user_id": current_user["id"]}
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Company profile not found")

    service = AIChatService(db)
    try:
        return await service.chat(
            tender_id=tender_id,
            company_id=payload.company_id,
            owner_user_id=current_user["id"],
            message=payload.message,
            chat_id=payload.chat_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{tender_id}/share", response_model=Dict[str, Any])
async def share_tender_by_email(
    tender_id: str,
    payload: ShareRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    profile = await db.get_collection("company_profiles").find_one(
        {"company_id": payload.company_id, "owner_user_id": current_user["id"]}
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Company profile not found")

    try:
        object_id = ObjectId(tender_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Tender not found")

    tender = await db.get_collection("tenders").find_one({"_id": object_id})
    if not tender:
        raise HTTPException(status_code=404, detail="Tender not found")

    action = await db.get_collection("tender_actions").find_one(
        {"company_id": payload.company_id, "tender_id": tender_id}
    )

    tender_meta = tender.get("metadata") or {}
    tender_info = {
        "id": tender_id,
        "title": tender_meta.get("title") or (tender.get("scraped_info") or {}).get("items"),
        "department": tender_meta.get("department") or (tender.get("scraped_info") or {}).get("department"),
        "location": tender_meta.get("location"),
        "bid_id": tender.get("bid_id"),
        "gem_url": tender.get("gem_url"),
        "summary": tender_meta.get("summary"),
        "published_date": (tender.get("scraped_info") or {}).get("start_date"),
        "close_date": (tender.get("scraped_info") or {}).get("end_date"),
    }

    match_percent = 80
    if action and action.get("action") in {"saved", "applied"}:
        match_percent = 90

    email_service = EmailService()
    result = await email_service.send_tender_share(
        recipients=payload.recipients,
        tender=tender_info,
        match_percent=match_percent,
        note=payload.note,
    )

    await db.get_collection("share_logs").insert_one(
        {
            "company_id": payload.company_id,
            "tender_id": tender_id,
            "sent_to": payload.recipients,
            "status": "sent" if result.get("sent") else "failed",
            "provider": result.get("provider"),
            "error": result.get("error"),
            "created_at": datetime.now(timezone.utc),
        }
    )

    return {"sent": bool(result.get("sent")), "count": result.get("count", 0)}
