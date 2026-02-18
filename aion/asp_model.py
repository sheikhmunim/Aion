"""ASP Model for Calendar Scheduling — generates Answer Set Programs for Clingo."""

from datetime import datetime, timedelta

from aion.config import get_now


class ASPModel:
    """Generates ASP rules for calendar scheduling.

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

    def time_to_slot(self, time_str: str) -> int:
        h, m = map(int, time_str.split(":"))
        return (h - self.day_start_hour) * self.slots_per_hour + (1 if m >= 30 else 0)

    def slot_to_time(self, slot: int) -> str:
        h = self.day_start_hour + slot // self.slots_per_hour
        m = 30 if slot % self.slots_per_hour else 0
        return f"{h:02d}:{m:02d}"

    def duration_to_slots(self, minutes: int) -> int:
        return (minutes + 29) // 30

    def date_to_weekday(self, date_str: str) -> str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A").lower()

    def get_week_dates(self, start_date: str | None = None) -> list[str]:
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            start = get_now()
        start = start - timedelta(days=start.weekday())
        return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    def generate_base_program(self) -> str:
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

    def generate_busy_constraints(self, events: list[dict], dates: list[str] | None = None) -> str:
        lines = ["\n% Busy times from existing events"]
        for event in events:
            if dates and event["date"] not in dates:
                continue
            weekday = self.date_to_weekday(event["date"])
            start_slot = self.time_to_slot(event["time"])
            duration_slots = self.duration_to_slots(event["duration"])
            for slot in range(start_slot, min(start_slot + duration_slots, self.total_slots)):
                lines.append(f'busy({weekday}, {slot}, "{event["date"]}").')
        return "\n".join(lines)

    def generate_scheduling_request(self, request: dict) -> str:
        activity = request.get("activity", "event").replace(" ", "_").lower()
        duration_slots = self.duration_to_slots(request.get("duration", 60))
        count = request.get("count", 1)
        specific_date = request.get("date")

        lines = [f"\n% Scheduling request: {activity}"]
        lines.append(f'activity("{activity}").')
        lines.append(f'duration("{activity}", {duration_slots}).')
        lines.append(f'need_count("{activity}", {count}).')

        if specific_date:
            weekday = self.date_to_weekday(specific_date)
            lines.append(f'allowed_day("{activity}", {weekday}).')
            lines.append(f'target_date("{activity}", "{specific_date}").')
        else:
            allowed_days = request.get("days", [
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
            ])
            for day in allowed_days:
                lines.append(f'allowed_day("{activity}", {day}).')

        lines.append(f"""
% Generate exactly {count} time slot(s) for {activity}
{count} {{ schedule("{activity}", D, T) : allowed_day("{activity}", D), time_slot(T) }} {count}.
""")

        lines.append(f"""
% Cannot overlap with busy times
:- schedule("{activity}", D, T), busy(D, T, _).
""")

        if duration_slots > 1:
            lines.append(f"""
% Ensure full duration is available
:- schedule("{activity}", D, T), duration("{activity}", Dur),
   Offset = 1..Dur-1, busy(D, T+Offset, _).
""")

        lines.append(f"""
% Don't exceed end of day
:- schedule("{activity}", D, T), duration("{activity}", Dur), T + Dur > {self.total_slots}.
""")

        if count > 1:
            lines.append(f"""
% Spread sessions across different days
:- schedule("{activity}", D, T1), schedule("{activity}", D, T2), T1 != T2.
""")

        if request.get("avoid_weekends"):
            lines.append(f"""
:- schedule("{activity}", D, _), weekend(D).
""")

        if request.get("working_hours_only"):
            lines.append(f"""
:- schedule("{activity}", D, T), not working_hour(T).
""")

        if request.get("prefer_morning"):
            lines.append(f"""
#minimize {{ T@1,D : schedule("{activity}", D, T), not morning(T) }}.
#minimize {{ T@2,D : schedule("{activity}", D, T) }}.
""")
        elif request.get("prefer_afternoon"):
            lines.append(f"""
#minimize {{ 1@1,D,T : schedule("{activity}", D, T), not afternoon(T) }}.
""")
        elif request.get("prefer_evening"):
            lines.append(f"""
#minimize {{ 1@1,D,T : schedule("{activity}", D, T), not evening(T) }}.
""")
        else:
            lines.append(f"""
#minimize {{ T@1,D : schedule("{activity}", D, T) }}.
""")

        lines.append("\n#show schedule/3.")
        return "\n".join(lines)

    def generate_preference_constraints(
        self, blocked_slots: list[dict], target_date: str | None = None
    ) -> str:
        """Convert blocked preference slots into ASP busy facts.

        If target_date is given, only generate facts for that date's weekday.
        """
        today = get_now().strftime("%Y-%m-%d")
        lines = ["\n% Blocked times from user preferences"]

        for block in blocked_slots:
            until = block.get("until")
            if until and until < today:
                continue

            start_slot = self.time_to_slot(block["start"])
            end_slot = self.time_to_slot(block["end"])

            if target_date:
                weekday = self.date_to_weekday(target_date)
                if weekday not in block.get("days", []):
                    continue
                for slot in range(start_slot, min(end_slot, self.total_slots)):
                    lines.append(f'busy({weekday}, {slot}, "preference").')
            else:
                for day in block.get("days", []):
                    for slot in range(start_slot, min(end_slot, self.total_slots)):
                        lines.append(f'busy({day}, {slot}, "preference").')

        return "\n".join(lines)

    def generate_full_program(self, events: list[dict], request: dict, dates: list[str] | None = None) -> str:
        program = self.generate_base_program()
        program += self.generate_busy_constraints(events, dates)
        program += self.generate_scheduling_request(request)
        return program
