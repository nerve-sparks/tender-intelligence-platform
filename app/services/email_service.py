"""
Email service for tender sharing (one tender, or a digest of all saved tenders with their GeM bid PDFs).
"""

from __future__ import annotations

import asyncio
import base64
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

Attachment = Tuple[str, bytes]  # (filename, PDF bytes)


class EmailService:
    def __init__(self) -> None:
        self.provider = (settings.EMAIL_PROVIDER or "smtp").lower()

    async def send_tender_share(
        self,
        recipients: List[str],
        tender: Dict[str, Any],
        match_percent: Optional[int],
        note: str | None = None,
    ) -> Dict[str, Any]:
        if not recipients:
            raise ValueError("At least one recipient is required")

        match_text = f" (Match {match_percent}%)" if match_percent is not None else ""
        subject = f"Tender Shared: {tender.get('title') or 'Tender'}{match_text}"
        body = "\n".join(self._tender_lines(tender))
        if note:
            body += f"\n\nNote:\n{note}"
        return await self._send(recipients, subject, body, [])

    async def send_tender_digest(
        self,
        recipients: List[str],
        company_name: str,
        entries: List[Dict[str, Any]],
        attachments: List[Attachment],
        note: str | None = None,
    ) -> Dict[str, Any]:
        """One email listing several tenders, with their bid documents attached."""
        if not recipients:
            raise ValueError("At least one recipient is required")
        if not entries:
            raise ValueError("No tenders to send")

        count = len(entries)
        subject = f"{count} saved GeM tender{'s' if count != 1 else ''} - {company_name}"
        lines = [f"{count} GeM tender{'s' if count != 1 else ''} saved by {company_name}.", ""]
        if note:
            lines += ["Note:", note, ""]
        for index, entry in enumerate(entries, start=1):
            tender_lines = self._tender_lines(entry)
            lines.append(f"{index}. {tender_lines[0]}")
            lines.extend(f"   {line}" for line in tender_lines[1:])
            lines.append("")
        return await self._send(recipients, subject, "\n".join(lines).rstrip() + "\n", attachments)

    # ------------------------------------------------------------------ body

    def _tender_lines(self, tender: Dict[str, Any]) -> List[str]:
        """Title first, then the details. Shared by the single-tender email and the digest."""
        lines = [
            str(tender.get("title") or "Tender"),
            f"Buyer: {tender.get('department') or 'N/A'}",
            f"Location: {tender.get('location') or 'N/A'}",
            f"Bid No: {tender.get('bid_id') or 'N/A'}",
            f"Published: {tender.get('published_date') or 'N/A'}",
            f"Closes: {tender.get('close_date') or 'N/A'}",
        ]
        if tender.get("emd_amount"):
            lines.append(f"EMD: {tender['emd_amount']}")
        lines.append(f"Bid value: {tender.get('bid_value') or 'N/A'}")
        if tender.get("match_percent") is not None:
            verdict = f" - {tender['recommendation']}" if tender.get("recommendation") else ""
            lines.append(f"Match: {tender['match_percent']}%{verdict}")
        if tender.get("why"):
            lines.append(f"Why it fits: {tender['why']}")
        if tender.get("gap"):
            lines.append(f"Watch out: {tender['gap']}")
        if tender.get("summary"):
            lines.append(f"Summary: {tender['summary']}")
        lines.append(f"GeM link: {tender.get('gem_url') or 'N/A'}")
        if tender.get("document"):
            lines.append(f"Bid document: {tender['document']}")
        return lines

    # -------------------------------------------------------------- delivery

    async def _send(self, recipients: List[str], subject: str, body: str, attachments: List[Attachment]) -> Dict[str, Any]:
        if self.provider == "sendgrid" and settings.SENDGRID_API_KEY:
            return await self._send_with_sendgrid(recipients, subject, body, attachments)

        if self.provider == "smtp" and settings.SMTP_HOST and settings.SMTP_USER:
            return await asyncio.to_thread(self._send_with_smtp, recipients, subject, body, attachments)

        logger.info(
            "email.share_mocked",
            recipients=recipients,
            subject=subject,
            attachments=[name for name, _ in attachments],
        )
        return {"sent": True, "provider": "mock", "count": len(recipients)}

    def _send_with_smtp(
        self, recipients: List[str], subject: str, body: str, attachments: List[Attachment]
    ) -> Dict[str, Any]:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)
        for filename, data in attachments:
            msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)

        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=60) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
                server.send_message(msg)
            return {"sent": True, "provider": "smtp", "count": len(recipients)}
        except Exception as exc:
            logger.info("email.smtp_failed", error=str(exc))
            return {"sent": False, "provider": "smtp", "count": 0, "error": str(exc)}

    async def _send_with_sendgrid(
        self, recipients: List[str], subject: str, body: str, attachments: List[Attachment]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "personalizations": [{"to": [{"email": email} for email in recipients]}],
            "from": {"email": settings.EMAIL_FROM.split("<")[-1].replace(">", "").strip() or "no-reply@example.com"},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        if attachments:
            payload["attachments"] = [
                {
                    "content": base64.b64encode(data).decode("ascii"),
                    "type": "application/pdf",
                    "filename": filename,
                    "disposition": "attachment",
                }
                for filename, data in attachments
            ]

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
                )
            if response.status_code >= 300:
                return {"sent": False, "provider": "sendgrid", "count": 0, "error": response.text}
            return {"sent": True, "provider": "sendgrid", "count": len(recipients)}
        except Exception as exc:
            return {"sent": False, "provider": "sendgrid", "count": 0, "error": str(exc)}
