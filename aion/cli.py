"""Interactive CLI chat loop and command routing."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from rich.console import Console
from rich.prompt import Prompt

from aion import display
from aion.config import clear_tokens, get_config, get_tokens
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


async def handle_schedule(cmd: ParsedCommand, gcal: GoogleCalendar, solver: ScheduleSolver) -> None:
    if not cmd.activity:
        display.print_error("What would you like to schedule? Try: schedule gym tomorrow morning")
        return

    date = cmd.dates[0] if cmd.dates else datetime.now().strftime("%Y-%m-%d")
    duration = cmd.duration or int(get_config().get("default_duration", 60))

    # Explicit time — skip solver
    if cmd.time:
        label = cmd.date_label or date
        if display.confirm(f"Schedule '{cmd.activity}' on {label} at {cmd.time} for {duration} min?"):
            with console.status("Creating event..."):
                ev = await gcal.create_event(cmd.activity, date, cmd.time, duration)
            display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")
        return

    # Use ASP solver to find optimal slot
    display.print_info(f"Finding optimal slot for '{cmd.activity}'...")

    with console.status("Fetching calendar..."):
        events = await gcal.list_events(date)

    request = {
        "activity": cmd.activity,
        "duration": duration,
        "date": date,
        "prefer_morning": cmd.time_pref == "morning",
        "prefer_afternoon": cmd.time_pref == "afternoon",
        "prefer_evening": cmd.time_pref == "evening",
    }
    solutions = solver.find_available_slots([e.to_dict() for e in events], request)

    if not solutions or (isinstance(solutions[0], dict) and "error" in solutions[0]):
        display.print_error("No available slots found. Calendar may be full for this date.")
        return

    best = solutions[0][0]
    label = cmd.date_label or best["date"]

    if display.confirm(f"Schedule '{cmd.activity}' on {label} at {best['time']} for {duration} min?"):
        with console.status("Creating event..."):
            ev = await gcal.create_event(cmd.activity, best["date"], best["time"], duration)
        display.print_success(f"Created! '{ev.title}' on {ev.date} at {ev.time}")


async def handle_list(cmd: ParsedCommand, gcal: GoogleCalendar) -> None:
    date = cmd.dates[0] if cmd.dates else None
    label = cmd.date_label or ("today" if not date else date)
    with console.status("Fetching events..."):
        events = await gcal.list_events(date)
    display.print_events(events, label)


async def handle_delete(cmd: ParsedCommand, gcal: GoogleCalendar) -> None:
    if not cmd.activity:
        display.print_error("Which event to delete? Try: cancel gym tomorrow")
        return

    # Default to today if no date specified — most deletes are for today's events
    date = cmd.dates[0] if cmd.dates else datetime.now().strftime("%Y-%m-%d")
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
    date = cmd.dates[0] if cmd.dates else datetime.now().strftime("%Y-%m-%d")
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
    request = {
        "activity": cmd.activity or "event",
        "duration": duration,
        "prefer_morning": cmd.time_pref == "morning",
        "prefer_afternoon": cmd.time_pref == "afternoon",
        "prefer_evening": cmd.time_pref == "evening",
    }
    if date:
        request["date"] = date

    solutions = solver.find_available_slots([e.to_dict() for e in events], request)
    if not solutions or (isinstance(solutions[0], dict) and "error" in solutions[0]):
        display.print_error("No available slots found.")
        return

    display.print_optimal_slot(solutions[0][0])


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

    if gcal is None:
        display.print_error("Not logged in. Run 'login' first to connect Google Calendar.")
        return True

    cmd = await classify(text)

    match cmd.intent:
        case "HELP":
            display.print_help()
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
    # Direct `aion login` subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        from aion.auth import login
        try:
            console.print("Opening browser for Google login...")
            await login()
            display.print_success("Logged in! Google Calendar connected.")
        except Exception as e:
            display.print_error(str(e))
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
