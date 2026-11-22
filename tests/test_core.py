from datetime import date, timedelta

import pytest

from scripture import hints, refs, schedule
from scripture.settings import DEFAULTS as CFG


# ---------- refs ----------

@pytest.mark.parametrize("text,expected", [
    ("Alma 34:14-15", "Alma 34:14-15"),
    ("Alma 34:14–15", "Alma 34:14-15"),
    ("D&C 121:34-36", "Doctrine and Covenants 121:34-36"),
    ("d & c 93:1", "Doctrine and Covenants 93:1"),
    ("Doctrine and Covenants 88:67-68", "Doctrine and Covenants 88:67-68"),
    ("2 Ne 2:25-26", "2 Nephi 2:25-26"),
    ("2ne. 9:28", "2 Nephi 9:28"),
    ("Hel 14:17-18", "Helaman 14:17-18"),
    ("Psalm 34:18", "Psalms 34:18"),
    ("Moro. 10:4-5", "Moroni 10:4-5"),
    ("JS-H 1:17", "Joseph Smith—History 1:17"),
    ("Joseph Smith—History 1:17", "Joseph Smith—History 1:17"),
])
def test_parse_normalizes(text, expected):
    assert str(refs.parse(text)) == expected


def test_parse_rejects_non_refs():
    assert refs.parse("faith") is None
    assert refs.parse("34:14") is None


def test_keys_match_agent_state():
    assert refs.key_for("Doctrine and Covenants 121:34-36") == "doctrine and covenants 121:34-36"
    assert refs.key_for("D&C 121:34–36") == "doctrine and covenants 121:34-36"


def test_overlap():
    assert refs.overlaps(refs.parse("2 Nephi 2:25"), refs.parse("2 Nephi 2:25-27"))
    assert not refs.overlaps(refs.parse("2 Nephi 2:25"), refs.parse("2 Nephi 2:26-27"))
    assert not refs.overlaps(refs.parse("2 Nephi 2:25"), refs.parse("1 Nephi 2:25"))


def test_compare():
    exp = refs.parse("Alma 34:14-15")
    assert refs.compare("alma 34:14–15", exp) == "exact"
    assert refs.compare("Alma 34:14", exp) == "overlap"
    assert refs.compare("Alma 34:20", exp) == "chapter"
    assert refs.compare("Mosiah 3:19", exp) == "wrong"
    assert refs.compare("no idea", exp) == "wrong"


# ---------- hints ----------

TEXT = "Adam fell that men might be; and men are, that they might have joy."


def test_hints_full_visible_hides_nothing():
    toks = hints.tokenize(TEXT, 1.0, seed=1)
    assert "".join(t.text for t in toks) == TEXT
    assert not any(t.hidden for t in toks)


def test_hints_stable_for_seed_and_roundtrip():
    a = hints.tokenize(TEXT, 0.5, seed=42)
    b = hints.tokenize(TEXT, 0.5, seed=42)
    assert a == b
    assert "".join(t.text for t in a) == TEXT
    eligible = [t for t in a if t.is_word and len(t.text) >= 3]
    assert sum(t.hidden for t in eligible) == round(0.5 * len(eligible))


def test_underscores_preserve_punctuation():
    toks = hints.tokenize(TEXT, 0.0, seed=1)
    s = hints.to_underscores(toks)
    assert ";" in s and s.endswith(".")
    assert "Adam" not in s


def test_hints_keep_shrinking_across_stages():
    assert hints.visible_ratio("daily", 0, CFG) == pytest.approx(1.0)
    assert hints.visible_ratio("weekly", 3, CFG) == pytest.approx(0.2)
    assert hints.visible_ratio("monthly", 0, CFG) == pytest.approx(0.35)
    assert hints.visible_ratio("monthly", 23, CFG) == pytest.approx(0.0)
    assert hints.visible_ratio("mastered", 0, CFG) == 0.0


def test_interpolation_matches_agent_math():
    sched = [1.0, 0.75, 0.5, 0.35, 0.2]
    assert hints.interpolate(sched, 0, 24) == pytest.approx(1.0)
    assert hints.interpolate(sched, 23, 24) == pytest.approx(0.2)


