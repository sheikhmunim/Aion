"""Google Calendar API v3 wrapper using httpx."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from aion.config import get_config, get_now, get_tokens, save_tokens

BASE_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass
class EventData:
    id: str
    title: str
    date: str       # YYYY-MM-DD
    time: str       # HH:MM
    duration: int   # minutes
    description: str = ""
    category: str = "other"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "time": self.time,
            "duration": self.duration,
            "description": self.description,
            "category": self.category,
        }


def _parse_rfc3339(s: str) -> datetime:
    """Parse RFC3339 datetime (e.g. '2026-02-16T09:00:00-05:00')."""
    if s[-3] == ":" and (s[-6] == "+" or s[-6] == "-"):
        s = s[:-3] + s[-2:]
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def _parse_gcal_event(raw: dict) -> EventData | None:
    """Convert a Google Calendar API event to EventData."""
    start_str = raw.get("start", {}).get("dateTime")
    if not start_str:
        return None  # skip all-day events

    end_str = raw.get("end", {}).get("dateTime", start_str)
    start_dt = _parse_rfc3339(start_str)
    end_dt = _parse_rfc3339(end_str)
    duration = max(int((end_dt - start_dt).total_seconds() / 60), 15)

    return EventData(
        id=raw.get("id", ""),
        title=raw.get("summary", "(no title)"),
        date=start_dt.strftime("%Y-%m-%d"),
        time=start_dt.strftime("%H:%M"),
        duration=duration,
        description=raw.get("description", ""),
    )


class GoogleCalendar:
    """Google Calendar API v3 client."""

    def __init__(self):
        tokens = get_tokens()
        if not tokens:
            raise RuntimeError("Not logged in. Run 'aion login' first.")
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token", "")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _refresh_if_needed(self, resp: httpx.Response) -> bool:
        """Auto-refresh token on 401. Returns True if refreshed."""
        if resp.status_code != 401 or not self._refresh_token:
            return False

        cfg = get_config()
        async with httpx.AsyncClient() as client:
            r = await client.post(
                TOKEN_URL,
                data={
                    "client_id": cfg.get("google_client_id", ""),
                    "client_secret": cfg.get("google_client_secret", ""),
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if r.status_code != 200:
                raise RuntimeError(
                    "Google session expired. Run 'login' to reconnect."
                )
            data = r.json()

        self._access_token = data["access_token"]
        save_tokens({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_in": data.get("expires_in", 3600),
            "token_type": data.get("token_type", "Bearer"),
        })
        return True

    async def list_events(self, date: str | None = None) -> list[EventData]:
        """List events, optionally filtered to a specific date."""
        tz = get_config().get("timezone", "UTC")
        params: dict[str, str] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "100",
            "timeZone": tz,
        }
        tz_info = ZoneInfo(tz)
        if date:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz_info)
            params["timeMin"] = d.strftime("%Y-%m-%dT00:00:00%z")
            params["timeMax"] = (d + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00%z")
        else:
            now = get_now()
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            params["timeMin"] = day_start.strftime("%Y-%m-%dT00:00:00%z")
            params["timeMax"] = (day_start + timedelta(days=8)).strftime("%Y-%m-%dT00:00:00%z")

        async with httpx.AsyncClient() as client:
            resp = await client.get(BASE_URL, params=params, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.get(BASE_URL, params=params, headers=self._headers())
            resp.raise_for_status()

        events = [ev for item in resp.json().get("items", []) if (ev := _parse_gcal_event(item))]

        # Filter to exact date if specified (API padding may include adjacent days)
        if date:
            events = [ev for ev in events if ev.date == date]

        return events

    async def list_events_range(self, start_date: str, end_date: str) -> list[EventData]:
        """List events across a date range (inclusive)."""
        tz = get_config().get("timezone", "UTC")
        tz_info = ZoneInfo(tz)
        d_start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz_info)
        d_end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz_info)
        params: dict[str, str] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "250",
            "timeZone": tz,
            "timeMin": d_start.strftime("%Y-%m-%dT00:00:00%z"),
            "timeMax": (d_end + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00%z"),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(BASE_URL, params=params, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.get(BASE_URL, params=params, headers=self._headers())
            resp.raise_for_status()

        events = [ev for item in resp.json().get("items", []) if (ev := _parse_gcal_event(item))]
        # Filter to exact range
        return [ev for ev in events if start_date <= ev.date <= end_date]

    async def create_event(self, title: str, date: str, time: str, duration: int, description: str = "") -> EventData:
        """Create a new calendar event."""
        start_dt = datetime.strptime(f"{date}T{time}", "%Y-%m-%dT%H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        tz = get_config().get("timezone", "UTC")

        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
            "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(BASE_URL, json=body, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.post(BASE_URL, json=body, headers=self._headers())
            resp.raise_for_status()

        return EventData(
            id=resp.json().get("id", ""),
            title=title, date=date, time=time,
            duration=duration, description=description,
        )

    async def update_event(self, event_id: str, **changes) -> EventData:
        """Update an existing event."""
        url = f"{BASE_URL}/{event_id}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            current = resp.json()

        if "title" in changes:
            current["summary"] = changes["title"]
        if "description" in changes:
            current["description"] = changes["description"]
        if "date" in changes or "time" in changes:
            date = changes.get("date", current["start"].get("dateTime", "")[:10])
            time = changes.get("time", current["start"].get("dateTime", "")[11:16])
            dur = changes.get("duration", 60)
            start_dt = datetime.strptime(f"{date}T{time}", "%Y-%m-%dT%H:%M")
            end_dt = start_dt + timedelta(minutes=dur)
            tz = get_config().get("timezone", "UTC")
            current["start"] = {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz}
            current["end"] = {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz}

        async with httpx.AsyncClient() as client:
            resp = await client.put(url, json=current, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.put(url, json=current, headers=self._headers())
            resp.raise_for_status()

        return _parse_gcal_event(resp.json())

    async def delete_event(self, event_id: str) -> None:
        """Delete an event."""
        url = f"{BASE_URL}/{event_id}"
        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, headers=self._headers())
            if await self._refresh_if_needed(resp):
                resp = await client.delete(url, headers=self._headers())
            resp.raise_for_status()
