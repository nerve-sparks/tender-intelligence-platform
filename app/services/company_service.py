"""
Company profile onboarding and processing service.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiofiles
from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.database.repositories.company_repo import CompanyRepository
from app.processors.document_extractor import DocumentExtractor, is_extractable_file
from app.processors.embedder import TextEmbedder
from app.processors.llm_extractor import LLMExtractor
from app.services.auth_service import AuthService
from app.services.company_website_scraper import CompanyWebsiteScraper
from app.services.relevance_service import schedule_refresh
from app.services.socket_manager import manager
from app.utils.helpers import ensure_dir, safe_filename, sha256_file
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Offering-profile fields the user may edit after extraction.
OFFERING_LIST_FIELDS = [
    "core_capabilities",
    "offerings",
    "capabilities",
    "technologies",
    "domains",
    "target_sectors",
    "preferred_procurement_types",
    "excluded_capabilities",
    "certifications",
    "registrations",
    "tender_search_keywords",
]
# Fields that only steer GeM searching, not matching - editing them keeps cached match judgments.
SEARCH_ONLY_FIELDS = {"tender_search_keywords"}
# Fields used only by the deterministic eligibility check (computed live), not by the LLM judgment.
ELIGIBILITY_ONLY_FIELDS = {"annual_turnover", "annual_turnover_inr", "is_dpiit_startup", "is_mse"}
OFFERING_FIELDS = set(OFFERING_LIST_FIELDS) | {
    "business_type", "summary", "annual_turnover", "annual_turnover_inr", "is_dpiit_startup", "is_mse",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CompanyService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db
        self.repo = CompanyRepository(db)
        self.document_extractor = DocumentExtractor()
        self.embedder = TextEmbedder()
        self.website_scraper = CompanyWebsiteScraper()
        self.auth_service = AuthService(db)
        self._llm: Optional[LLMExtractor] = None

    def _get_llm(self) -> LLMExtractor:
        if self._llm is None:
            self._llm = LLMExtractor()
        return self._llm

    async def create_company(self, owner_user_id: str, name: str, company_url: str, experience_years: int) -> Dict[str, Any]:
        if not name.strip():
            raise ValueError("Company name is required")
        if not company_url.strip():
            raise ValueError("Company URL is required")
        parsed_url = urlparse(company_url.strip() if "://" in company_url else f"https://{company_url.strip()}")
        if not parsed_url.netloc:
            raise ValueError("Company URL is invalid")
        if experience_years < 0 or experience_years > 100:
            raise ValueError("Experience must be between 0 and 100")

        company_id = str(uuid.uuid4())
        normalized_url = company_url.strip() if "://" in company_url else f"https://{company_url.strip()}"

        profile = {
            "company_id": company_id,
            "owner_user_id": owner_user_id,
            "name": name.strip(),
            "company_url": normalized_url,
            "experience_years": int(experience_years),
            "interest_tags": [],
            "website_scrape": {
                "status": "pending",
                "pages": [],
                "extracted_text": "",
                "last_error": None,
            },
            "uploaded_files": [],
            "metadata": {},
            "summary_embedding": [],
            "status": {
                "processing_status": "pending",
                "files_processed": 0,
                "total_files": 0,
                "last_error": None,
            },
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await self.repo.create(profile)
        await self.auth_service.set_user_company(owner_user_id, company_id)
        return self._serialize(profile)

    async def scrape_website(self, company_id: str, owner_user_id: str) -> Dict[str, Any]:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")

        company_url = profile.get("company_url")
        result = await self.website_scraper.scrape(company_url)

        await self.repo.update(
            company_id,
            {
                "website_scrape": result,
                "updated_at": utcnow(),
            },
        )
        return result

    async def upload_documents(
        self,
        company_id: str,
        owner_user_id: str,
        files: List[UploadFile],
    ) -> Dict[str, Any]:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")
        if not files:
            raise ValueError("No files provided")

        company_dir = os.path.join(settings.PROFILE_UPLOAD_DIR, company_id)
        ensure_dir(company_dir)

        existing_files = profile.get("uploaded_files", [])
        existing_hashes = {item.get("file_hash") for item in existing_files}
        current_total_size = sum(int(item.get("size_bytes") or 0) for item in existing_files)

        uploaded_items: List[Dict[str, Any]] = []
        for index, file in enumerate(files, start=1):
            if not file.filename:
                continue

            ext = os.path.splitext(file.filename.lower())[1]
            safe_name = safe_filename(file.filename, default=f"file_{index}{ext or ''}")
            local_path = os.path.join(company_dir, safe_name)

            file_size = 0
            async with aiofiles.open(local_path, "wb") as handle:
                while True:
                    chunk = await file.read(8192)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    if file_size > settings.MAX_UPLOAD_SIZE:
                        raise ValueError(f"File {file.filename} exceeds max upload size")
                    if current_total_size + file_size > settings.MAX_ONBOARDING_TOTAL_SIZE:
                        raise ValueError("Total upload size exceeds allowed onboarding limit")
                    await handle.write(chunk)

            file_hash = sha256_file(local_path)
            if file_hash in existing_hashes:
                os.remove(local_path)
                uploaded_items.append(
                    {
                        "file_hash": file_hash,
                        "original_name": file.filename,
                        "status": "already_exists",
                    }
                )
                continue

            extracted_text = None
            extract_status = "stored_only"
            if is_extractable_file(file.filename):
                extracted_text = self.document_extractor.extract_text(local_path)
                extract_status = "extracted" if extracted_text else "failed"

            item = {
                "file_hash": file_hash,
                "original_name": file.filename,
                "local_path": local_path,
                "mime_type": file.content_type,
                "size_bytes": file_size,
                "uploaded_at": utcnow(),
                "extract_status": extract_status,
                "extracted_text_len": len(extracted_text or ""),
                "extracted_text": extracted_text,
            }
            existing_files.append(item)
            existing_hashes.add(file_hash)
            current_total_size += file_size

            uploaded_items.append(
                {
                    "file_hash": file_hash,
                    "original_name": file.filename,
                    "status": extract_status,
                    "size_bytes": file_size,
                    "extract_status": extract_status,
                    "extracted_text_len": len(extracted_text or ""),
                }
            )

            await self._broadcast_progress(
                job="PROCESS_COMPANY",
                job_id=company_id,
                status="progress",
                current=len(uploaded_items),
                total=len(files),
                message=f"Uploaded {file.filename}",
            )

        files_processed = len([item for item in existing_files if item.get("extract_status") == "extracted"])
        status = profile.get("status") or {}
        status.update(
            {
                "files_processed": files_processed,
                "total_files": len(existing_files),
            }
        )

        await self.repo.update(
            company_id,
            {
                "uploaded_files": existing_files,
                "status": status,
                "updated_at": utcnow(),
            },
        )

        return {
            "company_id": company_id,
            "files": uploaded_items,
            "total_files": len(existing_files),
            "uploaded": len(uploaded_items),
        }

    async def remove_document(self, company_id: str, owner_user_id: str, file_hash: str) -> bool:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")

        files = profile.get("uploaded_files", [])
        target = next((item for item in files if item.get("file_hash") == file_hash), None)
        if not target:
            return False

        path = target.get("local_path")
        if path and os.path.exists(path):
            os.remove(path)

        updated = [item for item in files if item.get("file_hash") != file_hash]
        status = profile.get("status") or {}
        status["total_files"] = len(updated)
        status["files_processed"] = len([item for item in updated if item.get("extract_status") == "extracted"])

        await self.repo.update(
            company_id,
            {
                "uploaded_files": updated,
                "status": status,
                "updated_at": utcnow(),
            },
        )
        return True

    async def set_interests(self, company_id: str, owner_user_id: str, interest_tags: List[str]) -> Dict[str, Any]:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")

        cleaned = [tag.strip() for tag in interest_tags if tag and tag.strip()]
        await self.repo.update(
            company_id,
            {
                "interest_tags": cleaned,
                "updated_at": utcnow(),
            },
        )
        profile["interest_tags"] = cleaned
        return self._serialize(profile)

    async def start_processing(self, company_id: str, owner_user_id: str) -> Dict[str, Any]:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")

        job_id = str(uuid.uuid4())
        await self.repo.update(
            company_id,
            {
                "status": {
                    **(profile.get("status") or {}),
                    "processing_status": "processing",
                    "last_error": None,
                },
            },
        )

        asyncio.create_task(self._process_company_profile(company_id=company_id, owner_user_id=owner_user_id, job_id=job_id))
        return {"status": "processing", "job_id": job_id}

    async def _process_company_profile(self, company_id: str, owner_user_id: str, job_id: str) -> None:
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            return

        await self._broadcast_progress(
            job="PROCESS_COMPANY",
            job_id=job_id,
            status="started",
            current=0,
            total=3,
            message="Starting company profile analysis",
        )

        try:
            combined_text = self._build_combined_text(profile)
            await self._broadcast_progress(
                job="PROCESS_COMPANY",
                job_id=job_id,
                status="progress",
                current=1,
                total=3,
                message="Generating structured metadata",
            )

            metadata: Dict[str, Any] = {}
            if combined_text:
                metadata = self._get_llm().extract_company(combined_text)

            summary_text = (metadata or {}).get("summary") or ""
            embedding = self.embedder.embed(summary_text)

            await self._broadcast_progress(
                job="PROCESS_COMPANY",
                job_id=job_id,
                status="progress",
                current=2,
                total=3,
                message="Building embeddings",
            )

            status = profile.get("status") or {}
            status.update(
                {
                    "processing_status": "ready",
                    "last_error": None,
                    "files_processed": len(
                        [item for item in profile.get("uploaded_files", []) if item.get("extract_status") == "extracted"]
                    ),
                    "total_files": len(profile.get("uploaded_files", [])),
                }
            )

            await self.repo.update(
                company_id,
                {
                    "metadata": self._normalize_offering(metadata or {}),
                    "metadata_updated_at": utcnow().isoformat(),
                    "summary_embedding": embedding,
                    "status": status,
                    "updated_at": utcnow(),
                },
            )
            schedule_refresh(self.db, company_id)

            await self._broadcast_progress(
                job="PROCESS_COMPANY",
                job_id=job_id,
                status="completed",
                current=3,
                total=3,
                message="Company profile is ready",
            )
        except Exception as exc:
            logger.info("company.process_failed", company_id=company_id, error=str(exc))
            status = profile.get("status") or {}
            status.update(
                {
                    "processing_status": "failed",
                    "last_error": str(exc),
                }
            )
            await self.repo.update(company_id, {"status": status, "updated_at": utcnow()})
            await self._broadcast_progress(
                job="PROCESS_COMPANY",
                job_id=job_id,
                status="failed",
                current=0,
                total=3,
                message=str(exc),
            )

    async def update_offering(self, company_id: str, owner_user_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """
        Let the user correct the extracted offering profile (capabilities, exclusions,
        procurement types, turnover...). Any change invalidates cached match judgments.
        """
        profile = await self._get_owned_profile(company_id, owner_user_id)
        if not profile:
            raise ValueError("Company profile not found")

        old_metadata = self._normalize_offering(dict(profile.get("metadata") or {}))
        metadata = dict(old_metadata)
        for key, value in changes.items():
            if key in OFFERING_FIELDS and value is not None:
                metadata[key] = value
        metadata = self._normalize_offering(metadata)

        offering_changed = any(
            metadata.get(key) != old_metadata.get(key)
            for key in OFFERING_FIELDS - SEARCH_ONLY_FIELDS - ELIGIBILITY_ONLY_FIELDS
        )
        update: Dict[str, Any] = {"metadata": metadata, "updated_at": utcnow()}
        years = changes.get("experience_years")
        if isinstance(years, (int, float)) and 0 <= years <= 100:
            update["experience_years"] = int(years)
        if offering_changed:
            update["metadata_updated_at"] = utcnow().isoformat()
        await self.repo.update(company_id, update)
        if offering_changed:
            schedule_refresh(self.db, company_id)
        latest = await self.get_profile(company_id, owner_user_id)
        return latest or {}

    @staticmethod
    def _normalize_offering(metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Clean list fields so matching always sees lists of non-empty strings."""
        cleaned = dict(metadata)
        for key in OFFERING_LIST_FIELDS:
            value = cleaned.get(key)
            if isinstance(value, str):
                value = [part for part in value.split(",")]
            if isinstance(value, list):
                cleaned[key] = [str(v).strip() for v in value if v and str(v).strip()]
        if isinstance(cleaned.get("preferred_procurement_types"), list):
            cleaned["preferred_procurement_types"] = [v.lower() for v in cleaned["preferred_procurement_types"]]
        return cleaned

    def _build_combined_text(self, profile: Dict[str, Any]) -> str:
        chunks: List[str] = []
        chunks.append(f"Company Name: {profile.get('name') or ''}")
        chunks.append(f"Company URL: {profile.get('company_url') or ''}")
        chunks.append(f"Experience Years: {profile.get('experience_years') or ''}")

        interest_tags = profile.get("interest_tags") or []
        if interest_tags:
            chunks.append(f"Interest Tags: {', '.join(interest_tags)}")

        website_text = ((profile.get("website_scrape") or {}).get("extracted_text") or "").strip()
        if website_text:
            chunks.append("Website Information:")
            chunks.append(website_text)

        for file_info in profile.get("uploaded_files", []):
            extracted = (file_info.get("extracted_text") or "").strip()
            if extracted:
                chunks.append(f"Document {file_info.get('original_name')}:\n{extracted}")

        full_text = "\n\n".join(part for part in chunks if part)
        max_chars = settings.LLM_GEMINI_MAX_CHARS if settings.LLM_PROVIDER.lower() == "gemini" else settings.LLM_INPUT_MAX_CHARS
        if len(full_text) > max_chars:
            return full_text[:max_chars]
        return full_text

    async def get_profile(self, company_id: str, owner_user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        profile = await self.repo.get_by_id(company_id)
        if not profile:
            return None
        if owner_user_id and profile.get("owner_user_id") != owner_user_id:
            return None
        return self._serialize(profile)

    async def list_companies(
        self,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        search: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}
        if status:
            filters["status.processing_status"] = status
        if search:
            regex = {"$regex": search, "$options": "i"}
            filters["$or"] = [
                {"name": regex},
                {"metadata.company_name": regex},
                {"metadata.domains": regex},
            ]
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id

        total = await self.repo.collection.count_documents(filters)
        cursor = self.repo.collection.find(filters).sort("created_at", -1).skip(skip).limit(limit)
        items = []
        async for profile in cursor:
            items.append(self._serialize(profile))
        return {"items": items, "total": total, "skip": skip, "limit": limit}

    async def delete_profile(self, company_id: str, owner_user_id: Optional[str] = None) -> bool:
        profile = await self.repo.get_by_id(company_id)
        if not profile:
            return False
        if owner_user_id and profile.get("owner_user_id") != owner_user_id:
            return False

        for file_info in profile.get("uploaded_files", []):
            path = file_info.get("local_path")
            if path and os.path.exists(path):
                os.remove(path)
        return await self.repo.delete(company_id)

    async def get_search_history(self, company_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        cursor = (
            self.repo.collection.database.get_collection("search_history")
            .find({"company_id": company_id})
            .sort("searched_at", -1)
            .limit(limit)
        )
        items = []
        async for entry in cursor:
            entry_id = entry.get("_id")
            if entry_id is not None:
                entry["id"] = str(entry_id)
                entry["_id"] = str(entry_id)
            items.append(entry)
        return items

    async def _get_owned_profile(self, company_id: str, owner_user_id: str) -> Optional[Dict[str, Any]]:
        profile = await self.repo.get_by_id(company_id)
        if not profile:
            return None
        if profile.get("owner_user_id") != owner_user_id:
            return None
        return profile

    async def _broadcast_progress(
        self,
        job: str,
        job_id: str,
        status: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        payload = {
            "type": "JOB_PROGRESS",
            "job": job,
            "job_id": job_id,
            "status": status,
            "progress": {
                "current": current,
                "total": total,
                "percent": round((current / total), 4) if total else 0,
            },
            "message": message,
            "ts": utcnow().isoformat(),
        }
        await manager.broadcast(payload)

    def _serialize(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not profile:
            return profile
        profile_data = dict(profile)
        profile_id = profile_data.get("_id")
        if profile_id is not None:
            profile_data["id"] = str(profile_id)
            profile_data["_id"] = str(profile_id)
        return profile_data

    async def create_profile(self, files: List[UploadFile], company_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Backward-compatible helper used by legacy endpoint.
        Creates a company, uploads files, and starts processing.
        """
        owner_user_id = str(uuid.uuid4())
        profile = await self.create_company(
            owner_user_id=owner_user_id,
            name=company_name or "Unnamed Company",
            company_url="https://example.com",
            experience_years=0,
        )
        company_id = profile["company_id"]
        await self.upload_documents(company_id=company_id, owner_user_id=owner_user_id, files=files)
        await self.start_processing(company_id=company_id, owner_user_id=owner_user_id)
        latest = await self.get_profile(company_id)
        return latest or profile
