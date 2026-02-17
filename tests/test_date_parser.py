"""Tests for date parsing."""

from datetime import datetime, timedelta

from aion.date_parser import parse_date_from_query


class TestRelativeDates:
    def test_today(self):
        result = parse_date_from_query("what's on today?")
        assert result["type"] == "date"
        assert result["dates"] == [datetime.now().strftime("%Y-%m-%d")]
        assert "today" in result["label"]

    def test_tomorrow(self):
        result = parse_date_from_query("schedule gym tomorrow")
        expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert result["type"] == "date"
        assert result["dates"] == [expected]

    def test_yesterday(self):
        result = parse_date_from_query("what happened yesterday?")
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result["type"] == "date"
        assert result["dates"] == [expected]


class TestWeekDates:
    def test_this_week(self):
        result = parse_date_from_query("show events this week")
        assert result["type"] == "week"
        assert len(result["dates"]) == 7

    def test_next_week(self):
        result = parse_date_from_query("what's on next week?")
        assert result["type"] == "week"
        assert len(result["dates"]) == 7


class TestWeekdayNames:
    def test_friday(self):
        result = parse_date_from_query("schedule gym friday")
        assert result["type"] == "date"
        assert len(result["dates"]) == 1
        dt = datetime.strptime(result["dates"][0], "%Y-%m-%d")
        assert dt.weekday() == 4  # Friday

    def test_monday(self):
        result = parse_date_from_query("meeting on monday")
        assert result["type"] == "date"
        dt = datetime.strptime(result["dates"][0], "%Y-%m-%d")
        assert dt.weekday() == 0  # Monday


class TestSpecificDates:
    def test_month_day(self):
        result = parse_date_from_query("schedule for feb 20")
        assert result["type"] == "date"
        assert "02-20" in result["dates"][0]

    def test_month_day_year(self):
        result = parse_date_from_query("event on march 15 2026")
        assert result["type"] == "date"
        assert result["dates"] == ["2026-03-15"]


class TestNoMatch:
    def test_no_date(self):
        result = parse_date_from_query("hello there")
        assert result["type"] is None
        assert result["dates"] == []

    def test_empty(self):
        result = parse_date_from_query("")
        assert result["type"] is None
