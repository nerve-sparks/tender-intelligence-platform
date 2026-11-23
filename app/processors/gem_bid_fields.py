"""
Deterministic parsing of standard GeM bid-document fields.

GeM bid PDFs are bilingual (Hindi/English) with fixed English labels, e.g.

    ... /Item Category
    Artificial intelligence, Machine Learning, and Deep Learning
    ... /MSE Relaxation for Years of Experience and Turnover      -> Yes / No
    ... /Startup Relaxation for Turnover                          -> Yes | Partial | Turn over value - 75 (in lakhs)

These are read with rules (not the LLM) because eligibility decisions depend on them.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

_LABEL_HINT = re.compile(r"/[A-Z]|[ऀ-ॿ]")  # bilingual label lines: Hindi text or "/Label"


def _lines(text: str) -> List[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _item_category(lines: List[str]) -> Optional[str]:
    for index, line in enumerate(lines):
        if re.search(r"Item Category\s*$", line):
            parts: List[str] = []
            for follow in lines[index + 1 : index + 3]:
                if _LABEL_HINT.search(follow) or re.match(r"^(Bid|Minimum|Years|Quantity|Turnover)\b", follow):
                    break
                parts.append(follow)
            value = " ".join(parts).strip()
            return value[:200] or None
    return None


def _relaxation(flat: str, who: str) -> Dict[str, bool]:
    """Relaxations for `who` ("MSE" / "Startup") as {"experience": bool, "turnover": bool}."""
    result = {"experience": False, "turnover": False}
    both = re.search(rf"{who}\s+Relaxation for Years of\s+Experience and Turnover\s+(Yes|No)\b", flat, re.I)
    if both and both.group(1).lower() == "yes":
        result = {"experience": True, "turnover": True}
    turnover = re.search(rf"{who}\s+Relaxation for Turnover\s+(Yes|No)\b", flat, re.I)
    if turnover and turnover.group(1).lower() == "yes":
        result["turnover"] = True
    experience = re.search(rf"{who}\s+Relaxation for Years of\s+Experience\s+(Yes|No)\b", flat, re.I)
    if experience and experience.group(1).lower() == "yes":
        result["experience"] = True
    return result


_DATE = r"(\d{2}-\d{2}-\d{4})(?:\s+(\d{1,2}:\d{2})(?::\d{2})?)?"


def _pdf_date(flat: str, label: str) -> Optional[datetime]:
    """A date printed after an English label, e.g. "Bid End Date/Time 04-11-2026 12:00:00" (IST, naive)."""
    match = re.search(rf"{label}\s*:?\s*{_DATE}", flat)
    if not match:
        return None
    day, time = match.group(1), match.group(2) or "00:00"
    try:
        return datetime.strptime(f"{day} {time}", "%d-%m-%Y %H:%M")
    except ValueError:
        return None


def parse_gem_bid_dates(text: str) -> Dict[str, Optional[datetime]]:
    """Bid end / opening / published dates exactly as written in the GeM bid PDF."""
    flat = re.sub(r"\s+", " ", text or "")
    return {
        "pdf_bid_end_date": _pdf_date(flat, r"Bid End Date\s*/\s*Time"),
        "pdf_bid_opening_date": _pdf_date(flat, r"Bid Opening\s+Date\s*/\s*Time"),
        "pdf_bid_published_date": _pdf_date(flat, r"/\s*Dated"),
    }


def parse_gem_bid_fields(text: str) -> Dict[str, Any]:
    """Extract item category, MSE / Startup relaxations and the bid dates from GeM bid text."""
    lines = _lines(text)
    # Labels wrap across lines ("MSE" / "Relaxation for Turnover"); match on a whitespace-flattened copy.
    flat = re.sub(r"\s+", " ", " ".join(lines))
    flat = re.sub(r"Relaxation for Turnover Relaxation for Turnover", "Relaxation for Turnover", flat)
    flat = re.sub(
        r"Experience and Turnover Experience and Turnover", "Experience and Turnover", flat
    )
    return {
        "item_category": _item_category(lines),
        "mse_relaxation": _relaxation(flat, "MSE"),
        "startup_relaxation": _relaxation(flat, "Startup"),
        **parse_gem_bid_dates(text),
    }
