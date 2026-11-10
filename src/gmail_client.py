"""Gmail client: read recent mail and create reply drafts.

One class for both accounts. account="personal" can read and draft;
account="work" is read-only (its token is issued with gmail.readonly only).
The bot never sends email: it only prepares drafts for a human to review.
"""

from __future__ import annotations

import asyncio
import base64
import re
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build

from google_auth import SCOPES_BY_ACCOUNT, load_credentials


class GmailClient:
    def __init__(self, token_path: str, account: str = "personal"):
        self.token_path = token_path
        self.account = account
        self.scopes = SCOPES_BY_ACCOUNT[account]
        self._service = None

    @property
    def configured(self) -> bool:
        return Path(self.token_path).exists()

    def _get_service(self):
        if self._service is None:
            creds = load_credentials(self.token_path, self.scopes, account=self.account)
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _fetch_recent_sync(self, max_results: int = 20, only_unread: bool = True) -> list[dict]:
        svc = self._get_service()
        query = "is:unread" if only_unread else ""
        messages = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        out = []
        for m in messages.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date", "To"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            out.append({
                "id": m["id"],
                "thread_id": msg.get("threadId"),
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            })
        return out

    def _create_draft_sync(self, to: str, subject: str, body: str,
                           thread_id: str | None = None, reply_to_msg_id: str | None = None) -> dict:
        if self.account != "personal":
            raise PermissionError("Drafts are only created in the personal inbox; work mail is read-only.")
        svc = self._get_service()
        msg = MIMEText(body, _charset="utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to_msg_id:
            # In-Reply-To and References keep the draft inside the original thread.
            full = svc.users().messages().get(
                userId="me", id=reply_to_msg_id, format="metadata",
                metadataHeaders=["Message-ID", "References"],
            ).execute()
            hdrs = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            mid = hdrs.get("Message-ID") or hdrs.get("Message-Id")
            if mid:
                msg["In-Reply-To"] = mid
                msg["References"] = f"{hdrs.get('References', '')} {mid}".strip()

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_dict = {"message": {"raw": raw}}
        if thread_id:
            body_dict["message"]["threadId"] = thread_id
        return svc.users().drafts().create(userId="me", body=body_dict).execute()

    async def fetch_recent(self, max_results: int = 20, only_unread: bool = True) -> list[dict]:
        return await asyncio.to_thread(self._fetch_recent_sync, max_results, only_unread)

    async def create_draft(self, to: str, subject: str, body: str,
                           thread_id: str | None = None, reply_to_msg_id: str | None = None) -> dict:
        return await asyncio.to_thread(self._create_draft_sync, to, subject, body, thread_id, reply_to_msg_id)


def extract_email(from_header: str) -> str:
    """'First Last <email@domain>' -> 'email@domain'."""
    m = re.search(r"<([^>]+)>", from_header)
    return m.group(1) if m else from_header.strip()
