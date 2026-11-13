"""
HTML parsing utilities for GeM bid listings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup


BID_ID_PATTERN = re.compile(r"GEM/\d{4}/[A-Z]/\d+")
RA_NO_PATTERN = re.compile(r"GEM/\d{4}/R/\d+")


def parse_bid_cards(html: str) -> List[Dict[str, Any]]:
    """Parse bid cards from HTML into structured dictionaries."""
    soup = BeautifulSoup(html, "lxml")
    candidates = []

    for link in soup.find_all("a", string=BID_ID_PATTERN):
        bid_id = link.get_text(strip=True)
        container = _find_card_container(link)
        text = container.get_text(" ", strip=True) if container else link.parent.get_text(" ", strip=True)
        candidates.append(_parse_bid_text(bid_id, text, link))

    # Fallback: scan text blocks if anchors are missing
    if not candidates:
        for element in soup.find_all(text=BID_ID_PATTERN):
            bid_id = BID_ID_PATTERN.search(element).group(0)
            container = _find_card_container(element.parent)
            text = container.get_text(" ", strip=True) if container else element.parent.get_text(" ", strip=True)
            candidates.append(_parse_bid_text(bid_id, text, None))

    # Deduplicate by bid_id
    seen = set()
    unique = []
    for item in candidates:
        bid_id = item.get("bid_id")
        if not bid_id or bid_id in seen:
            continue
        seen.add(bid_id)
        unique.append(item)
    return unique


def _find_card_container(element) -> Optional[Any]:
    current = element
    for _ in range(6):
        if current is None:
            break
        if hasattr(current, "get"):
            classes = current.get("class") or []
            if any("card" in cls.lower() for cls in classes):
                return current
        current = current.parent
    return None


def _parse_bid_text(bid_id: str, text: str, link) -> Dict[str, Any]:
    ra_no = _search_pattern(RA_NO_PATTERN, text)
    scraped = {
        "bid_id": bid_id,
        "ra_no": ra_no,
        "items": _extract_after_label(text, "Items"),
        "quantity": _extract_int_after_label(text, "Quantity"),
        "department": _extract_after_label(text, "Department"),
        "department_address": _extract_after_label(text, "Department Name And Address"),
        "start_date": _extract_date(text, "Start Date"),
        "end_date": _extract_date(text, "End Date"),
        "bid_type": _extract_after_label(text, "Bid Type"),
        "bid_value_range": _extract_after_label(text, "Bid Value"),
    }
    pdf_url = None
    if link is not None:
        href = link.get("href")
        if href:
            pdf_url = _normalize_pdf_url(href)
    scraped["pdf_url"] = pdf_url
    scraped["gem_url"] = pdf_url
    return scraped


def _normalize_pdf_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://bidplus.gem.gov.in{href}"
    return f"https://bidplus.gem.gov.in/{href}"


# Field labels printed on GeM bid cards; a value ends where the next label begins.
_CARD_LABELS = [
    "Items", "Quantity", "Department Name And Address", "Department", "Start Date", "End Date",
    "Bid Type", "Bid Value", "RA NO", "BID NO",
]
_NEXT_LABEL = "|".join(re.escape(label) for label in _CARD_LABELS)


def _extract_after_label(text: str, label: str) -> Optional[str]:
    pattern = re.compile(rf"{re.escape(label)}\s*[:\-]\s*(.+?)(?=\s+(?:{_NEXT_LABEL})\s*:|$)")
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return None


def _extract_int_after_label(text: str, label: str) -> Optional[int]:
    value = _extract_after_label(text, label)
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _extract_date(text: str, label: str) -> Optional[datetime]:
    match = re.search(rf"{re.escape(label)}\s*[:\-]\s*(\d{{2}}-\d{{2}}-\d{{4}}\s+\d{{1,2}}:\d{{2}}\s*(?:AM|PM))", text)
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _search_pattern(pattern: re.Pattern, text: str) -> Optional[str]:
    match = pattern.search(text)
    return match.group(0) if match else None
