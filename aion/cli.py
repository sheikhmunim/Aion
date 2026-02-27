"""Interactive CLI chat loop and command routing."""

from __future__ import annotations

import asyncio
import re
import sys

from rich.console import Console
from rich.prompt import Prompt

from aion import display
from aion.config import clear_tokens, get_config, get_now, get_preferences, get_tokens, save_config, save_preferences
from aion.google_cal import EventData, GoogleCalendar
from aion.intent import ParsedCommand, classify, classify_all
from aion.ollama import ollama_available, reset_status
from aion.solver import ScheduleSolver

console = Console()


_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Matches "delete/cancel/remove <number>" in the raw user input — used as a
# fallback when Ollama hallucinates an activity name instead of passing "1" through.
_NUMERIC_DELETE_RE = re.compile(
    r"\b(?:delete|cancel|remove)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.I,
)

# Session history queries — "what did I schedule?", "what have I added this session?"
# Only matched when there is NO date qualifier in the text (date-qualified queries
# like "what did I schedule today" are routed to Google Calendar via classify_all).
_SESSION_HISTORY_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:did|have)\s+i\s+(?:scheduled?|added?|created?|booked?)(?:\s+(?:so\s+far|this\s+session))?"
    r"|what\s+(?:i\s+)?(?:have\s+)?(?:scheduled?|added?|created?)"
    r"|show\s+(?:my\s+)?(?:recent\s+)?(?:session\s+)?(?:activity|history|log)"
    r"|(?:session|recent)\s+(?:activity|history|log|events?)"
    r"|what\s+(?:i\s+)?(?:have\s+)?done\s+this\s+session"
    r")\b",
    re.I,
)

# Pronouns / anaphoric references meaning "the last event I mentioned"
_ANAPHORA_RE = re.compile(
    r"^(?:that|it|this|the\s+last\s+(?:one|event)?|last\s+(?:one|event)?|the\s+one)\s*$",
    re.I,
)
_ANAPHORA_IN_TEXT_RE = re.compile(
    r"\b(?:delete|cancel|remove|reschedule|update|move)\s+(?:that|it|this)\b",
    re.I,
)


class SessionContext:
    """Per-session ephemeral memory — lives only while aion is running, never written to disk.

    Two layers:
    - last_title / last_date  → most recent event for pronoun resolution ("delete that")
    - history                 → log of every event *created* this session ("what did I schedule?")
    """

    __slots__ = ("last_title", "last_date", "history")

    def __init__(self) -> None:
        self.last_title: str | None = None
        self.last_date: str | None = None
        self.history: list[EventData] = []

    def record(self, ev: EventData) -> None:
        """Update recent-event memory. Call for create, delete, and update."""
        self.last_title = ev.title
        self.last_date = ev.date

    def record_created(self, ev: EventData) -> None:
        """Record a newly created event — updates recent memory AND appends to history."""
        self.record(ev)
        self.history.append(ev)


def _parse_event_index(activity: str | None) -> int | None:
    """Return 1-based index if activity is a digit string or number word, else None."""
    if not activity:
        return None
    s = activity.strip().lower()
    if s.isdigit():
        return int(s)
    return _NUMBER_WORDS.get(s)


def _find_event_by_title(events: list[EventData], title: str) -> EventData | None:
    """Find an event by fuzzy title match."""
    if not title:
        return None
    t = title.lower()
    for ev in events:
        if ev.title.lower() == t:
            return ev
    for ev in events:
        if t in ev.title.lower() or ev.title.lower() in t:
            return ev
    return None


def _check_preference_block(date: str, time: str, duration: int) -> list[dict]:
    """Check if a proposed time overlaps with preference-blocked slots."""
    from aion.asp_model import ASPModel
    model = ASPModel()
    prefs = get_preferences()
    weekday = model.date_to_weekday(date)
    today = get_now().strftime("%Y-%m-%d")

    try:
        new_start = model.time_to_slot(time)
    except (ValueError, IndexError):
        return []
    new_end = new_start + model.duration_to_slots(duration)

    hits = []
    for block in prefs.get("blocked_slots", []):
        until = block.get("until")
        if until and until < today:
            continue
        if weekday not in block.get("days", []):
            continue
        block_start = model.time_to_slot(block["start"])
        block_end = model.time_to_slot(block["end"])
        if new_start < block_end and new_end > block_start:
            hits.append(block)
    return hits


def _check_conflict(events: list[EventData], date: str, time: str, duration: int) -> list[EventData]:
    """Check if a proposed time slot conflicts with existing events."""
    from aion.asp_model import ASPModel
    model = ASPModel()
    try:
        new_start = model.time_to_slot(time)
    except (ValueError, IndexError):
        return []
    new_end = new_start + model.duration_to_slots(duration)

    conflicts = []
    for ev in events:
        if ev.date != date:
            continue
        try:
            ev_start = model.time_to_slot(ev.time)
        except (ValueError, IndexError):
            continue
        ev_end = ev_start + model.duration_to_slots(ev.duration)
        if new_start < ev_end and new_end > ev_start:
            conflicts.append(ev)
    return conflicts


