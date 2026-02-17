"""Ollama LLM fallback for complex command parsing."""

from __future__ import annotations

import json

import httpx

from aion.config import get_config
from aion.date_parser import parse_date_from_query
from aion.intent import ParsedCommand

_ollama_status: bool | None = None


def _url() -> str:
    return get_config().get("ollama_url", "http://localhost:11434")


def _model() -> str:
    return get_config().get("ollama_model", "llama3.2")


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
    """Use Ollama to parse a complex command into structured data."""
    if events:
        summary = "\n".join(
            f"- {e.get('date', '?')} {e.get('time', '?')}: {e.get('title', '?')} ({e.get('duration', 60)}min)"
            for e in events[:20]
        )
    else:
        summary = "(no events loaded)"

    prompt = f"""You are a calendar command parser. Extract intent and entities from this command.

User command: "{user_input}"

Current events:
{summary}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "intent": "SCHEDULE|LIST|DELETE|UPDATE|FIND_FREE|FIND_OPTIMAL",
  "activity": "event title or null",
  "date": "YYYY-MM-DD or relative like 'tomorrow' or null",
  "time": "HH:MM or null",
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

    # Resolve date
    date_str = data.get("date")
    dates: list[str] = []
    date_label = ""
    if date_str:
        if len(date_str) == 10 and date_str[4] == "-":
            dates = [date_str]
            date_label = date_str
        else:
            info = parse_date_from_query(date_str)
            dates = info.get("dates", [])
            date_label = info.get("label", "")

    duration = data.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = None

    return ParsedCommand(
        intent=intent,
        activity=data.get("activity"),
        dates=dates,
        date_label=date_label,
        time=data.get("time"),
        duration=duration,
        time_pref=data.get("time_pref"),
        confidence=0.85,
        raw=user_input,
    )
