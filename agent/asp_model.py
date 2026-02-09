"""
ASP Model for Calendar Scheduling.
Generates Answer Set Programs for the Clingo solver.
Can be run standalone to inspect/test the raw ASP rules.
"""

from datetime import datetime, timedelta


class ASPModel:
    """
    Generates ASP (Answer Set Programming) rules for calendar scheduling.

    Time model:
    - Day divided into 30-minute slots (0-31)
    - Slot 0 = 6:00 AM, Slot 31 = 9:30 PM
    - Working hours: slots 6-24 (9:00 AM - 6:00 PM)
    """

    def __init__(self):
        self.slots_per_hour = 2  # 30-minute slots
        self.day_start_hour = 6  # 6:00 AM
        self.day_end_hour = 22   # 10:00 PM
        self.total_slots = (self.day_end_hour - self.day_start_hour) * self.slots_per_hour

    # ==========================================
    # Time Conversion Utilities
    # ==========================================

    def time_to_slot(self, time_str: str) -> int:
        """
        Convert "HH:MM" to slot number.
        Example: "09:00" -> 6, "09:30" -> 7
        """
        h, m = map(int, time_str.split(":"))
        return (h - self.day_start_hour) * self.slots_per_hour + (1 if m >= 30 else 0)

    def slot_to_time(self, slot: int) -> str:
        """
        Convert slot number to "HH:MM".
        Example: 6 -> "09:00", 7 -> "09:30"
        """
        h = self.day_start_hour + slot // self.slots_per_hour
        m = 30 if slot % self.slots_per_hour else 0
        return f"{h:02d}:{m:02d}"

    def duration_to_slots(self, minutes: int) -> int:
        """Convert duration in minutes to number of slots."""
        return (minutes + 29) // 30  # Round up

    def date_to_weekday(self, date_str: str) -> str:
        """Convert "YYYY-MM-DD" to weekday name."""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A").lower()

    def get_week_dates(self, start_date: str = None) -> list[str]:
        """Get list of dates for the week starting from start_date or today."""
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            start = datetime.now()

        # Go to start of week (Monday)
        start = start - timedelta(days=start.weekday())

        return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    # ==========================================
    # ASP Program Generation
    # ==========================================

    def generate_base_program(self) -> str:
        """Generate base ASP rules for time slots and days."""
        return f"""
% Time slots: 0 to {self.total_slots - 1} (30-min blocks from 6AM to 10PM)
time_slot(0..{self.total_slots - 1}).

% Days of the week
day(monday; tuesday; wednesday; thursday; friday; saturday; sunday).

% Day types
weekday(monday; tuesday; wednesday; thursday; friday).
weekend(saturday; sunday).

% Working hours: 9AM-6PM (slots 6-24)
working_hour(6..24).

% Morning: 6AM-12PM (slots 0-12)
morning(0..12).

% Afternoon: 12PM-6PM (slots 12-24)
afternoon(12..24).

% Evening: 6PM-10PM (slots 24-32)
evening(24..{self.total_slots - 1}).
"""

    def generate_busy_constraints(self, events: list[dict], dates: list[str] = None) -> str:
        """
        Generate busy time constraints from existing events.

        Args:
            events: List of event dicts with date, time, duration
            dates: Optional list of dates to filter events
        """
        lines = ["\n% Busy times from existing events"]

        for event in events:
            # Filter by dates if provided
            if dates and event["date"] not in dates:
                continue

            weekday = self.date_to_weekday(event["date"])
            start_slot = self.time_to_slot(event["time"])
            duration_slots = self.duration_to_slots(event["duration"])

            # Mark each slot as busy
            for slot in range(start_slot, min(start_slot + duration_slots, self.total_slots)):
                lines.append(f'busy({weekday}, {slot}, "{event["date"]}").')

        return "\n".join(lines)

    def generate_scheduling_request(self, request: dict) -> str:
        """
        Generate ASP rules for a scheduling request.

        Request dict fields:
            - activity: str - name of the activity
            - duration: int - duration in minutes
            - count: int - number of sessions (default 1)
            - days: list[str] - allowed days (default all)
            - date: str - specific date (optional)
            - prefer_morning: bool - prefer morning slots
            - prefer_afternoon: bool - prefer afternoon slots
            - prefer_evening: bool - prefer evening slots
            - avoid_weekends: bool - don't schedule on weekends
            - working_hours_only: bool - only during 9-6
        """
        activity = request.get("activity", "event").replace(" ", "_").lower()
        duration_slots = self.duration_to_slots(request.get("duration", 60))
        count = request.get("count", 1)
        specific_date = request.get("date")

        lines = [f"\n% Scheduling request: {activity}"]
        lines.append(f'activity("{activity}").')
        lines.append(f'duration("{activity}", {duration_slots}).')
        lines.append(f'need_count("{activity}", {count}).')

        # If specific date provided, constrain to that day
        if specific_date:
            weekday = self.date_to_weekday(specific_date)
            lines.append(f'allowed_day("{activity}", {weekday}).')
            lines.append(f'target_date("{activity}", "{specific_date}").')
        else:
            # Use specified days or all days
            allowed_days = request.get("days", ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])
            for day in allowed_days:
                lines.append(f'allowed_day("{activity}", {day}).')

        # Core scheduling rule: generate exactly 'count' scheduled slots
        lines.append(f"""
% Generate exactly {count} time slot(s) for {activity}
{count} {{ schedule("{activity}", D, T) : allowed_day("{activity}", D), time_slot(T) }} {count}.
""")

        # No overlap with busy times
        lines.append(f"""
% Cannot overlap with busy times
:- schedule("{activity}", D, T), busy(D, T, _).
""")

        # Duration constraint - ensure all slots for duration are free
        if duration_slots > 1:
            lines.append(f"""
% Ensure full duration is available (no busy slots within duration)
:- schedule("{activity}", D, T), duration("{activity}", Dur),
   Offset = 1..Dur-1, busy(D, T+Offset, _).
""")

        # Don't exceed day boundary
        lines.append(f"""
% Don't exceed end of day
:- schedule("{activity}", D, T), duration("{activity}", Dur), T + Dur > {self.total_slots}.
""")

        # Spread across different days if multiple sessions
        if count > 1:
            lines.append(f"""
% Spread sessions across different days
:- schedule("{activity}", D, T1), schedule("{activity}", D, T2), T1 != T2.
""")

        # Preference: avoid weekends
        if request.get("avoid_weekends"):
            lines.append(f"""
% Avoid weekends
:- schedule("{activity}", D, _), weekend(D).
""")

        # Preference: working hours only
        if request.get("working_hours_only"):
            lines.append(f"""
% Only during working hours (9AM-6PM)
:- schedule("{activity}", D, T), not working_hour(T).
""")

        # Optimization: prefer morning/afternoon/evening
        if request.get("prefer_morning"):
            lines.append(f"""
% Optimization: prefer morning slots (minimize slot number for morning)
#minimize {{ T@1,D : schedule("{activity}", D, T), not morning(T) }}.
#minimize {{ T@2,D : schedule("{activity}", D, T) }}.
""")
        elif request.get("prefer_afternoon"):
            lines.append(f"""
% Optimization: prefer afternoon slots
#minimize {{ 1@1,D,T : schedule("{activity}", D, T), not afternoon(T) }}.
""")
        elif request.get("prefer_evening"):
            lines.append(f"""
% Optimization: prefer evening slots
#minimize {{ 1@1,D,T : schedule("{activity}", D, T), not evening(T) }}.
""")
        else:
            # Default: prefer earlier in the day
            lines.append(f"""
% Default: prefer earlier time slots
#minimize {{ T@1,D : schedule("{activity}", D, T) }}.
""")

        # Show scheduled times
        lines.append(f'\n#show schedule/3.')

        return "\n".join(lines)

    def generate_full_program(self, events: list[dict], request: dict, dates: list[str] = None) -> str:
        """
        Generate the complete ASP program for a scheduling request.

        Args:
            events: List of existing events
            request: Scheduling request dict
            dates: Optional list of dates to scope the program
        """
        program = self.generate_base_program()
        program += self.generate_busy_constraints(events, dates)
        program += self.generate_scheduling_request(request)
        return program


if __name__ == "__main__":
    model = ASPModel()

    # Sample events
    events = [
        {"date": "2026-02-09", "time": "09:00", "duration": 60, "title": "Meeting"},
        {"date": "2026-02-09", "time": "14:00", "duration": 90, "title": "Workshop"},
        {"date": "2026-02-10", "time": "10:00", "duration": 60, "title": "Call"},
    ]

    # Sample request
    request = {
        "activity": "gym",
        "duration": 60,
        "count": 1,
        "date": "2026-02-09",
        "prefer_morning": True,
    }

    print("=" * 60)
    print("ASP Program (standalone R&D mode)")
    print("=" * 60)
    print(model.generate_full_program(events, request, dates=["2026-02-09"]))
