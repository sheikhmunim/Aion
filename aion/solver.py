"""Clingo-based Schedule Solver — finds optimal time slots for events."""

import clingo

from aion.asp_model import ASPModel
from aion.config import get_preferences


class ScheduleSolver:
    def __init__(self):
        self.model = ASPModel()

    def find_available_slots(
        self, events: list[dict], request: dict, max_solutions: int = 5
    ) -> list[list[dict]]:
        if request.get("date"):
            dates = [request["date"]]
        else:
            dates = self.model.get_week_dates()

        program = self.model.generate_full_program(events, request, dates)

        # Inject user preference constraints
        prefs = get_preferences()
        blocked = prefs.get("blocked_slots", [])
        if blocked:
            target_date = request.get("date")
            program += self.model.generate_preference_constraints(blocked, target_date)
        ctl = clingo.Control([f"--models={max_solutions}", "--opt-mode=optN"])
        ctl.add("base", [], program)

        try:
            ctl.ground([("base", [])])
        except Exception as e:
            return [{"error": f"Grounding error: {str(e)}"}]

        solutions: list[list[dict]] = []

        def on_model(model):
            solution = []
            for atom in model.symbols(shown=True):
                if atom.name == "schedule":
                    activity = str(atom.arguments[0]).strip('"')
                    day = str(atom.arguments[1])
                    slot = atom.arguments[2].number
                    actual_date = None
                    if request.get("date"):
                        actual_date = request["date"]
                    else:
                        for date in dates:
                            if self.model.date_to_weekday(date) == day:
                                actual_date = date
                                break
                    solution.append({
                        "activity": activity,
                        "day": day,
                        "date": actual_date,
                        "time": self.model.slot_to_time(slot),
                        "slot": slot,
                        "duration": request.get("duration", 60),
                    })
            solution.sort(key=lambda x: (x.get("date", ""), x["slot"]))
            solutions.append(solution)

        ctl.solve(on_model=on_model)
        return solutions if solutions else []

    def find_free_slots(
        self, events: list[dict], date: str, min_duration: int = 30
    ) -> list[dict]:
        busy_slots: set[int] = set()
        for event in events:
            if event["date"] == date:
                start = self.model.time_to_slot(event["time"])
                duration = self.model.duration_to_slots(event["duration"])
                for slot in range(start, min(start + duration, self.model.total_slots)):
                    busy_slots.add(slot)

        # Also block preference slots
        prefs = get_preferences()
        weekday = self.model.date_to_weekday(date)
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        for block in prefs.get("blocked_slots", []):
            until = block.get("until")
            if until and until < today:
                continue
            if weekday not in block.get("days", []):
                continue
            start = self.model.time_to_slot(block["start"])
            end = self.model.time_to_slot(block["end"])
            for slot in range(start, min(end, self.model.total_slots)):
                busy_slots.add(slot)

        free_slots: list[dict] = []
        current_start = None

        for slot in range(self.model.total_slots):
            if slot not in busy_slots:
                if current_start is None:
                    current_start = slot
            else:
                if current_start is not None:
                    duration_mins = (slot - current_start) * 30
                    if duration_mins >= min_duration:
                        free_slots.append({
                            "start": self.model.slot_to_time(current_start),
                            "end": self.model.slot_to_time(slot),
                            "duration_mins": duration_mins,
                            "date": date,
                        })
                    current_start = None

        if current_start is not None:
            duration_mins = (self.model.total_slots - current_start) * 30
            if duration_mins >= min_duration:
                free_slots.append({
                    "start": self.model.slot_to_time(current_start),
                    "end": self.model.slot_to_time(self.model.total_slots),
                    "duration_mins": duration_mins,
                    "date": date,
                })

        return free_slots
