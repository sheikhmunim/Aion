"""Date parsing — extracts date references from natural language input."""

import calendar
import re
from datetime import datetime, timedelta

from aion.config import get_now


MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

WEEKDAY_NAMES: dict[str, int] = {
    "sunday": 6, "sun": 6,
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
}


_TYPOS: dict[str, str] = {
    "tommorow": "tomorrow", "tomorow": "tomorrow", "tmrw": "tomorrow", "tmr": "tomorrow",
    "tomorroow": "tomorrow", "tomorrw": "tomorrow", "2morrow": "tomorrow",
    "yesteday": "yesterday", "ysterday": "yesterday", "yesterdy": "yesterday",
    "wenesday": "wednesday", "wensday": "wednesday", "wedensday": "wednesday",
    "thurday": "thursday", "thrusday": "thursday", "tusday": "tuesday", "tueday": "tuesday",
    "firday": "friday", "saterday": "saturday", "satruday": "saturday",
    "satuday": "saturday", "munday": "monday", "mondy": "monday",
    "sundya": "sunday", "suday": "sunday",
}

_TYPO_PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in _TYPOS) + r")\b", re.I)


def _fix_typos(text: str) -> str:
    return _TYPO_PATTERN.sub(lambda m: _TYPOS[m.group(1).lower()], text)


def parse_date_from_query(message: str) -> dict:
    """Parse date references from user message.

    Returns: {type: 'date'|'month'|'week'|None, dates: [...], label: str}
    """
    message_lower = _fix_typos(message.lower())
    today = get_now()
    result: dict = {"type": None, "dates": [], "label": ""}

    if "today" in message_lower:
        result["type"] = "date"
        result["dates"] = [today.strftime("%Y-%m-%d")]
        result["label"] = f"today ({today.strftime('%B %d, %Y')})"
        return result

    if "tomorrow" in message_lower:
        tomorrow = today + timedelta(days=1)
        result["type"] = "date"
        result["dates"] = [tomorrow.strftime("%Y-%m-%d")]
        result["label"] = f"tomorrow ({tomorrow.strftime('%B %d, %Y')})"
        return result

    if "yesterday" in message_lower:
        yesterday = today - timedelta(days=1)
        result["type"] = "date"
        result["dates"] = [yesterday.strftime("%Y-%m-%d")]
        result["label"] = f"yesterday ({yesterday.strftime('%B %d, %Y')})"
        return result

    if "this week" in message_lower:
        start_of_week = today - timedelta(days=today.weekday())
        dates = [(start_of_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        result["type"] = "week"
        result["dates"] = dates
        result["label"] = (
            f"this week ({start_of_week.strftime('%b %d')} - "
            f"{(start_of_week + timedelta(days=6)).strftime('%b %d')})"
        )
        return result

    if "next week" in message_lower:
        start_of_next_week = today + timedelta(days=(7 - today.weekday()))
        dates = [(start_of_next_week + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        result["type"] = "week"
        result["dates"] = dates
        result["label"] = (
            f"next week ({start_of_next_week.strftime('%b %d')} - "
            f"{(start_of_next_week + timedelta(days=6)).strftime('%b %d')})"
        )
        return result

    # Specific weekday — "next friday" vs "friday"
    for day_name, day_num in WEEKDAY_NAMES.items():
        if day_name in message_lower:
            days_ahead = day_num - today.weekday()
            if "next" in message_lower:
                days_ahead += 7
            if days_ahead <= 0:
                days_ahead += 7
            target_date = today + timedelta(days=days_ahead)
            result["type"] = "date"
            result["dates"] = [target_date.strftime("%Y-%m-%d")]
            result["label"] = f"{day_name.capitalize()} ({target_date.strftime('%B %d, %Y')})"
            return result

    # Specific date patterns (check BEFORE bare month names)
    date_patterns = [
        r"(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?",
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(\w+)(?:\s*,?\s*(\d{4}))?",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, message_lower)
        if match:
            groups = match.groups()
            try:
                if groups[0].isdigit():
                    day = int(groups[0])
                    month_str = groups[1]
                else:
                    month_str = groups[0]
                    day = int(groups[1])

                if month_str in MONTH_NAMES:
                    month_num = MONTH_NAMES[month_str]
                    year = int(groups[2]) if groups[2] else today.year
                    if not groups[2] and month_num < today.month:
                        year += 1
                    target_date = datetime(year, month_num, day)
                    result["type"] = "date"
                    result["dates"] = [target_date.strftime("%Y-%m-%d")]
                    result["label"] = target_date.strftime("%B %d, %Y")
                    return result
            except (ValueError, TypeError):
                pass

    # Bare month names
    for month_name, month_num in MONTH_NAMES.items():
        if month_name in message_lower:
            year = today.year
            if month_num < today.month:
                year += 1
            num_days = calendar.monthrange(year, month_num)[1]
            dates = [f"{year}-{month_num:02d}-{d:02d}" for d in range(1, num_days + 1)]
            result["type"] = "month"
            result["dates"] = dates
            result["label"] = f"{month_name.capitalize()} {year}"
            return result

    return result
