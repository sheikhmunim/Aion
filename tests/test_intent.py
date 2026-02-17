"""Tests for intent classification and entity extraction."""

from aion.intent import regex_classify


class TestScheduleIntent:
    def test_schedule_basic(self):
        cmd = regex_classify("schedule gym tomorrow morning")
        assert cmd.intent == "SCHEDULE"
        assert cmd.activity == "gym"
        assert cmd.time_pref == "morning"
        assert len(cmd.dates) == 1

    def test_schedule_with_time(self):
        cmd = regex_classify("schedule meeting at 3pm")
        assert cmd.intent == "SCHEDULE"
        assert cmd.time == "15:00"

    def test_schedule_with_duration(self):
        cmd = regex_classify("schedule study session for 2 hours")
        assert cmd.intent == "SCHEDULE"
        assert cmd.duration == 120

    def test_add_synonym(self):
        cmd = regex_classify("add dentist appointment tomorrow")
        assert cmd.intent == "SCHEDULE"
        assert "dentist" in cmd.activity

    def test_book_synonym(self):
        cmd = regex_classify("book lunch meeting at 12pm")
        assert cmd.intent == "SCHEDULE"
        assert cmd.time == "12:00"

    def test_create_synonym(self):
        cmd = regex_classify("create team standup tomorrow at 9am")
        assert cmd.intent == "SCHEDULE"
        assert cmd.time == "09:00"


class TestListIntent:
    def test_whats_on(self):
        cmd = regex_classify("what's on my calendar today?")
        assert cmd.intent == "LIST"
        assert len(cmd.dates) == 1

    def test_show_events(self):
        cmd = regex_classify("show my events this week")
        assert cmd.intent == "LIST"

    def test_list_keyword(self):
        cmd = regex_classify("list events tomorrow")
        assert cmd.intent == "LIST"


class TestDeleteIntent:
    def test_cancel(self):
        cmd = regex_classify("cancel gym tomorrow")
        assert cmd.intent == "DELETE"
        assert cmd.activity == "gym"

    def test_delete(self):
        cmd = regex_classify("delete my meeting")
        assert cmd.intent == "DELETE"

    def test_remove(self):
        cmd = regex_classify("remove dentist appointment")
        assert cmd.intent == "DELETE"


class TestUpdateIntent:
    def test_move(self):
        cmd = regex_classify("move gym to 3pm")
        assert cmd.intent == "UPDATE"

    def test_reschedule(self):
        cmd = regex_classify("reschedule meeting to friday")
        assert cmd.intent == "UPDATE"


class TestFindFreeIntent:
    def test_free_slots(self):
        cmd = regex_classify("when am I free tomorrow?")
        assert cmd.intent == "FIND_FREE"
        assert len(cmd.dates) == 1

    def test_available(self):
        cmd = regex_classify("find available slots this week")
        assert cmd.intent == "FIND_FREE"


class TestFindOptimalIntent:
    def test_best_time(self):
        cmd = regex_classify("find the best time for a 2 hour study session")
        assert cmd.intent == "FIND_OPTIMAL"
        assert cmd.duration == 120

    def test_suggest(self):
        cmd = regex_classify("suggest a time for gym")
        assert cmd.intent == "FIND_OPTIMAL"


class TestHelpIntent:
    def test_help(self):
        cmd = regex_classify("help")
        assert cmd.intent == "HELP"

    def test_commands(self):
        cmd = regex_classify("commands")
        assert cmd.intent == "HELP"


class TestTimeExtraction:
    def test_12h_pm(self):
        cmd = regex_classify("schedule meeting at 3pm")
        assert cmd.time == "15:00"

    def test_12h_am(self):
        cmd = regex_classify("schedule run at 6am")
        assert cmd.time == "06:00"

    def test_12h_with_minutes(self):
        cmd = regex_classify("schedule call at 2:30pm")
        assert cmd.time == "14:30"

    def test_24h(self):
        cmd = regex_classify("schedule lunch at 13:00")
        assert cmd.time == "13:00"

    def test_noon(self):
        cmd = regex_classify("schedule meeting at 12pm")
        assert cmd.time == "12:00"


class TestDurationExtraction:
    def test_hours(self):
        cmd = regex_classify("schedule study for 2 hours")
        assert cmd.duration == 120

    def test_minutes(self):
        cmd = regex_classify("schedule call for 45 minutes")
        assert cmd.duration == 45

    def test_short_h(self):
        cmd = regex_classify("schedule gym for 1.5h")
        assert cmd.duration == 90


class TestTimePrefExtraction:
    def test_morning(self):
        cmd = regex_classify("schedule gym morning")
        assert cmd.time_pref == "morning"

    def test_afternoon(self):
        cmd = regex_classify("schedule meeting afternoon")
        assert cmd.time_pref == "afternoon"

    def test_evening(self):
        cmd = regex_classify("schedule dinner evening")
        assert cmd.time_pref == "evening"

    def test_night_maps_to_evening(self):
        cmd = regex_classify("schedule study night")
        assert cmd.time_pref == "evening"


class TestConfidence:
    def test_known_intent_high_confidence(self):
        cmd = regex_classify("schedule gym tomorrow")
        assert cmd.confidence >= 0.9

    def test_unknown_low_confidence(self):
        cmd = regex_classify("asdfghjkl")
        assert cmd.confidence < 0.5

    def test_empty_zero_confidence(self):
        cmd = regex_classify("")
        assert cmd.confidence == 0.0