async def handle_schedule(
    cmd: ParsedCommand,
    gcal: GoogleCalendar,
    solver: ScheduleSolver,
    ctx: SessionContext | None = None,
    auto_confirm: bool = False,
) -> None:
    if not cmd.activity and not cmd.label:
        display.print_error("What would you like to schedule? Try: schedule gym tomorrow morning")
        return

    title = cmd.title  # label if set, otherwise activity
    date = cmd.dates[0] if cmd.dates else get_now().strftime("%Y-%m-%d")
    duration = cmd.duration or int(get_config().get("default_duration", 60))

    # Always fetch existing events for conflict checking
    with console.status("Fetching calendar..."):
        events = await gcal.list_events(date)

    # Explicit time given — check for conflicts and preference blocks
    if cmd.time:
        conflicts = _check_conflict(events, date, cmd.time, duration)
        pref_blocks = _check_preference_block(date, cmd.time, duration)

        has_issue = bool(conflicts or pref_blocks)
        if has_issue:
            if conflicts:
                display.print_error(f"Conflict! '{cmd.time}' overlaps with:")
                for c in conflicts:
                    console.print(f"    - {c.time} — {c.title} ({c.duration} min)")
            if pref_blocks:
                display.print_error(f"'{cmd.time}' falls in a blocked time slot:")
                for b in pref_blocks:
                    console.print(f"    - {b.get('label', 'Blocked')} ({b['start']} - {b['end']})")

            if auto_confirm:
                # Batch mode: warn but create anyway — user already confirmed the batch
                display.print_info("Creating anyway (batch confirmed).")
                with console.status("Creating event..."):
                    ev = await gcal.create_event(title, date, cmd.time, duration)
                display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time} (override)")
                if ctx:
                    ctx.record_created(ev)
                return

            console.print()
            console.print("  What would you like to do?")
            console.print("    [bold]1.[/] Find the next best slot (recommended)")
            console.print("    [bold]2.[/] Schedule anyway (override)")
            console.print("    [bold]3.[/] Cancel")

            choice = Prompt.ask("  Choose", choices=["1", "2", "3"], default="1")
            if choice == "3":
                return
            elif choice == "2":
                with console.status("Creating event..."):
                    ev = await gcal.create_event(title, date, cmd.time, duration)
                display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time} (override)")
                if ctx:
                    ctx.record_created(ev)
                return
            # choice == "1": fall through to solver below
        else:
            if not auto_confirm:
                date_display = cmd.date_label or date
                if not display.confirm(
                    f"Schedule '{title}' on {date_display} at {cmd.time} for {duration} min?"
                ):
                    return  # user declined
            with console.status("Creating event..."):
                ev = await gcal.create_event(title, date, cmd.time, duration)
            display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")
            if ctx:
                ctx.record_created(ev)
            return

    # Use ASP solver to find optimal slot
    display.print_info(f"Finding optimal slot for '{title}'...")

    # Use user's default time preference if none specified
    time_pref = cmd.time_pref or get_preferences().get("default_time_pref")

    request = {
        "activity": cmd.activity or title,
        "duration": duration,
        "date": date,
        "prefer_morning": time_pref == "morning",
        "prefer_afternoon": time_pref == "afternoon",
        "prefer_evening": time_pref == "evening",
    }
    solutions = solver.find_available_slots([e.to_dict() for e in events], request)

    if not solutions or (isinstance(solutions[0], dict) and "error" in solutions[0]):
        display.print_error("No available slots found. Calendar may be full for this date.")
        return

    # Collect all candidate slots from solver (deduplicate by time)
    seen_times: set[str] = set()
    all_slots: list[dict] = []
    for group in solutions:
        for s in (group if isinstance(group, list) else [group]):
            key = f"{s.get('date')}_{s['time']}"
            if key not in seen_times:
                seen_times.add(key)
                all_slots.append(s)
    slot_idx = 0

    while True:
        # Suggest the current solver slot if available
        if slot_idx < len(all_slots):
            best = all_slots[slot_idx]
            date_display = cmd.date_label or best["date"]

            if display.confirm(f"Schedule '{title}' on {date_display} at {best['time']} for {duration} min?"):
                with console.status("Creating event..."):
                    ev = await gcal.create_event(title, best["date"], best["time"], duration)
                display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")
                if ctx:
                    ctx.record_created(ev)
                return

            slot_idx += 1

        # Always show alternatives after declining (or when solver slots exhausted)
        has_more = slot_idx < len(all_slots)

        console.print()
        console.print("  What would you prefer?")
        if has_more:
            next_slot = all_slots[slot_idx]
            console.print(f"    [bold]1.[/] Try next slot ({next_slot['time']})")
        console.print("    [bold]2.[/] Pick a time preference (morning / afternoon / evening)")
        console.print("    [bold]3.[/] Enter a specific time")
        console.print("    [bold]4.[/] Cancel")

        valid = ["1", "2", "3", "4"] if has_more else ["2", "3", "4"]
        choice = Prompt.ask("  Choose", choices=valid, default="3")

        if choice == "4":
            return

        if choice == "1" and has_more:
            continue  # loop will suggest next slot

        if choice == "2":
            pref = Prompt.ask("  Preference", choices=["morning", "afternoon", "evening"])
            request["prefer_morning"] = pref == "morning"
            request["prefer_afternoon"] = pref == "afternoon"
            request["prefer_evening"] = pref == "evening"
            new_solutions = solver.find_available_slots([e.to_dict() for e in events], request)
            if new_solutions and not (isinstance(new_solutions[0], dict) and "error" in new_solutions[0]):
                seen_times.clear()
                all_slots.clear()
                for group in new_solutions:
                    for s in (group if isinstance(group, list) else [group]):
                        key = f"{s.get('date')}_{s['time']}"
                        if key not in seen_times:
                            seen_times.add(key)
                            all_slots.append(s)
                slot_idx = 0
                continue
            display.print_error(f"No {pref} slots available.")
            continue

        if choice == "3":
            from aion.intent import _extract_time
            while True:
                time_str = Prompt.ask("  Enter time (e.g. 2pm, 14:00)")
                parsed_time = _extract_time(f"at {time_str}")
                if parsed_time:
                    break
                display.print_error(f"Couldn't parse '{time_str}'. Try formats like 2pm, 14:00, 9:30am.")

            # Check event conflicts
            conflicts = _check_conflict(events, date, parsed_time, duration)
            if conflicts:
                display.print_error(f"Conflict at {parsed_time} with:")
                for c in conflicts:
                    console.print(f"    - {c.time} — {c.title} ({c.duration} min)")
                if not display.confirm("Schedule anyway (overlap)?"):
                    continue

            # Check preference blocks
            pref_blocks = _check_preference_block(date, parsed_time, duration)
            if pref_blocks:
                display.print_error(f"'{parsed_time}' falls in a blocked time slot:")
                for b in pref_blocks:
                    console.print(f"    - {b.get('label', 'Blocked')} ({b['start']} - {b['end']})")
                if not display.confirm("Schedule anyway (override preference)?"):
                    continue

            with console.status("Creating event..."):
                ev = await gcal.create_event(title, date, parsed_time, duration)
            display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")
            if ctx:
                ctx.record_created(ev)
            return


