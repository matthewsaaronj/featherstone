"""Fixed-date Featherstone schedule.

A verse's review dates come from the day it enters Daily (its start date) and
never shift because a review was done late:

    Daily     every day, starting on the start date          (daily_repeats)
    Weekly    every week on the start date's weekday         (weekly_repeats)
    Monthly   every month on the start date's day of month   (monthly_repeats)
              (a 29th–31st falls back to the month's last day)
    Mastered  3, 6, 12 months apart (mastered.review_months), then every
              yearly_interval months, on the same day of month

Each stage needs its full number of good reviews (Got it or Shaky). A review
stays open for a grace window after its date (grace_days: Daily same day only,
Weekly 3 days, Monthly 7, Mastered 14). Done late but inside the window, it
counts as that review. A "Missed" answer leaves it open to try again. When the
window closes without a good review, that review is lost: it's counted as
missed, and since the stage still needs its full number of good reviews, the
stage runs one review longer. A verse never drops back a stage.

Everything here is pure: plain dates in, new state out.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional

STAGES = ["backlog", "daily", "weekly", "monthly", "mastered"]
ACTIVE_STAGES = ["daily", "weekly", "monthly", "mastered"]
NEXT_STAGE = {"daily": "weekly", "weekly": "monthly", "monthly": "mastered"}

# A monthly slot must come at least this many days after the previous review,
# so the first monthly review isn't two days after the last weekly one.
MIN_MONTHLY_GAP_DAYS = 14

# Days after its date that a review stays open, and the most each may be set
# to (the window must close before the next review's date).
DEFAULT_GRACE_DAYS = {"daily": 0, "weekly": 3, "monthly": 7, "mastered": 14}
MAX_GRACE_DAYS = {"daily": 0, "weekly": 6, "monthly": 13, "mastered": 60}


def grace_days(stage: str, cfg: dict) -> int:
    val = cfg.get("grace_days", {}).get(stage, DEFAULT_GRACE_DAYS.get(stage, 0))
    try:
        val = int(val)
    except (TypeError, ValueError):
        val = DEFAULT_GRACE_DAYS.get(stage, 0)
    return max(0, min(val, MAX_GRACE_DAYS.get(stage, 0)))


@dataclass(frozen=True)
class Schedule:
    stage: str             # daily | weekly | monthly | mastered
    done: int              # counted reviews in this stage (mastered: checks passed)
    extra: int             # reviews in this stage lost to misses (each makes it one longer)
    due: date              # date of the current open review slot
    anchor_weekday: int    # 0 = Monday … 6 = Sunday
    anchor_day: int        # 1–31

    def required(self, cfg: dict) -> Optional[int]:
        """Good reviews needed to leave this stage (None for mastered)."""
        cad = cfg["cadence"]
        base = {"daily": cad["daily_repeats"], "weekly": cad["weekly_repeats"],
                "monthly": cad["monthly_repeats"]}.get(self.stage)
        return None if base is None else int(base)

    @property
    def position(self) -> int:
        """0-based index of the current review within its stage."""
        return self.done


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def add_months(d: date, months: int, day: Optional[int] = None) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return _clamp_day(y, m, day if day is not None else d.day)


def _next_weekday_after(prev: date, weekday: int) -> date:
    ahead = (weekday - prev.weekday()) % 7
    return prev + timedelta(days=ahead or 7)


def _next_monthly_after(prev: date, anchor_day: int) -> date:
    cand = _clamp_day(prev.year, prev.month, anchor_day)
    while cand <= prev or (cand - prev).days < MIN_MONTHLY_GAP_DAYS:
        cand = add_months(cand, 1, anchor_day)
    return cand


def _mastered_gap(cfg: dict, checks_done: int) -> int:
    months = list(cfg["mastered"]["review_months"])
    if checks_done < len(months):
        return int(months[checks_done])
    return int(cfg["mastered"]["yearly_interval"])


def next_slot(s: Schedule, prev: date, cfg: dict) -> date:
    """Date of the slot after `prev` within the same stage."""
    if s.stage == "daily":
        return prev + timedelta(days=1)
    if s.stage == "weekly":
        return _next_weekday_after(prev, s.anchor_weekday)
    if s.stage == "monthly":
        return _next_monthly_after(prev, s.anchor_day)
    if s.stage == "mastered":
        return add_months(prev, _mastered_gap(cfg, s.done), s.anchor_day)
    raise ValueError(f"unknown stage {s.stage!r}")


def first_slot(stage: str, prev: date, anchor_weekday: int, anchor_day: int,
               cfg: dict) -> date:
    """First slot of `stage`, after the last slot `prev` of the previous stage."""
    s = Schedule(stage, 0, 0, prev, anchor_weekday, anchor_day)
    return next_slot(s, prev, cfg)


def start(start_date: date) -> Schedule:
    """A verse entering Daily: first review is on the start date itself."""
    return Schedule("daily", 0, 0, start_date, start_date.weekday(), start_date.day)


def place(stage: str, on: date, anchor_weekday: int, anchor_day: int, cfg: dict,
          done: int = 0) -> Schedule:
    """Put a verse into `stage` by hand, keeping its weekday/day-of-month rhythm.

    The first review is the first date on or after `on` that fits the stage's
    rhythm: Daily today, Weekly the next anchor weekday, Monthly the next anchor
    day of month. Mastered waits its first interval (or the next interval for
    the number of checks already done). `done` sets reviews already counted.
    """
    if stage == "daily":
        due = on
    elif stage == "weekly":
        due = on + timedelta(days=(anchor_weekday - on.weekday()) % 7)
    elif stage == "monthly":
        due = _clamp_day(on.year, on.month, anchor_day)
        if due < on:
            due = add_months(due, 1, anchor_day)
    elif stage == "mastered":
        due = add_months(on, _mastered_gap(cfg, done), anchor_day)
    else:
        raise ValueError(f"unknown stage {stage!r}")
    s = Schedule(stage, 0, 0, due, anchor_weekday, anchor_day)
    req = s.required(cfg)
    done = max(0, done if req is None else min(done, req - 1))
    return replace(s, done=done)


def record(s: Schedule, counted: bool, cfg: dict) -> Schedule:
    """A review of the open slot. A good one (counted) closes it and moves on;
    a missed one leaves it open to try again within its window."""
    if not counted:
        return s
    done = s.done + 1
    required = s.required(cfg)
    if required is not None and done >= required:
        stage = NEXT_STAGE[s.stage]
        due = first_slot(stage, s.due, s.anchor_weekday, s.anchor_day, cfg)
        return Schedule(stage, 0, 0, due, s.anchor_weekday, s.anchor_day)
    nxt = replace(s, done=done)
    return replace(nxt, due=next_slot(nxt, s.due, cfg))


def lose(s: Schedule, cfg: dict) -> Schedule:
    """The open slot's window closed without a good review: count it missed and
    move to the next date. (Mastered: the next check keeps the same gap.)"""
    return replace(s, extra=s.extra + 1, due=next_slot(s, s.due, cfg))


def window_end(s: Schedule, cfg: dict) -> date:
    """Last day the open review still counts."""
    return s.due + timedelta(days=grace_days(s.stage, cfg))


def catch_up(s: Schedule, today: date, cfg: dict) -> tuple[Schedule, list[date]]:
    """Lose every slot whose window has closed. Returns the updated schedule and
    the dates of the slots that were lost."""
    missed: list[date] = []
    while today > window_end(s, cfg):
        missed.append(s.due)
        s = lose(s, cfg)
    return s, missed


def is_due(s: Schedule, today: date) -> bool:
    return s.due <= today


def is_overdue(s: Schedule, today: date) -> bool:
    """Late but still open (catch_up has already closed anything past its window)."""
    return s.due < today


def label(s: Schedule, cfg: dict) -> str:
    """Short progress label, e.g. "Day 2 of 7", "Week 1 of 4", "Monthly 3 of 24"."""
    n = s.done + 1
    if s.stage == "mastered":
        return f"Mastered check {n}"
    base = s.required(cfg) or 0
    unit = {"daily": "Day", "weekly": "Week", "monthly": "Month"}[s.stage]
    text = f"{unit} {n} of {base}"
    if s.extra:
        text += f" · {s.extra} missed"
    return text
