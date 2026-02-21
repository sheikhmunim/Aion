"""Regex-based intent classifier and entity extractor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from aion.date_parser import parse_date_from_query


@dataclass
class ParsedCommand:
    intent: str                        # SCHEDULE, LIST, DELETE, UPDATE, FIND_FREE, FIND_OPTIMAL, HELP, UNKNOWN
    activity: str | None = None        # "gym", "meeting with John"
    label: str | None = None           # custom title: "called Morning Workout"
    dates: list[str] = field(default_factory=list)
    date_label: str = ""               # "tomorrow (February 18, 2026)"
    time: str | None = None            # "15:00"
    duration: int | None = None        # minutes
    time_pref: str | None = None       # "morning", "afternoon", "evening"
    confidence: float = 1.0
    raw: str = ""

    @property
    def title(self) -> str | None:
        """Event title — label if provided, otherwise activity."""
        return self.label or self.activity


# (intent_name, pattern, priority) — higher priority checked first
_INTENT_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("HELP", re.compile(
        r"^(?:help|commands|what can you do|how do(?:es)? (?:this|it) work)\s*\??$", re.I), 10),
    ("PREFERENCES", re.compile(
        r"\b(?:preferences?|settings?|blocked?\s*(?:slots?|times?)?|configure)\b", re.I), 8),
    ("FIND_OPTIMAL", re.compile(
        r"\b(?:best\s+time|optimal|when\s+should\s+i|suggest|recommend)\b", re.I), 9),
    ("FIND_FREE", re.compile(
        r"\b(?:free|available|open\s+slots?|when\s+am\s+i\s+free)\b", re.I), 8),
    ("DELETE", re.compile(
        r"\b(?:delete|cancel|remove)\b", re.I), 7),
    ("UPDATE", re.compile(
        r"\b(?:move|change|reschedule|update|push\s+back|bring\s+forward)\b", re.I), 7),
    ("SCHEDULE", re.compile(
        r"\b(?:schedule|add|create|book|set\s+up|plan)\b", re.I), 6),
    ("LIST", re.compile(
        r"\b(?:list|show|what'?s\s+on|events|calendar|plans|agenda|what\s+(?:do\s+)?i\s+have|check\s+(?:my\s+)?(?:calendar|events|schedule)|is\s+there\s+anything|anything\s+(?:on|today|tomorrow)|do\s+i\s+have|what\s+(?:event|meeting)|have\s+i\s+got|what'?s\s+(?:on\s+)?(?:my\s+)?(?:today|tomorrow|schedule)|what\s+(?:about\s+|(?:is\s+)?(?:there\s+|happening\s+)?(?:on\s+|in\s+|for\s+)?)?(?:today|tomorrow|(?:this|next)\s+week|(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)))\b", re.I), 5),
]

_TIME_12H = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_TIME_24H = re.compile(r"\bat\s+(\d{1,2}):(\d{2})\b")
# Bare hour: "at 2", "at 10" — no am/pm, no minutes
_TIME_BARE = re.compile(r"\bat\s+(\d{1,2})\b(?!\s*(?:am|pm|:\d|hours?|hrs?|h\b|minutes?|mins?|m\b))", re.I)
_DURATION = re.compile(r"\b(?:for\s+)?(\d+(?:\.\d+)?)\s*[-\s]*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)
_DURATION_SHORT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(h|hr|hrs|min|mins)\b", re.I)
_TIME_PREF = re.compile(r"\b(morning|afternoon|evening|night)\b", re.I)
_LABEL = re.compile(r"\b(?:called|named|titled?|as)\s+[\"']?(.+?)[\"']?\s*$", re.I)


def _extract_time(text: str) -> str | None:
    m = _TIME_12H.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3).lower() == "pm" and hour != 12:
            hour += 12
        elif m.group(3).lower() == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    m = _TIME_24H.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"

    # Bare hour: "at 2" → 14:00, "at 9" → 09:00
    # Heuristic: 1-6 = PM (nobody schedules "at 2" meaning 2am), 7-12 = AM
    m = _TIME_BARE.search(text)
    if m:
        hour = int(m.group(1))
        if 1 <= hour <= 6:
            hour += 12
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"

    return None


def _extract_duration(text: str) -> int | None:
    m = _DURATION.search(text) or _DURATION_SHORT.search(text)
    if m:
        value = float(m.group(1))
        unit = m.group(2).lower()
        return int(value * 60) if unit.startswith("h") else int(value)
    return None


def _extract_time_pref(text: str) -> str | None:
    m = _TIME_PREF.search(text)
    if m:
        pref = m.group(1).lower()
        return "evening" if pref == "night" else pref
    return None


def _extract_label(text: str) -> tuple[str | None, str]:
    """Extract custom label and return (label, text_without_label)."""
    m = _LABEL.search(text)
    if m:
        label = m.group(1).strip(" \"'")
        cleaned = text[:m.start()].strip()
        return label, cleaned
    return None, text


def _extract_activity(text: str, intent: str) -> str | None:
    """Extract the activity/event name from text by stripping known patterns."""
    from aion.date_parser import _fix_typos
    cleaned = _fix_typos(text.strip())

    # Strip conversational preamble: "can you", "could you", "please", "I want to", etc.
    cleaned = re.sub(
        r"^(?:(?:can|could|would)\s+you\s+(?:please\s+)?|please\s+|I\s+(?:want\s+to|need\s+to|'d\s+like\s+to)\s+)",
        "", cleaned, flags=re.I,
    )

    # Check for "for <activity>" pattern at end (e.g. "add event for gym")
    # Only if "for" is NOT followed by a number (which would be duration)
    for_activity = re.search(r"\bfor\s+(?![\d.]+\s*(?:hour|hr|h|min|m\b))(\w[\w\s]*?)\s*$", cleaned, re.I)

    # Remove the intent verb phrase
    verb_patterns = {
        "SCHEDULE": r"^(?:schedule|add|create|book|set\s+up|plan)\s+",
        "DELETE": r"^(?:delete|cancel|remove)\s+",
        "UPDATE": r"^(?:move|change|reschedule|update)\s+",
        "FIND_OPTIMAL": r"^(?:find\s+(?:the\s+)?best\s+time\s+for\s+(?:a\s+)?|suggest\s+(?:a\s+)?time\s+for\s+(?:a\s+)?|when\s+should\s+i\s+)",
    }
    pat = verb_patterns.get(intent)
    if pat:
        cleaned = re.sub(pat, "", cleaned, flags=re.I)

    # Remove time/date/duration/preference fragments
    removals = [
        _TIME_12H, _TIME_24H, _TIME_BARE, _DURATION, _DURATION_SHORT, _TIME_PREF,
        re.compile(r"\b(?:today|tomorrow|yesterday|this\s+week|next\s+week)\b", re.I),
        re.compile(r"\b(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I),
        re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s*\d{0,2}(?:st|nd|rd|th)?\b", re.I),
        re.compile(r"\b(?:at|on|for|from|to|in\s+the)\b\s*$", re.I),
    ]
    for r in removals:
        cleaned = r.sub("", cleaned)

    # Remove filler words
    cleaned = re.sub(r"\b(?:a|an|the|my|me)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-?!")

    # If stripping left nothing useful but we found "for <activity>", use that
    if not cleaned and for_activity:
        cleaned = for_activity.group(1).strip()

    # If result is still messy but "for <activity>" was clear, prefer it
    if for_activity and len(cleaned.split()) > 3:
        candidate = for_activity.group(1).strip()
        if candidate:
            cleaned = candidate

    return cleaned if cleaned else None


def regex_classify(user_input: str) -> ParsedCommand:
    """Classify intent and extract entities using regex patterns."""
    from aion.date_parser import _fix_typos
    text = _fix_typos(user_input.strip())
    if not text:
        return ParsedCommand(intent="UNKNOWN", raw=text, confidence=0.0)

    # Match intent
    intent = "UNKNOWN"
    confidence = 0.0
    for name, pattern, _ in _INTENT_PATTERNS:
        if pattern.search(text):
            intent = name
            confidence = 0.9
            break

    # Extract label first (strip "called/named/titled/as ..." from end)
    label, text_for_activity = _extract_label(text)

    # Extract entities
    date_info = parse_date_from_query(text)
    dates = date_info.get("dates", [])
    date_label = date_info.get("label", "")
    time = _extract_time(text)
    duration = _extract_duration(text)
    time_pref = _extract_time_pref(text)
    activity = _extract_activity(text_for_activity, intent) if intent in ("SCHEDULE", "DELETE", "UPDATE", "FIND_OPTIMAL") else None

    if intent != "UNKNOWN" and (dates or time or activity):
        confidence = min(confidence + 0.1, 1.0)
    if intent == "UNKNOWN":
        confidence = 0.3

    return ParsedCommand(
        intent=intent,
        activity=activity,
        label=label,
        dates=dates,
        date_label=date_label,
        time=time,
        duration=duration,
        time_pref=time_pref,
        confidence=confidence,
        raw=text,
    )


async def classify(user_input: str, events: list[dict] | None = None) -> ParsedCommand:
    """Classify intent — Ollama when available, regex as offline fallback."""
    from aion.ollama import ollama_available, ollama_classify
    from aion.config import get_config

    if ollama_available() and get_config().get("ollama_enabled", True):
        try:
            return await ollama_classify(user_input, events)
        except Exception:
            pass

    # Ollama not available — offline regex fallback
    return regex_classify(user_input)