async def handle_list(cmd: ParsedCommand, gcal: GoogleCalendar) -> None:
    label = cmd.date_label or ("today" if not cmd.dates else cmd.dates[0])

    if len(cmd.dates) > 1:
        # Date range (e.g. "this week") — fetch from first to last date
        with console.status("Fetching events..."):
            events = await gcal.list_events_range(cmd.dates[0], cmd.dates[-1])
    else:
        date = cmd.dates[0] if cmd.dates else None
        with console.status("Fetching events..."):
            events = await gcal.list_events(date)

    display.print_events(events, label)


async def handle_delete(cmd: ParsedCommand, gcal: GoogleCalendar, ctx: SessionContext | None = None) -> None:
    # Anaphora: "delete that", "cancel it", "remove this"
    is_anaphora = (
        (cmd.activity and _ANAPHORA_RE.match(cmd.activity.strip()))
        or (not cmd.activity and cmd.raw and _ANAPHORA_IN_TEXT_RE.search(cmd.raw))
    )
    if is_anaphora:
        if ctx and ctx.last_title:
            cmd.activity = ctx.last_title
            if ctx.last_date and not cmd.dates:
                cmd.dates = [ctx.last_date]
        else:
            display.print_error("No recent event in memory. Try: cancel gym tomorrow")
            return

    if not cmd.activity:
        display.print_error("Which event to delete? Try: cancel gym tomorrow, or list events then: delete 1")
        return

    # Numeric reference — "delete 1", "delete two", etc.
    # Check cmd.activity first; fall back to scanning raw text because Ollama
    # sometimes hallucinates an activity name instead of forwarding the bare number.
    idx = _parse_event_index(cmd.activity)
    if idx is None and cmd.raw:
        m = _NUMERIC_DELETE_RE.search(cmd.raw)
        if m:
            idx = _parse_event_index(m.group(1))
    if idx is not None:
        date = cmd.dates[0] if cmd.dates else get_now().strftime("%Y-%m-%d")
        with console.status("Fetching events..."):
            events = await gcal.list_events(date)
        if not events:
            display.print_error(f"No events found for {date}.")
            return
        if idx < 1 or idx > len(events):
            display.print_error(f"No event #{idx} — there are {len(events)} event(s) on this date:")
            display.print_events(events)
            return
        event = events[idx - 1]
        if display.confirm(f"Delete '{event.title}' on {event.date} at {event.time}?"):
            with console.status("Deleting event..."):
                await gcal.delete_event(event.id)
            display.print_success(f"Deleted '{event.title}'")
            if ctx:
                ctx.record(event)
        return

    # Default to today if no date specified — most deletes are for today's events
    date = cmd.dates[0] if cmd.dates else get_now().strftime("%Y-%m-%d")
    with console.status("Fetching events..."):
        events = await gcal.list_events(date)

    # Also search upcoming events if not found on that date
    event = _find_event_by_title(events, cmd.activity)
    if not event and not cmd.dates:
        with console.status("Searching upcoming events..."):
            events = await gcal.list_events()
        event = _find_event_by_title(events, cmd.activity)

    if not event:
        display.print_error(f"No event matching '{cmd.activity}' found.")
        if events:
            display.print_info("Events on this date:")
            display.print_events(events)
        return

    if display.confirm(f"Delete '{event.title}' on {event.date} at {event.time}?"):
        with console.status("Deleting event..."):
            await gcal.delete_event(event.id)
        display.print_success(f"Deleted '{event.title}'")
        if ctx:
            ctx.record(event)


