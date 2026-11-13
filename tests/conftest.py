"""
Pytest fixtures and in-memory async DB fakes.
"""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest


class SimpleResult:
    def __init__(self, inserted_id=None, modified_count=0, deleted_count=0, upserted_id=None):
        self.inserted_id = inserted_id
        self.modified_count = modified_count
        self.deleted_count = deleted_count
        self.upserted_id = upserted_id


class InMemoryCursor:
    def __init__(self, items: List[Dict[str, Any]]):
        self.items = list(items)
        self._index = 0

    def sort(self, key: str, direction: int):
        reverse = direction < 0
        self.items.sort(key=lambda item: _nested_get(item, key), reverse=reverse)
        return self

    def skip(self, count: int):
        self.items = self.items[count:]
        return self

    def limit(self, count: int):
        self.items = self.items[:count]
        return self

    async def to_list(self, length: int = 50):
        return self.items[:length]

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self.items):
            raise StopAsyncIteration
        value = self.items[self._index]
        self._index += 1
        return value


class InMemoryCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def create_index(self, *args, **kwargs):
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        payload = copy.deepcopy(doc)
        payload.setdefault("_id", str(uuid.uuid4()))
        self.docs.append(payload)
        return SimpleResult(inserted_id=payload["_id"])

    def find(self, filters: Optional[Dict[str, Any]] = None):
        matched = [copy.deepcopy(item) for item in self.docs if _matches(item, filters or {})]
        return InMemoryCursor(matched)

    async def find_one(self, filters: Optional[Dict[str, Any]] = None, projection=None, sort=None):
        # `projection` is accepted for signature parity with Motor and ignored (full docs returned).
        matched = [copy.deepcopy(item) for item in self.docs if _matches(item, filters or {})]
        if sort:
            key, direction = sort[0]
            matched.sort(key=lambda item: _nested_get(item, key), reverse=direction < 0)
        return matched[0] if matched else None

    async def update_one(self, filters: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        for index, item in enumerate(self.docs):
            if _matches(item, filters):
                updated = _apply_update(item, update)
                self.docs[index] = updated
                return SimpleResult(modified_count=1)

        if upsert:
            new_doc = _seed_from_filter(filters)
            new_doc = _apply_update(new_doc, update)
            new_doc.setdefault("_id", str(uuid.uuid4()))
            self.docs.append(new_doc)
            return SimpleResult(modified_count=0, upserted_id=new_doc["_id"])

        return SimpleResult(modified_count=0)

    async def update_many(self, filters: Dict[str, Any], update: Dict[str, Any]):
        modified = 0
        for index, item in enumerate(self.docs):
            if _matches(item, filters):
                self.docs[index] = _apply_update(item, update)
                modified += 1
        return SimpleResult(modified_count=modified)

    async def delete_one(self, filters: Dict[str, Any]):
        for index, item in enumerate(self.docs):
            if _matches(item, filters):
                self.docs.pop(index)
                return SimpleResult(deleted_count=1)
        return SimpleResult(deleted_count=0)

    async def count_documents(self, filters: Dict[str, Any]):
        return sum(1 for item in self.docs if _matches(item, filters or {}))


def _nested_get(doc: Dict[str, Any], path: str):
    current = doc
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _nested_set(doc: Dict[str, Any], path: str, value: Any):
    keys = path.split(".")
    current = doc
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _seed_from_filter(filters: Dict[str, Any]) -> Dict[str, Any]:
    seeded: Dict[str, Any] = {}
    for key, value in filters.items():
        if key.startswith("$"):
            continue
        if isinstance(value, dict):
            continue
        _nested_set(seeded, key, value)
    return seeded


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    payload = copy.deepcopy(doc)
    for key, value in update.get("$set", {}).items():
        _nested_set(payload, key, value)
    for key, value in update.get("$setOnInsert", {}).items():
        if _nested_get(payload, key) is None:
            _nested_set(payload, key, value)
    if "$push" in update:
        for key, value in update["$push"].items():
            current = _nested_get(payload, key) or []
            if isinstance(value, dict) and "$each" in value:
                current.extend(value["$each"])
            else:
                current.append(value)
            _nested_set(payload, key, current)
    return payload


def _matches(doc: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    if not filters:
        return True

    for key, value in filters.items():
        if key == "$or":
            if not any(_matches(doc, clause) for clause in value):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, clause) for clause in value):
                return False
            continue

        actual = _nested_get(doc, key)
        if isinstance(value, dict):
            for operator, expected in value.items():
                if operator == "$in":
                    if isinstance(actual, list):
                        if not any(item in expected for item in actual):
                            return False
                    elif actual not in expected:
                        return False
                elif operator == "$ne":
                    if actual == expected:
                        return False
                elif operator == "$regex":
                    options = value.get("$options", "")
                    flags = re.IGNORECASE if "i" in options else 0
                    if not re.search(expected, str(actual or ""), flags=flags):
                        return False
                elif operator == "$gte":
                    if actual is None or actual < expected:
                        return False
                elif operator == "$lt":
                    if actual is None or actual >= expected:
                        return False
                elif operator == "$options":
                    continue
        else:
            if actual != value:
                return False

    return True


class InMemoryDB:
    def __init__(self):
        self.collections: Dict[str, InMemoryCollection] = {}

    def get_collection(self, name: str):
        if name not in self.collections:
            self.collections[name] = InMemoryCollection()
        return self.collections[name]


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def fake_db():
    return InMemoryDB()


@pytest.fixture(autouse=True)
def _no_background_rematch(monkeypatch):
    """Never fire background LLM relevance refreshes from tests."""
    from app.config import settings

    monkeypatch.setattr(settings, "MATCH_AUTO_REFRESH", False)