# ---------- schedule ----------

S = date(2026, 11, 21)  # a Saturday


def _walk(s, n):
    """Count n reviews on their due dates; return the due dates visited."""
    dates = []
    for _ in range(n):
        dates.append(s.due)
        s = schedule.record(s, counted=True, cfg=CFG)
    return s, dates


def test_fixed_date_progression():
    s = schedule.start(S)
    s, daily = _walk(s, 7)
    assert daily == [date(2026, 11, d) for d in range(21, 28)]
    assert s.stage == "weekly" and s.due == date(2026, 11, 28)

    s, weekly = _walk(s, 4)
    assert weekly == [date(2026, 11, 28), date(2026, 12, 5), date(2026, 12, 12), date(2026, 12, 19)]
    assert all(d.weekday() == 5 for d in weekly)
    # Dec 21 is only 2 days after the last weekly review, so monthly starts Jan 21.
    assert s.stage == "monthly" and s.due == date(2027, 1, 21)

    s, monthly = _walk(s, 24)
    assert all(d.day == 21 for d in monthly)
    assert monthly[-1] == date(2028, 12, 21)
    assert s.stage == "mastered" and s.due == date(2029, 3, 21)

    s, mastered = _walk(s, 4)
    assert mastered == [date(2029, 3, 21), date(2029, 9, 21), date(2030, 9, 21), date(2031, 9, 21)]


def test_late_review_keeps_dates():
    s = schedule.Schedule("weekly", 1, 0, date(2026, 12, 5), 5, 21)
    # Reviewed on Tuesday Dec 8 — the next one is still Saturday Dec 12.
    s, missed = schedule.catch_up(s, date(2026, 12, 8), CFG)
    assert missed == [] and s.due == date(2026, 12, 5)
    s = schedule.record(s, counted=True, cfg=CFG)
    assert s.due == date(2026, 12, 12) and s.done == 2


@pytest.mark.parametrize("stage,due,last_ok", [
    ("weekly", date(2026, 12, 5), date(2026, 12, 8)),     # 3 days
    ("monthly", date(2026, 12, 21), date(2026, 12, 28)),  # 7 days
    ("mastered", date(2027, 3, 21), date(2027, 4, 4)),    # 14 days
])
def test_grace_window(stage, due, last_ok):
    s = schedule.Schedule(stage, 1, 0, due, 5, 21)
    assert schedule.window_end(s, CFG) == last_ok
    kept, missed = schedule.catch_up(s, last_ok, CFG)
    assert missed == [] and kept == s
    lost, missed = schedule.catch_up(s, last_ok + timedelta(days=1), CFG)
    assert missed == [due] and lost.extra == 1 and lost.done == 1 and lost.due > due


def test_lost_review_makes_stage_one_longer_not_two():
    s = schedule.Schedule("weekly", 1, 0, date(2026, 12, 5), 5, 21)
    s, missed = schedule.catch_up(s, date(2026, 12, 9), CFG)
    assert missed == [date(2026, 12, 5)]
    assert s.due == date(2026, 12, 12) and s.done == 1 and s.extra == 1
    assert s.required(CFG) == 4   # still 4 good reviews, just finishing a week later


def test_missed_answer_leaves_review_open_and_never_demotes():
    s = schedule.Schedule("monthly", 5, 0, date(2027, 6, 21), 5, 21)
    assert schedule.record(s, counted=False, cfg=CFG) == s


def test_mastered_lost_check_keeps_same_gap():
    s = schedule.Schedule("mastered", 1, 0, date(2027, 3, 21), 5, 21)  # next gap would be 6 months
    s, _ = schedule.catch_up(s, date(2027, 4, 5), CFG)
    assert s.done == 1 and s.due == date(2027, 9, 21)


def test_day_31_clamps_to_month_end():
    s = schedule.Schedule("monthly", 0, 0, date(2027, 1, 31), 6, 31)
    s, dates = _walk(s, 3)
    assert dates == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]