async def handle_update(cmd: ParsedCommand, gcal: GoogleCalendar, ctx: SessionContext | None = None) -> None:
    # Anaphora: "reschedule that to 3pm", "move it to tomorrow"
    is_anaphora = (
        (cmd.activity and _ANAPHORA_RE.match(cmd.activity.strip()))
        or (not cmd.activity and cmd.raw and _ANAPHORA_IN_TEXT_RE.search(cmd.raw))
    )
    if is_anaphora:
        if ctx and ctx.last_title:
            cmd.activity = ctx.last_title
            if ctx.last_date and not cmd.dates:
                cmd.dates = [ctx.last_date]
        else:
            display.print_error("No recent event in memory. Try: move gym to 3pm")
            return

    if not cmd.activity:
        display.print_error("Which event to update? Try: move gym to 3pm")
        return

    with console.status("Fetching events..."):
        events = await gcal.list_events()

    event = _find_event_by_title(events, cmd.activity)
    if not event:
        display.print_error(f"No event matching '{cmd.activity}' found.")
        return

    changes: dict = {}
    if cmd.time:
        changes["time"] = cmd.time
    if cmd.dates:
        changes["date"] = cmd.dates[0]
    if cmd.duration:
        changes["duration"] = cmd.duration

    if not changes:
        display.print_error("What should I change? Try: move gym to 3pm")
        return

    desc = ", ".join(f"{k}={v}" for k, v in changes.items())
    if display.confirm(f"Update '{event.title}': {desc}?"):
        with console.status("Updating event..."):
            updated = await gcal.update_event(event.id, **changes)
        display.print_success(f"Updated '{updated.title}' — {updated.date} at {updated.time}")
        if ctx:
            ctx.record(updated)


async def handle_find_free(cmd: ParsedCommand, gcal: GoogleCalendar, solver: ScheduleSolver) -> None:
    date = cmd.dates[0] if cmd.dates else get_now().strftime("%Y-%m-%d")
    label = cmd.date_label or date

    with console.status("Fetching events..."):
        events = await gcal.list_events(date)

    slots = solver.find_free_slots([e.to_dict() for e in events], date)
    display.print_free_slots(slots, label)


async def handle_find_optimal(cmd: ParsedCommand, gcal: GoogleCalendar, solver: ScheduleSolver) -> None:
    date = cmd.dates[0] if cmd.dates else None

    with console.status("Fetching events..."):
        events = await gcal.list_events(date)

    duration = cmd.duration or int(get_config().get("default_duration", 60))
    time_pref = cmd.time_pref or get_preferences().get("default_time_pref")
    request = {
        "activity": cmd.activity or "event",
        "duration": duration,
        "prefer_morning": time_pref == "morning",
        "prefer_afternoon": time_pref == "afternoon",
        "prefer_evening": time_pref == "evening",
    }
    if date:
        request["date"] = date

    solutions = solver.find_available_slots([e.to_dict() for e in events], request)
    if not solutions or (isinstance(solutions[0], dict) and "error" in solutions[0]):
        display.print_error("No available slots found.")
        return

    display.print_optimal_slot(solutions[0][0])


ALL_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]
WEEKENDS = ["saturday", "sunday"]


