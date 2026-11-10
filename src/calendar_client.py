"""Google Calendar client.

Credentials come from google_auth.load_credentials, so no browser login is needed on the server.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

from googleapiclient.discovery import build

from google_auth import PERSONAL_SCOPES, load_credentials


class CalendarClient:
    def __init__(self, token_path: str, calendar_id: str = "primary", default_tz: str = "UTC"):
        self.token_path = token_path
        self.calendar_id = calendar_id
        self.default_tz = default_tz
        self._service = None

    def _get_service(self):
        if self._service is None:
            creds = load_credentials(self.token_path, PERSONAL_SCOPES, account="personal")
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # The Google client is synchronous; each call runs in a thread so the bot's event loop never blocks.

    def _add_event_sync(self, summary: str, start_iso: str, end_iso: str,
                        description: Optional[str] = None, location: Optional[str] = None) -> dict:
        body = {
            "summary": summary,
            "start": {"dateTime": start_iso, "timeZone": self.default_tz},
            "end": {"dateTime": end_iso, "timeZone": self.default_tz},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return self._get_service().events().insert(calendarId=self.calendar_id, body=body).execute()

    def _list_events_sync(self, time_min_iso: str, time_max_iso: str, max_results: int = 50) -> list[dict]:
        results = self._get_service().events().list(
            calendarId=self.calendar_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return results.get("items", [])

    def _delete_event_sync(self, event_id: str) -> bool:
        self._get_service().events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        return True

    async def add_event(self, summary: str, start_iso: str, end_iso: str,
                        description: str | None = None, location: str | None = None) -> dict:
        return await asyncio.to_thread(self._add_event_sync, summary, start_iso, end_iso, description, location)

    async def list_events(self, time_min_iso: str, time_max_iso: str, max_results: int = 50) -> list[dict]:
        return await asyncio.to_thread(self._list_events_sync, time_min_iso, time_max_iso, max_results)

    async def list_days(self, days: int = 1) -> list[dict]:
        """Events from the start of today for the next `days` days (1 = today, 7 = this week)."""
        import pytz
        tz = pytz.timezone(self.default_tz)
        now = datetime.datetime.now(tz)
        start = tz.localize(datetime.datetime.combine(now.date(), datetime.time.min))
        end = start + datetime.timedelta(days=days)
        return await self.list_events(start.isoformat(), end.isoformat())

    async def delete_event(self, event_id: str) -> bool:
        return await asyncio.to_thread(self._delete_event_sync, event_id)


def format_event_short(e: dict) -> str:
    """One-line view of an event; handles all-day events."""
    summary = e.get("summary", "(untitled)")
    start = e.get("start", {})
    if "dateTime" in start:
        try:
            dt = datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            return f"{dt.strftime('%H:%M')} — {summary}"
        except ValueError:
            return f"{start['dateTime']} — {summary}"
    if "date" in start:
        return f"all day — {summary}"
    return summary