def test_daily_catch_up_after_days_away():
    s = schedule.start(S)
    s, missed = schedule.catch_up(s, date(2026, 11, 24), CFG)
    assert missed == [date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23)]
    assert s.due == date(2026, 11, 24) and s.extra == 3 and s.required(CFG) == 7


def test_labels():
    assert schedule.label(schedule.start(S), CFG) == "Day 1 of 7"
    assert schedule.label(schedule.Schedule("monthly", 2, 0, S, 5, 21), CFG) == "Month 3 of 24"
    assert schedule.label(schedule.Schedule("daily", 1, 5, S, 5, 21), CFG) == "Day 2 of 7 · 5 missed"


# ---------- lenient typing ----------

@pytest.mark.parametrize("text,expected", [
    ("Alma 34 14 15", "Alma 34:14-15"),
    ("alma 34 14-15", "Alma 34:14-15"),
    ("D&C 121 34 36", "Doctrine and Covenants 121:34-36"),
    ("1 Nephi 3 7", "1 Nephi 3:7"),
    ("Hel. 14.17", "Helaman 14:17"),
])
def test_spaces_instead_of_punctuation(text, expected):
    assert str(refs.parse(text)) == expected


def test_run_together_verses_match_either_reading():
    assert refs.grade("alma 34 56", refs.parse("Alma 34:5-6"))[0] == "exact"
    assert refs.grade("alma 34 56", refs.parse("Alma 34:56"))[0] == "exact"
    assert refs.grade("Alma 34 1415", refs.parse("Alma 34:14-15"))[0] == "exact"
    g, reading = refs.grade("alma 34 56", refs.parse("Alma 34:5-7"))
    assert g == "overlap" and str(reading) == "Alma 34:5-6"


# ---------- multiple choice ----------

from scripture import choices  # noqa: E402


@pytest.mark.parametrize("answer", [
    "Doctrine and Covenants 121:34-36", "Alma 34:14-15", "Enos 1:27", "Moroni 10:4-5", "Psalms 23:1",
])
def test_choices_same_book_distinct_and_stable(answer):
    a = refs.parse(answer)
    program = [refs.parse(x) for x in ["Doctrine and Covenants 93:1", "Alma 41:10-11", "Mosiah 3:19-20"]]
    opts = choices.options(a, program, seed=3)
    assert len(opts) == 4 and a in opts
    assert all(o.book == a.book for o in opts)
    others = [o for o in opts if o != a]
    assert not any(refs.overlaps(o, a) for o in others)
    assert len({str(o) for o in opts}) == 4
    assert choices.options(a, program, seed=3) == opts
    for o in others:
        assert 1 <= o.chapter <= choices.CHAPTERS[a.book]


# ---------- moving by hand ----------

def test_place_keeps_rhythm():
    sat = date(2026, 11, 21)  # Saturday the 21st
    wed = date(2026, 11, 25)
    assert schedule.place("daily", wed, 5, 21, CFG).due == wed
    assert schedule.place("weekly", wed, 5, 21, CFG).due == date(2026, 11, 28)
    assert schedule.place("weekly", sat, 5, 21, CFG).due == sat
    assert schedule.place("monthly", wed, 5, 21, CFG).due == date(2026, 12, 21)
    assert schedule.place("monthly", date(2026, 11, 2), 5, 21, CFG).due == date(2026, 11, 21)
    assert schedule.place("mastered", wed, 5, 21, CFG).due == date(2027, 2, 21)


def test_place_sets_done_within_stage():
    s = schedule.place("monthly", date(2026, 11, 25), 5, 21, CFG, done=5)
    assert s.done == 5 and schedule.label(s, CFG) == "Month 6 of 24"
    assert schedule.place("weekly", date(2026, 11, 25), 5, 21, CFG, done=99).done == 3


def test_dangling_separator_from_formatting():
    assert str(refs.parse("Alma 34:14–")) == "Alma 34:14"
    assert str(refs.parse("Alma 34:14-15")) == "Alma 34:14-15"