def handle_preferences() -> None:
    """Interactive preferences menu."""
    prefs = get_preferences()
    raw_cfg = get_config().get("preferences", {})

    display.print_preferences(prefs)

    ollama_on = get_config().get("ollama_enabled", True)
    toggle_label = "Disable" if ollama_on else "Enable"

    console.print("  What would you like to do?")
    console.print("    [bold]1.[/] Add a blocked time slot")
    console.print("    [bold]2.[/] Remove a blocked slot")
    console.print("    [bold]3.[/] Change default time preference")
    console.print(f"    [bold]4.[/] {toggle_label} smart commands (Ollama)")
    console.print("    [bold]5.[/] Back")
    console.print()

    choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4", "5"], default="5")

    if choice == "5":
        return

    if choice == "1":
        # Add a blocked slot
        label = Prompt.ask("  Label (e.g. Lunch break)")

        console.print("  Which days?")
        console.print("    [bold]1.[/] Every day")
        console.print("    [bold]2.[/] Weekdays (Mon-Fri)")
        console.print("    [bold]3.[/] Weekends (Sat-Sun)")
        console.print("    [bold]4.[/] Specific days")
        day_choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="1")

        if day_choice == "1":
            days = ALL_DAYS[:]
        elif day_choice == "2":
            days = WEEKDAYS[:]
        elif day_choice == "3":
            days = WEEKENDS[:]
        else:
            while True:
                days_input = Prompt.ask("  Days (comma-separated, e.g. monday,wednesday,friday)")
                days = [d.strip().lower() for d in days_input.split(",") if d.strip().lower() in ALL_DAYS]
                if days:
                    break
                display.print_error("No valid days entered. Use full names like monday, tuesday.")

        import re
        time_pat = re.compile(r"^\d{1,2}:\d{2}$")

        while True:
            start = Prompt.ask("  Start time (e.g. 09:00)")
            if not time_pat.match(start):
                display.print_error("Invalid time format. Use HH:MM (e.g. 09:00).")
                continue
            break

        while True:
            end = Prompt.ask("  End time (e.g. 17:00)")
            if not time_pat.match(end):
                display.print_error("Invalid time format. Use HH:MM (e.g. 17:00).")
                continue
            if end <= start:
                display.print_error(f"End time must be after {start}.")
                continue
            break

        console.print("  How long should this block last?")
        console.print("    [bold]1.[/] Always (permanent)")
        console.print("    [bold]2.[/] Until a specific date")
        until_choice = Prompt.ask("  Choose", choices=["1", "2"], default="1")

        until = None
        if until_choice == "2":
            while True:
                until = Prompt.ask("  Until date (YYYY-MM-DD)")
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", until):
                    display.print_error("Invalid date format. Use YYYY-MM-DD.")
                    continue
                break

        new_slot = {"label": label, "days": days, "start": start, "end": end, "until": until}

        all_slots = raw_cfg.get("blocked_slots", [])
        all_slots.append(new_slot)
        raw_cfg["blocked_slots"] = all_slots
        save_preferences(raw_cfg)
        display.print_success(f"Added blocked slot: {label}")

    elif choice == "2":
        # Remove a blocked slot
        blocked = prefs.get("blocked_slots", [])
        if not blocked:
            display.print_info("No blocked slots to remove.")
            return

        choices = [str(i) for i in range(1, len(blocked) + 1)]
        pick = Prompt.ask("  Which slot to remove? (enter number)", choices=choices)
        idx = int(pick) - 1
        slot = blocked[idx]

        if display.confirm(f"Remove '{slot.get('label', 'Blocked')}'?"):
            # Remove from the raw config (which may include expired entries)
            all_slots = raw_cfg.get("blocked_slots", [])
            # Match by label+start+end+days to find the right one
            all_slots = [
                s for s in all_slots
                if not (s.get("label") == slot.get("label") and s.get("start") == slot.get("start")
                        and s.get("end") == slot.get("end") and s.get("days") == slot.get("days"))
            ]
            raw_cfg["blocked_slots"] = all_slots
            save_preferences(raw_cfg)
            display.print_success(f"Removed '{slot.get('label', 'Blocked')}'")

    elif choice == "3":
        # Change default time preference
        pref = Prompt.ask("  Default time preference", choices=["morning", "afternoon", "evening", "none"])
        raw_cfg["default_time_pref"] = None if pref == "none" else pref
        save_preferences(raw_cfg)
        if pref == "none":
            display.print_success("Cleared default time preference.")
        else:
            display.print_success(f"Default time preference set to: {pref}")

    elif choice == "4":
        # Toggle smart commands (Ollama)
        cfg = get_config()
        new_state = not cfg.get("ollama_enabled", True)
        cfg["ollama_enabled"] = new_state
        save_config(cfg)
        if new_state:
            display.print_success("Smart commands enabled.")
        else:
            display.print_success("Smart commands disabled.")


