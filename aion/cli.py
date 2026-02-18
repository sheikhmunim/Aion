"""Interactive CLI chat loop and command routing."""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.prompt import Prompt

from aion import display
from aion.config import clear_tokens, get_config, get_now, get_preferences, get_tokens, save_config, save_preferences
from aion.google_cal import EventData, GoogleCalendar
from aion.intent import ParsedCommand, classify
from aion.ollama import ollama_available, reset_status
from aion.solver import ScheduleSolver

console = Console()


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


async def handle_schedule(cmd: ParsedCommand, gcal: GoogleCalendar, solver: ScheduleSolver) -> None:
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
                return
            # choice == "1": fall through to solver below
        else:
            date_display = cmd.date_label or date
            if display.confirm(f"Schedule '{title}' on {date_display} at {cmd.time} for {duration} min?"):
                with console.status("Creating event..."):
                    ev = await gcal.create_event(title, date, cmd.time, duration)
                display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")
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


async def handle_delete(cmd: ParsedCommand, gcal: GoogleCalendar) -> None:
    if not cmd.activity:
        display.print_error("Which event to delete? Try: cancel gym tomorrow")
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


async def handle_update(cmd: ParsedCommand, gcal: GoogleCalendar) -> None:
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

    console.print("  What would you like to do?")
    console.print("    [bold]1.[/] Add a blocked time slot")
    console.print("    [bold]2.[/] Remove a blocked slot")
    console.print("    [bold]3.[/] Change default time preference")
    console.print("    [bold]4.[/] Back")
    console.print()

    choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="4")

    if choice == "4":
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


async def handle_input(user_input: str, gcal: GoogleCalendar | None, solver: ScheduleSolver) -> bool:
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

    if gcal is None:
        display.print_error("Not logged in. Run 'login' first to connect Google Calendar.")
        return True

    cmd = await classify(text)

    match cmd.intent:
        case "HELP":
            display.print_help()
        case "PREFERENCES":
            handle_preferences()
        case "SCHEDULE":
            await handle_schedule(cmd, gcal, solver)
        case "LIST":
            await handle_list(cmd, gcal)
        case "DELETE":
            await handle_delete(cmd, gcal)
        case "UPDATE":
            await handle_update(cmd, gcal)
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
                        await handle_schedule(cmd, gcal, solver)
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

    # First run: offer to set up Ollama for smart understanding
    if not ollama_ok and not get_config().get("ollama_setup_declined"):
        from rich.prompt import Confirm as RichConfirm
        console.print()
        if RichConfirm.ask("  Enable smart command understanding? (auto-installs Ollama, ~500MB download)", default=True):
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

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]aion[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\nBye!")
            break

        try:
            if not await handle_input(user_input, gcal, solver):
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


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
