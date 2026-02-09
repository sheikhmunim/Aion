"""
Clingo-based Schedule Solver for Calendar App.
Uses Answer Set Programming to find optimal time slots for events.
"""

import clingo
from typing import Optional

from .asp_model import ASPModel


class ScheduleSolver:
    """
    Constraint-based scheduler using Clingo ASP solver.
    Delegates ASP program generation to ASPModel.
    """

    def __init__(self):
        self.model = ASPModel()

    def find_available_slots(
        self,
        events: list[dict],
        request: dict,
        max_solutions: int = 5
    ) -> list[list[dict]]:
        """
        Find available time slots that satisfy the request constraints.

        Args:
            events: List of existing events
            request: Scheduling request dict
            max_solutions: Maximum number of alternative solutions

        Returns:
            List of solutions, each solution is a list of scheduled slots
        """
        # Get relevant dates
        if request.get("date"):
            dates = [request["date"]]
        else:
            dates = self.model.get_week_dates()

        # Build ASP program
        program = self.model.generate_full_program(events, request, dates)

        # Create Clingo control
        ctl = clingo.Control([f"--models={max_solutions}", "--opt-mode=optN"])
        ctl.add("base", [], program)

        try:
            ctl.ground([("base", [])])
        except Exception as e:
            return [{"error": f"Grounding error: {str(e)}"}]

        # Collect solutions
        solutions = []

        def on_model(model):
            solution = []
            for atom in model.symbols(shown=True):
                if atom.name == "schedule":
                    activity = str(atom.arguments[0]).strip('"')
                    day = str(atom.arguments[1])
                    slot = atom.arguments[2].number

                    # Find the actual date for this day
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
                        "duration": request.get("duration", 60)
                    })

            # Sort by day and time
            solution.sort(key=lambda x: (x.get("date", ""), x["slot"]))
            solutions.append(solution)

        # Solve
        result = ctl.solve(on_model=on_model)

        if not solutions:
            return []

        return solutions

    def find_free_slots(
        self,
        events: list[dict],
        date: str,
        min_duration: int = 30
    ) -> list[dict]:
        """
        Find all free time slots on a specific date.

        Args:
            events: List of existing events
            date: Date to check (YYYY-MM-DD)
            min_duration: Minimum slot duration in minutes

        Returns:
            List of free time slots with start, end, duration
        """
        # Get busy slots for this date
        busy_slots = set()
        for event in events:
            if event["date"] == date:
                start = self.model.time_to_slot(event["time"])
                duration = self.model.duration_to_slots(event["duration"])
                for slot in range(start, min(start + duration, self.model.total_slots)):
                    busy_slots.add(slot)

        # Find contiguous free slots
        free_slots = []
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
                            "date": date
                        })
                    current_start = None

        # Handle free slot at end of day
        if current_start is not None:
            duration_mins = (self.model.total_slots - current_start) * 30
            if duration_mins >= min_duration:
                free_slots.append({
                    "start": self.model.slot_to_time(current_start),
                    "end": self.model.slot_to_time(self.model.total_slots),
                    "duration_mins": duration_mins,
                    "date": date
                })

        return free_slots


# ==========================================
# Convenience function for quick testing
# ==========================================

def test_solver():
    """Quick test of the solver."""
    solver = ScheduleSolver()

    # Sample events
    events = [
        {"date": "2026-02-09", "time": "09:00", "duration": 60, "title": "Meeting"},
        {"date": "2026-02-09", "time": "14:00", "duration": 90, "title": "Workshop"},
        {"date": "2026-02-10", "time": "10:00", "duration": 60, "title": "Call"},
    ]

    # Request: find time for 1-hour gym session, prefer morning
    request = {
        "activity": "gym",
        "duration": 60,
        "count": 1,
        "date": "2026-02-09",
        "prefer_morning": True
    }

    print("Finding available slots for gym session...")
    solutions = solver.find_available_slots(events, request)

    if solutions:
        print(f"Found {len(solutions)} solution(s):")
        for i, sol in enumerate(solutions):
            print(f"  Solution {i+1}: {sol}")
    else:
        print("No available slots found.")

    # Find all free slots
    print("\nFree slots on 2026-02-09:")
    free = solver.find_free_slots(events, "2026-02-09")
    for slot in free:
        print(f"  {slot['start']} - {slot['end']} ({slot['duration_mins']} mins)")


if __name__ == "__main__":
    test_solver()