def _find_chain_conflicts(cmds: list[ParsedCommand]) -> set[int]:
    """Return 0-based indices of commands that overlap with another command in the chain.

    Uses ASP slot arithmetic (30-min slots, 6AM base) — the same model the solver uses —
    so conflict detection is consistent with what the solver would enforce.
    Only SCHEDULE commands with an explicit date AND time are checked.
    """
    from aion.asp_model import ASPModel
    model = ASPModel()
    default_duration = int(get_config().get("default_duration", 60))

    # Collect only schedulable commands (SCHEDULE + date + explicit time)
    schedulable: list[tuple[int, ParsedCommand]] = [
        (i, cmd) for i, cmd in enumerate(cmds)
        if cmd.intent == "SCHEDULE" and cmd.dates and cmd.time
    ]

    conflicts: set[int] = set()
    for a in range(len(schedulable)):
        for b in range(a + 1, len(schedulable)):
            i, cmd_a = schedulable[a]
            j, cmd_b = schedulable[b]

            if cmd_a.dates[0] != cmd_b.dates[0]:
                continue  # different dates — no overlap possible

            try:
                start_a = model.time_to_slot(cmd_a.time)
                start_b = model.time_to_slot(cmd_b.time)
            except (ValueError, IndexError):
                continue

            end_a = start_a + model.duration_to_slots(cmd_a.duration or default_duration)
            end_b = start_b + model.duration_to_slots(cmd_b.duration or default_duration)

            if start_a < end_b and start_b < end_a:
                conflicts.add(i)
                conflicts.add(j)

    return conflicts


async def _presolve_timeless(
    cmds: list[ParsedCommand],
    gcal: GoogleCalendar,
    solver: ScheduleSolver,
) -> tuple[list[int], list[int]]:
    """Run the ASP solver for every SCHEDULE command that has no explicit time.

    Mutates cmd.time in-place.  Accumulates already-solved commands as pending
    busy events so the solver for command N+1 doesn't double-book command N.

    Returns (solved_indices, failed_indices) — both are 0-based.
    """
    default_duration = int(get_config().get("default_duration", 60))
    pending_events: list[dict] = []   # fake events for already-solved commands
    solved: list[int] = []
    failed: list[int] = []

    for i, cmd in enumerate(cmds):
        if cmd.intent != "SCHEDULE" or cmd.time:
            continue  # already has a time or not a schedule command

        date = cmd.dates[0] if cmd.dates else get_now().strftime("%Y-%m-%d")
        if not cmd.dates:
            cmd.dates = [date]

        duration = cmd.duration or default_duration
        time_pref = cmd.time_pref or get_preferences().get("default_time_pref")

        with console.status(f"  Finding slot for '{cmd.title}'..."):
            cal_events = await gcal.list_events(date)

        request = {
            "activity": cmd.activity or "event",
            "duration": duration,
            "date": date,
            "prefer_morning": time_pref == "morning",
            "prefer_afternoon": time_pref == "afternoon",
            "prefer_evening": time_pref == "evening",
        }

        # Include already-solved commands on the same date as busy slots
        same_day_pending = [e for e in pending_events if e["date"] == date]
        all_events = [e.to_dict() for e in cal_events] + same_day_pending

        solutions = solver.find_available_slots(all_events, request)

        if solutions and not (isinstance(solutions[0], dict) and "error" in solutions[0]):
            first = solutions[0]
            slot = first[0] if isinstance(first, list) else first
            cmd.time = slot["time"]
            solved.append(i)
            pending_events.append({
                "date": date,
                "time": cmd.time,
                "duration": duration,
                "title": cmd.title or "pending",
            })
        else:
            failed.append(i)

    return solved, failed


