"""Rich terminal formatting for Aion CLI."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from aion.google_cal import EventData

console = Console()


def print_banner() -> None:
    console.print(Panel(
        "[bold cyan]Aion[/] — AI Calendar Agent\n"
        "Type [bold]help[/] for commands, [bold]preferences[/] to configure, [bold]quit[/] to exit",
        border_style="cyan",
    ))


def print_status(gcal_ok: bool, ollama_ok: bool, ollama_model: str = "") -> None:
    from aion.config import get_config
    cfg = get_config()
    tz = cfg.get("timezone", "UTC")
    ollama_enabled = cfg.get("ollama_enabled", True)
    gcal = "[green]Connected[/]" if gcal_ok else "[red]Not logged in[/] (run: aion login)"
    console.print(f"  Google Calendar: {gcal}")
    if ollama_ok and ollama_enabled:
        label = f" ({ollama_model})" if ollama_model else ""
        console.print(f"  Ollama{label}: [green]Available[/]")
    elif ollama_ok and not ollama_enabled:
        label = f" ({ollama_model})" if ollama_model else ""
        console.print(f"  Ollama{label}: [yellow]Disabled[/] (type [bold]preferences[/] to re-enable)")
    else:
        console.print("  Ollama: [yellow]Not running[/] (type [bold]setup[/] to enable smart commands)")
    console.print(f"  Timezone: [bold]{tz}[/]")
    console.print()


def print_help() -> None:
    t = Table(title="Commands", show_header=True, border_style="dim")
    t.add_column("Action", style="bold cyan")
    t.add_column("Examples")
    t.add_row("Schedule", '"schedule gym tomorrow morning", "add meeting at 3pm"')
    t.add_row("List", '"what\'s on today?", "show my calendar this week"')
    t.add_row("Delete", '"cancel gym tomorrow", "delete meeting"')
    t.add_row("Update", '"move gym to 3pm", "reschedule meeting to friday"')
    t.add_row("Free slots", '"when am I free tomorrow?", "free slots this week"')
    t.add_row("Best time", '"best time for a 2h study session"')
    t.add_row("Login", '"login" — connect Google Calendar')
    t.add_row("Logout", '"logout" — disconnect Google Calendar')
    t.add_row("Preferences", '"preferences" — blocked times & defaults')
    t.add_row("Setup", '"setup" — enable smart command understanding (Ollama)')
    t.add_row("Quit", '"quit" or "exit"')
    console.print(t)


def print_events(events: list[EventData], label: str = "") -> None:
    if not events:
        console.print(f"  No events{' for ' + label if label else ''}.")
        return

    t = Table(title=f"Events — {label}" if label else "Events", show_header=True, border_style="dim")
    t.add_column("Date", style="dim")
    t.add_column("Time", style="bold")
    t.add_column("Event", style="cyan")
    t.add_column("Duration", justify="right")

    current_date = None
    for ev in events:
        date_display = ""
        if ev.date != current_date:
            current_date = ev.date
            try:
                date_display = datetime.strptime(ev.date, "%Y-%m-%d").strftime("%a %b %d")
            except ValueError:
                date_display = ev.date
        t.add_row(date_display, ev.time, ev.title, f"{ev.duration} min")

    console.print(t)


def print_free_slots(slots: list[dict], label: str = "") -> None:
    if not slots:
        console.print("  No free slots found.")
        return
    console.print(f"\n  [bold]Free slots — {label}[/]" if label else "\n  [bold]Free slots[/]")
    for s in slots:
        console.print(f"  [green]\u2022[/] {s['start']} — {s['end']} ({s['duration_mins']} min)")
    console.print()


def print_optimal_slot(slot: dict) -> None:
    console.print(f"\n  [bold green]Best slot:[/] {slot['time']} ({slot.get('duration', 60)} min)")
    if slot.get("date"):
        try:
            console.print(f"  Date: {datetime.strptime(slot['date'], '%Y-%m-%d').strftime('%A, %B %d')}")
        except ValueError:
            console.print(f"  Date: {slot['date']}")
    console.print()


def print_success(msg: str) -> None:
    console.print(f"  [bold green]\u2714[/] {msg}")


def print_error(msg: str) -> None:
    console.print(f"  [bold red]\u2716[/] {msg}")


def print_info(msg: str) -> None:
    console.print(f"  [dim]\u2139[/] {msg}")


def confirm(message: str) -> bool:
    return Confirm.ask(f"  {message}")


def print_preferences(prefs: dict) -> None:
    """Display current preferences as a Rich table."""
    from aion.config import get_config
    blocked = prefs.get("blocked_slots", [])
    default_pref = prefs.get("default_time_pref")
    ollama_enabled = get_config().get("ollama_enabled", True)

    ALL_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday"}
    WEEKENDS = {"saturday", "sunday"}

    state = "[green]On[/]" if ollama_enabled else "[yellow]Off[/]"
    console.print(f"  Smart commands (Ollama): {state}")

    if not blocked and not default_pref:
        console.print()
        return

    if blocked:
        t = Table(title="Preferences", show_header=True, border_style="dim")
        t.add_column("#", style="dim", justify="right")
        t.add_column("Block", style="bold cyan")
        t.add_column("Days")
        t.add_column("Time")
        t.add_column("Until")

        for i, slot in enumerate(blocked, 1):
            days_set = set(slot.get("days", []))
            if days_set == ALL_DAYS:
                days_label = "Every day"
            elif days_set == WEEKDAYS:
                days_label = "Weekdays"
            elif days_set == WEEKENDS:
                days_label = "Weekends"
            else:
                days_label = ", ".join(d.capitalize()[:3] for d in slot["days"])

            until = slot.get("until")
            if until:
                try:
                    until_label = datetime.strptime(until, "%Y-%m-%d").strftime("%b %d")
                except ValueError:
                    until_label = until
            else:
                until_label = "Always"

            t.add_row(
                str(i),
                slot.get("label", "Blocked"),
                days_label,
                f"{slot['start']} - {slot['end']}",
                until_label,
            )
        console.print(t)

    if default_pref:
        console.print(f"\n  Default time preference: [bold]{default_pref}[/]")
    console.print()


def guided_fallback() -> str | None:
    """Show options and return the chosen intent, or None."""
    console.print("\n  I didn't fully understand that. Did you mean to:")
    console.print("    [bold]1.[/] Schedule an event")
    console.print("    [bold]2.[/] List events")
    console.print("    [bold]3.[/] Find free slots")
    console.print("    [bold]4.[/] Something else (try simpler phrasing)")
    console.print()
    from rich.prompt import Prompt
    choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="4")
    return {"1": "SCHEDULE", "2": "LIST", "3": "FIND_FREE"}.get(choice)
