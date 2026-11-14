from datetime import date, timedelta

import pytest

from app import recurrence as r
from app.parser import parse

MON = date(2026, 11, 2)  # a Monday


# ---------- recurrence ----------
def n(rule):
    return r.normalize(rule)


def test_daily_interval():
    rule = n({"type": "daily", "interval": 3})
    assert r.next_after(rule, MON, MON) == MON + timedelta(days=3)
    assert r.first_on_or_after(rule, MON, MON + timedelta(days=1)) == MON + timedelta(days=3)


def test_weekly_days_and_interval():
    rule = n({"type": "weekly", "days": [1, 4]})  # Tue, Fri
    assert r.next_after(rule, MON, MON) == date(2026, 11, 3)
    assert r.next_after(rule, MON, date(2026, 11, 3)) == date(2026, 11, 6)
    biweekly = n({"type": "weekly", "interval": 2, "days": [0]})
    assert r.next_after(biweekly, MON, MON) == date(2026, 11, 16)


def test_monthly_variants():
    assert r.next_after(n({"type": "monthly", "day": 31}), date(2026, 1, 31), date(2026, 1, 31)) == date(2026, 2, 28)
    assert r.next_after(n({"type": "monthly", "day": -1}), MON, MON) == date(2026, 11, 30)
    second_tue = n({"type": "monthly", "nth": 2, "weekday": 1})
    assert r.next_after(second_tue, MON, MON) == date(2026, 11, 10)
    assert r.next_after(second_tue, MON, date(2026, 11, 10)) == date(2026, 12, 8)
    last_fri = n({"type": "monthly", "nth": -1, "weekday": 4})
    assert r.next_after(last_fri, MON, MON) == date(2026, 11, 27)
    quarterly = n({"type": "monthly", "interval": 3, "day": 20})
    assert r.next_after(quarterly, date(2026, 11, 20), date(2026, 11, 20)) == date(2027, 2, 20)


def test_yearly_leap_day():
    rule = n({"type": "yearly", "month": 2, "day": 29})
    assert r.next_after(rule, MON, MON) == date(2027, 2, 28)


def test_bad_rules():
    for bad in ({"type": "nope"}, {"type": "weekly", "days": [9]}, {"type": "monthly", "day": 40}):
        with pytest.raises(r.RuleError):
            r.normalize(bad)


# ---------- parser ----------
@pytest.mark.parametrize("text,title,due,time,rule", [
    ("call dentist tomorrow at 3pm !!", "call dentist", date(2026, 11, 3), "15:00", None),
    ("water plants every 3 days", "water plants", None, None, {"type": "daily", "interval": 3}),
    ("change filter 30 days after done", "change filter", None, None, {"type": "after", "days": 30}),
    ("standup every weekday at 9:30", "standup", None, "09:30", {"type": "weekly", "days": [0, 1, 2, 3, 4]}),
    ("bins every tue and fri at 7pm", "bins", None, "19:00", {"type": "weekly", "interval": 1, "days": [1, 4]}),
    ("review budget every last sunday", "review budget", None, None, {"type": "monthly", "nth": -1, "weekday": 6}),
    ("fix the monday report", "fix the monday report", None, None, None),
    ("meet at 5", "meet", None, "17:00", None),
    ("dentist friday", "dentist", date(2026, 11, 6), None, None),
])
def test_parser(text, title, due, time, rule):
    p = parse(text, MON)
    assert (p.title, p.due_date, p.due_time, p.recurrence) == (title, due, time, rule)


def test_split_bulk_markers_and_subtasks():
    from app.parser import split_bulk
    text = "Plan party saturday !!\n  + invites\n  - cake\n+ balloons\n\n- [ ] buy milk\n1. call mum\n  2) nested\n"
    assert split_bulk(text) == [
        ("Plan party saturday !!", ["invites", "cake", "balloons"]),
        ("buy milk", []),
        ("call mum", ["nested"]),
    ]
    assert split_bulk("  first line indented\n+ sub") == [("first line indented", ["sub"])]