async def handle_multichain(cmds: list[ParsedCommand], gcal: GoogleCalendar, solver: ScheduleSolver, ctx: SessionContext | None = None) -> None:
    """Interactive preview-and-execute loop for multi-command inputs."""
    from aion.intent import _extract_time
    from aion.date_parser import parse_date_from_query

    while True:
        conflicts = _find_chain_conflicts(cmds)
        display.print_multicommand_preview(cmds, conflicts)
        console.print("  [bold]1.[/] Confirm all   [bold]2.[/] Edit   [bold]3.[/] Cancel")
        choice = Prompt.ask("  Choose", choices=["1", "2", "3"])

        if choice == "3":
            display.print_info("Cancelled.")
            return

        if choice == "2":
            n = len(cmds)
            idx_str = Prompt.ask(
                "  Which command?",
                choices=[str(i) for i in range(1, n + 1)],
            )
            cmd = cmds[int(idx_str) - 1]
            console.print(
                "  [bold]A.[/] Activity   [bold]D.[/] Date   "
                "[bold]T.[/] Time   [bold]M.[/] Duration   [bold]B.[/] Back"
            )
            field = Prompt.ask("  Choose", choices=["A", "D", "T", "M", "B"], case_sensitive=False).upper()

            if field == "B":
                continue

            if field == "A":
                val = Prompt.ask("  New activity")
                cmd.activity = val
                cmd.label = None

            elif field == "D":
                val = Prompt.ask("  New date (e.g. tomorrow, friday, Mar 5)")
                result = parse_date_from_query(val)
                if result.get("dates"):
                    cmd.dates = result["dates"]
                    cmd.date_label = result.get("label", "")
                else:
                    display.print_error(f"Couldn't parse '{val}' as a date.")

            elif field == "T":
                val = Prompt.ask("  New time (e.g. 9am, 14:00)")
                parsed_time = _extract_time(f"at {val}")
                if parsed_time:
                    cmd.time = parsed_time
                else:
                    display.print_error(f"Couldn't parse '{val}'. Try formats like 9am, 14:00.")

            elif field == "M":
                val = Prompt.ask("  Duration in minutes")
                try:
                    cmd.duration = int(val)
                except ValueError:
                    display.print_error(f"'{val}' is not a valid number.")

            continue  # re-show preview after edit

        # choice == "1": pre-solve timeless SCHEDULE commands, then execute
        needs_solving = any(
            cmd.intent == "SCHEDULE" and not cmd.time for cmd in cmds
        )
        if needs_solving:
            solved, failed = await _presolve_timeless(cmds, gcal, solver)

            if failed:
                titles = ", ".join(
                    f"'{cmds[i].title}'" for i in failed if cmds[i].title
                )
                display.print_error(
                    f"No available slot found for: {titles}. "
                    "Set a time manually via Edit, or they will be skipped."
                )

            if solved:
                # Re-show preview with suggested times marked
                conflicts = _find_chain_conflicts(cmds)
                display.print_multicommand_preview(cmds, conflicts, suggested=set(solved))
                console.print(
                    "  [dim]Times marked [bold](auto)[/bold] were suggested by the scheduler.[/]"
                )
                console.print("  [bold]1.[/] Confirm all   [bold]2.[/] Edit   [bold]3.[/] Cancel")
                inner = Prompt.ask("  Choose", choices=["1", "2", "3"])
                if inner == "3":
                    display.print_info("Cancelled.")
                    return
                if inner == "2":
                    continue  # back to main edit loop with times now populated

        # Execute all commands
        for i, cmd in enumerate(cmds):
            if cmd.intent == "SCHEDULE" and not cmd.time:
                display.print_error(f"No time for '{cmd.title}' — skipping.")
                continue
            console.print(f"\n  [bold]── Command {i + 1}/{len(cmds)} ──[/]")
            try:
                match cmd.intent:
                    case "SCHEDULE":
                        await handle_schedule(cmd, gcal, solver, ctx, auto_confirm=True)
                    case "LIST":
                        await handle_list(cmd, gcal)
                    case "DELETE":
                        await handle_delete(cmd, gcal, ctx)
                    case "UPDATE":
                        await handle_update(cmd, gcal, ctx)
                    case "FIND_FREE":
                        await handle_find_free(cmd, gcal, solver)
                    case "FIND_OPTIMAL":
                        await handle_find_optimal(cmd, gcal, solver)
                    case _:
                        display.print_error(f"Unknown intent '{cmd.intent}', skipping.")
            except Exception as e:
                display.print_error(f"Failed: {e}")
                choice2 = Prompt.ask(
                    "  [S]kip and continue  [X] Cancel remaining",
                    choices=["S", "X"],
                    default="S",
                ).upper()
                if choice2 == "X":
                    return
        return


