"""Ollama LLM fallback for complex command parsing."""

from __future__ import annotations

import json
from datetime import date as date_cls, timedelta

import httpx

from aion.config import get_config, get_now
from aion.intent import ParsedCommand, _extract_time


def _clean(val: object) -> object:
    """Convert LLM string 'null'/'none'/'' to Python None."""
    if isinstance(val, str) and val.strip().lower() in ("null", "none", ""):
        return None
    return val

_ollama_status: bool | None = None


def _url() -> str:
    return get_config().get("ollama_url", "http://localhost:11434")


def _model() -> str:
    return get_config().get("ollama_model", "qwen2.5:0.5b")


def ollama_available() -> bool:
    """Check if Ollama is running (cached after first check)."""
    global _ollama_status
    if _ollama_status is not None:
        return _ollama_status
    try:
        r = httpx.get(f"{_url()}/api/tags", timeout=2.0)
        _ollama_status = r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        _ollama_status = False
    return _ollama_status


def reset_status() -> None:
    global _ollama_status
    _ollama_status = None


async def ollama_classify(user_input: str, events: list[dict] | None = None) -> ParsedCommand:
    """Use Ollama to parse a command into structured data."""
    now = get_now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")

    if events:
        summary = "\n".join(
            f"- {e.get('date', '?')} {e.get('time', '?')}: {e.get('title', '?')} ({e.get('duration', 60)}min)"
            for e in events[:20]
        )
    else:
        summary = "(no events loaded)"

    prompt = f"""You are a calendar command parser. Today is {today} ({weekday}).

Intents:
- LIST = user wants to SEE/VIEW events ("what tomorrow?", "what I have today", "show my calendar")
- SCHEDULE = user wants to CREATE/ADD a new event ("schedule gym at 3pm", "add meeting tomorrow")
- DELETE = user wants to REMOVE an event ("cancel gym", "delete meeting")
- UPDATE = user wants to CHANGE an event ("move gym to 3pm", "reschedule meeting")
- FIND_FREE = user wants to see AVAILABLE/FREE time slots ("when am I free?")
- FIND_OPTIMAL = user wants a SUGGESTED time ("best time for study?")

If the user is ASKING about their schedule (what, when, anything), that is LIST, not SCHEDULE.

User command: "{user_input}"

Current events:
{summary}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "intent": "SCHEDULE|LIST|DELETE|UPDATE|FIND_FREE|FIND_OPTIMAL",
  "activity": "event title or null",
  "date": "YYYY-MM-DD resolved from today ({today}) or null",
  "date_end": "YYYY-MM-DD for date ranges like this week or next week, otherwise null",
  "time": "HH:MM in 24-hour format or null. For bare numbers like 'at 6' use context: gym/morning = 06:00, evening/dinner = 18:00",
  "duration": "minutes as integer or null",
  "time_pref": "morning|afternoon|evening|null"
}}"""

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_url()}/api/generate",
            json={"model": _model(), "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
        )
        resp.raise_for_status()

    raw = resp.json().get("response", "").strip()

    # Strip markdown code fences if present
    text = raw
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    data = json.loads(text.strip())

    intent = data.get("intent", "UNKNOWN").upper()
    valid = {"SCHEDULE", "LIST", "DELETE", "UPDATE", "FIND_FREE", "FIND_OPTIMAL"}
    if intent not in valid:
        intent = "UNKNOWN"

    # Sanitize all fields — LLM sometimes returns the string "null" instead of JSON null
    activity  = _clean(data.get("activity"))
    date_str  = _clean(data.get("date"))
    date_end_str = _clean(data.get("date_end"))
    time_val  = _clean(data.get("time"))
    time_pref = _clean(data.get("time_pref"))

    # Time fallback — if Ollama returned null, try regex on the raw input (handles "at 6" etc.)
    if time_val is None:
        time_val = _extract_time(user_input)

    # Resolve dates — Ollama returns YYYY-MM-DD directly
    dates: list[str] = []
    date_label = ""

    if date_str:
        try:
            start = date_cls.fromisoformat(date_str)
            if date_end_str:
                end = date_cls.fromisoformat(date_end_str)
                dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
                date_label = f"{start.strftime('%b %d')} – {end.strftime('%b %d')}"
            else:
                dates = [date_str]
                date_label = start.strftime("%A, %B %d")
        except ValueError:
            dates = [date_str]
            date_label = date_str

    duration = data.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = None

    return ParsedCommand(
        intent=intent,
        activity=activity,
        dates=dates,
        date_label=date_label,
        time=time_val,
        duration=duration,
        time_pref=time_pref,
        confidence=0.95,
        raw=user_input,
    )
