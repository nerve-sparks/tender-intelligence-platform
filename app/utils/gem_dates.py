"""
Dates on GeM.

GeM prints every date (bid card and bid PDF) in India time, and they are stored as they are written
(naive datetimes in IST). So "now" must also be India time when deciding whether a bid is still open,
otherwise every bid closes 5.5 hours late.

Which closing date is the real one:
- The GeM listing card shows the CURRENT deadline. When the buyer extends a bid, the card changes
  but the bid PDF keeps its original "Bid End Date/Time".
- Once a bid has gone to Reverse Auction (the card carries an "RA NO"), the card's dates are the RA's
  dates. The bid itself closed on the PDF's date and new sellers can no longer take part.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))


def gem_now() -> datetime:
    """Current India time as a naive datetime, comparable with stored GeM dates."""
    return datetime.now(IST).replace(tzinfo=None)


def as_gem_time(value: Any) -> Optional[datetime]:
    """A stored GeM date as a naive IST datetime (aware values are converted)."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(IST).replace(tzinfo=None)
    return value


def days_left(end: Any, now: Optional[datetime] = None) -> Optional[int]:
    """Whole days until the deadline (0 on the closing day, never negative)."""
    end_dt = as_gem_time(end)
    if end_dt is None:
        return None
    return max(0, (end_dt - (now or gem_now())).days)


def extended_from(tender: dict) -> Optional[datetime]:
    """The bid PDF's original closing date when the buyer has since extended the deadline, else None."""
    card_end = as_gem_time((tender.get("scraped_info") or {}).get("end_date"))
    pdf_end = as_gem_time((tender.get("metadata") or {}).get("pdf_bid_end_date"))
    if card_end and pdf_end and card_end - pdf_end >= timedelta(minutes=1):
        return pdf_end
    return None