async def handle_input(user_input: str, gcal: GoogleCalendar | None, solver: ScheduleSolver, ctx: SessionContext | None = None) -> bool:
    """Process one user input. Returns False to quit."""
    text = user_input.strip()
    if not text:
        return True
    if text.lower() in ("quit", "exit", "q"):
        return False

    # Login/logout don't need gcal
    if text.lower() == "login":
        from aion.auth import login
        try:
            with console.status("Opening browser for Google login..."):
                await login()
            display.print_success("Logged in! Google Calendar connected.")
        except Exception as e:
            display.print_error(str(e))
        return True

    if text.lower() == "logout":
        clear_tokens()
        display.print_success("Logged out. Tokens cleared.")
        return True

    if text.lower() == "help":
        display.print_help()
        return True

    if text.lower() in ("preferences", "prefs", "settings"):
        handle_preferences()
        return True

    if text.lower() in ("setup", "enable ollama", "setup ollama"):
        cfg = get_config()
        cfg.pop("ollama_setup_declined", None)
        save_config(cfg)
        from aion.setup import setup
        if setup():
            reset_status()
            display.print_success("Smart command understanding enabled!")
        else:
            display.print_error("Setup failed. You can also run: aion setup (from terminal)")
        return True

    if gcal is None:
        display.print_error("Not logged in. Run 'login' first to connect Google Calendar.")
        return True

    # Session history query — show events created this session (no calendar fetch needed).
    # Only intercept when there is no date qualifier; date-specific queries ("what did I
    # schedule today") fall through to classify_all → LIST → Google Calendar as normal.
    if _SESSION_HISTORY_RE.search(text) and ctx is not None:
        from aion.date_parser import parse_date_from_query
        if not parse_date_from_query(text).get("dates"):
            display.print_session_history(ctx.history)
            return True

    cmds = await classify_all(text)
    if len(cmds) > 1:
        await handle_multichain(cmds, gcal, solver, ctx)
        return True

    cmd = cmds[0]

    match cmd.intent:
        case "HELP":
            display.print_help()
        case "PREFERENCES":
            handle_preferences()
        case "SCHEDULE":
            await handle_schedule(cmd, gcal, solver, ctx)
        case "LIST":
            await handle_list(cmd, gcal)
        case "DELETE":
            await handle_delete(cmd, gcal, ctx)
        case "UPDATE":
            await handle_update(cmd, gcal, ctx)
        case "FIND_FREE":
            await handle_find_free(cmd, gcal, solver)
        case "FIND_OPTIMAL":
            await handle_find_optimal(cmd, gcal, solver)
        case _:
            chosen = display.guided_fallback()
            if chosen:
                cmd.intent = chosen
                match chosen:
                    case "SCHEDULE":
                        await handle_schedule(cmd, gcal, solver, ctx)
                    case "LIST":
                        await handle_list(cmd, gcal)
                    case "FIND_FREE":
                        await handle_find_free(cmd, gcal, solver)

    return True


async def async_main() -> None:
    """Async entry point."""
    # Direct subcommands: aion login / aion setup
    if len(sys.argv) > 1:
        subcmd = sys.argv[1]
        if subcmd == "login":
            from aion.auth import login
            try:
                console.print("Opening browser for Google login...")
                await login()
                display.print_success("Logged in! Google Calendar connected.")
            except Exception as e:
                display.print_error(str(e))
            return
        elif subcmd == "setup":
            from aion.setup import setup
            if setup():
                display.print_success("Smart command understanding is ready!")
            else:
                display.print_error("Setup failed. You can still use basic commands.")
            return

    display.print_banner()

    # Check connections
    gcal: GoogleCalendar | None = None
    gcal_ok = False
    if get_tokens():
        try:
            gcal = GoogleCalendar()
            gcal_ok = True
        except RuntimeError:
            pass

    reset_status()
    ollama_ok = ollama_available()
    ollama_model = get_config().get("ollama_model", "") if ollama_ok else ""

    # If Ollama was previously set up but server isn't running, start it silently
    _startup_cfg = get_config()
    if not ollama_ok and _startup_cfg.get("ollama_enabled") and not _startup_cfg.get("ollama_setup_declined"):
        from aion.setup import start_ollama
        if start_ollama():
            reset_status()
            ollama_ok = ollama_available()
            ollama_model = _startup_cfg.get("ollama_model", "")

    # First run: offer to set up Ollama for smart understanding
    elif not ollama_ok and not _startup_cfg.get("ollama_setup_declined"):
        from rich.prompt import Confirm as RichConfirm
        console.print()
        if RichConfirm.ask("  Enable smart command understanding? (auto-installs Ollama + ~2GB model download)", default=True):
            from aion.setup import setup
            if setup():
                reset_status()
                ollama_ok = ollama_available()
                ollama_model = get_config().get("ollama_model", "")
                display.print_success("Smart understanding enabled!")
            else:
                display.print_error("Setup failed. Using basic mode.")
        else:
            cfg = get_config()
            cfg["ollama_setup_declined"] = True
            save_config(cfg)

    display.print_status(gcal_ok, ollama_ok, ollama_model)

    solver = ScheduleSolver()
    ctx = SessionContext()

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]aion[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\nBye!")
            break

        try:
            if not await handle_input(user_input, gcal, solver, ctx):
                console.print("Bye!")
                break
        except Exception as e:
            display.print_error(f"Error: {e}")

        # Reconnect gcal after login
        if not gcal_ok and get_tokens():
            try:
                gcal = GoogleCalendar()
                gcal_ok = True
            except RuntimeError:
                pass

        # Re-check ollama after setup (ollama_available() returns cached value unless reset_status() was called)
        if not ollama_ok and ollama_available():
            ollama_ok = True
            ollama_model = get_config().get("ollama_model", "")


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
