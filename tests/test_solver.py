"""Tests for the ASP/Clingo schedule solver."""

from aion.solver import ScheduleSolver


class TestFindFreeSlots:
    def setup_method(self):
        self.solver = ScheduleSolver()

    def test_empty_calendar(self):
        slots = self.solver.find_free_slots([], "2026-02-18")
        assert len(slots) == 1
        assert slots[0]["start"] == "06:00"
        assert slots[0]["end"] == "22:00"
        assert slots[0]["duration_mins"] == 960  # 16 hours

    def test_one_event(self):
        events = [{"date": "2026-02-18", "time": "10:00", "duration": 60}]
        slots = self.solver.find_free_slots(events, "2026-02-18")
        assert len(slots) >= 2
        assert slots[0]["start"] == "06:00"
        assert slots[0]["end"] == "10:00"

    def test_min_duration_filter(self):
        events = [
            {"date": "2026-02-18", "time": "10:00", "duration": 30},
            {"date": "2026-02-18", "time": "11:00", "duration": 30},
        ]
        slots = self.solver.find_free_slots(events, "2026-02-18", min_duration=60)
        for s in slots:
            assert s["duration_mins"] >= 60

    def test_ignores_other_dates(self):
        events = [{"date": "2026-02-19", "time": "10:00", "duration": 60}]
        slots = self.solver.find_free_slots(events, "2026-02-18")
        assert len(slots) == 1
        assert slots[0]["duration_mins"] == 960


class TestFindAvailableSlots:
    def setup_method(self):
        self.solver = ScheduleSolver()

    def test_schedule_on_empty_day(self):
        request = {"activity": "gym", "duration": 60, "date": "2026-02-18"}
        solutions = self.solver.find_available_slots([], request)
        assert len(solutions) >= 1
        assert solutions[0][0]["activity"] == "gym"
        assert solutions[0][0]["date"] == "2026-02-18"

    def test_avoids_conflict(self):
        events = [{"date": "2026-02-18", "time": "09:00", "duration": 60}]
        request = {"activity": "meeting", "duration": 60, "date": "2026-02-18"}
        solutions = self.solver.find_available_slots(events, request)
        assert len(solutions) >= 1
        for sol in solutions:
            for item in sol:
                slot_start = self.solver.model.time_to_slot(item["time"])
                event_start = self.solver.model.time_to_slot("09:00")
                event_end = event_start + 2  # 60 min = 2 slots
                assert slot_start < event_start or slot_start >= event_end

    def test_prefer_morning(self):
        request = {
            "activity": "gym",
            "duration": 60,
            "date": "2026-02-18",
            "prefer_morning": True,
        }
        solutions = self.solver.find_available_slots([], request)
        assert len(solutions) >= 1
        best = solutions[0][0]
        hour = int(best["time"].split(":")[0])
        assert hour < 12
