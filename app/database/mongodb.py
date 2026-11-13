"""
MongoDB connection and index management.
"""

from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_client: Optional[AsyncIOMotorClient] = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGO_URI)
    return _client


def get_database() -> AsyncIOMotorDatabase:
    return get_client()[settings.DB_NAME]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def create_indexes(db: Optional[AsyncIOMotorDatabase] = None) -> None:
    """Create required indexes for collections."""
    database = db if db is not None else get_database()

    tender_coll = database.get_collection("tenders")
    await tender_coll.create_index([("bid_id", ASCENDING)], unique=True)
    await tender_coll.create_index([("status.scrape_status", ASCENDING)])
    await tender_coll.create_index([("status.llm_processed", ASCENDING)])
    await tender_coll.create_index([("scraped_info.end_date", ASCENDING)])
    await tender_coll.create_index([("metadata.domains", ASCENDING)])
    await tender_coll.create_index([("metadata.required_certifications", ASCENDING)])
    await tender_coll.create_index([("is_active", ASCENDING), ("expired", ASCENDING)])
    await tender_coll.create_index([("expired", ASCENDING)])
    await tender_coll.create_index([("scraped_at", DESCENDING)])
    await tender_coll.create_index([("created_at", DESCENDING)])

    company_coll = database.get_collection("company_profiles")
    await company_coll.create_index([("company_id", ASCENDING)], unique=True)
    await company_coll.create_index([("owner_user_id", ASCENDING)])
    await company_coll.create_index([("status.processing_status", ASCENDING)])
    await company_coll.create_index([("metadata.certifications", ASCENDING)])
    await company_coll.create_index([("metadata.technologies", ASCENDING)])
    await company_coll.create_index([("metadata.domains", ASCENDING)])
    await company_coll.create_index([("created_at", DESCENDING)])

    users_coll = database.get_collection("users")
    await users_coll.create_index([("email", ASCENDING)], unique=True)
    await users_coll.create_index([("company_id", ASCENDING)])

    sessions_coll = database.get_collection("auth_sessions")
    await sessions_coll.create_index([("user_id", ASCENDING)])
    await sessions_coll.create_index([("expires_at", ASCENDING)])

    otp_coll = database.get_collection("auth_otps")
    await otp_coll.create_index([("email", ASCENDING), ("created_at", DESCENDING)])
    await otp_coll.create_index([("expires_at", ASCENDING)])

    actions_coll = database.get_collection("tender_actions")
    await actions_coll.create_index([("company_id", ASCENDING), ("tender_id", ASCENDING)], unique=True)
    await actions_coll.create_index([("company_id", ASCENDING), ("action", ASCENDING)])
    await actions_coll.create_index([("updated_at", DESCENDING)])

    chats_coll = database.get_collection("tender_chats")
    await chats_coll.create_index([("company_id", ASCENDING), ("tender_id", ASCENDING)])
    await chats_coll.create_index([("updated_at", DESCENDING)])

    matches_coll = database.get_collection("tender_matches")
    await matches_coll.create_index([("company_id", ASCENDING), ("tender_id", ASCENDING)], unique=True)

    notifications_coll = database.get_collection("notifications")
    await notifications_coll.create_index([("company_id", ASCENDING), ("tender_id", ASCENDING)], unique=True)
    await notifications_coll.create_index([("company_id", ASCENDING), ("created_at", DESCENDING)])

    share_logs = database.get_collection("share_logs")
    await share_logs.create_index([("company_id", ASCENDING), ("created_at", DESCENDING)])
    await share_logs.create_index([("tender_id", ASCENDING)])

    await database.get_collection("search_history").create_index(
        [("company_id", ASCENDING), ("searched_at", DESCENDING)]
    )
    scrape_coll = database.get_collection("scrape_logs")
    await scrape_coll.create_index([("job_id", ASCENDING)], unique=True)
    await scrape_coll.create_index([("started_at", DESCENDING)])
    await scrape_coll.create_index([("status", ASCENDING)])
    await scrape_coll.create_index([("job_type", ASCENDING)])

    logger.info("mongodb.indexes.created")
