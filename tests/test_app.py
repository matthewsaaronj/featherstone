from datetime import date, timedelta

import pytest

from webapp import create_app
from webapp import services as svc
from webapp.models import Review, Verse, db

DAY = date(2026, 11, 21)


@pytest.fixture()
def app(monkeypatch, tmp_path):
    # Never hit the network in tests.
    monkeypatch.setattr(svc.texts, "fetch", lambda ref: (f"Text of {ref}.", "nephi.org"))
    monkeypatch.setattr(svc.ai, "available", lambda *a, **k: False)
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 't.db'}",
                      "TODAY_OVERRIDE": DAY.isoformat(), "TESTING": True,
                      "DATA_DIR": str(tmp_path)})
    with app.app_context():
        yield app


def test_add_normalizes_and_fetches(app):
    res = svc.add_verse("moro 10:4-5")
    assert res.error is None
    v = res.verse
    assert v.reference == "Moroni 10:4-5" and v.stage == "backlog"
    assert v.text == "Text of Moroni 10:4-5." and v.text_sha


def test_add_blocks_overlap(app):
    svc.add_verse("2 Nephi 2:25-26")
    res = svc.add_verse("2 Ne 2:26-27")
    assert res.verse is None and "overlaps" in res.error


def test_add_rejects_garbage(app):
    assert svc.add_verse("faith").error


def test_intake_respects_cadence(app):
    svc.add_verse("Alma 32:21")
    svc.add_verse("Ether 12:27")
    cfg = svc.get_settings()
    cfg["auto_add"]["every_n_days"] = 7
    first = svc.intake(DAY, cfg)
    assert first.reference == "Alma 32:21" and first.stage == "daily" and first.due_date == DAY
    assert svc.intake(DAY + timedelta(days=3), cfg) is None
    assert svc.intake(DAY + timedelta(days=7), cfg).reference == "Ether 12:27"


def test_intake_off_when_zero(app):
    svc.add_verse("Alma 32:21")
    cfg = svc.get_settings()
    cfg["auto_add"]["every_n_days"] = 0
    assert svc.intake(DAY, cfg) is None
    assert svc.intake(DAY, cfg, force=True).stage == "daily"


def test_review_counts_and_misses(app):
    v = svc.add_verse("Alma 32:21").verse
    svc.start_verse(v, DAY)
    db.session.commit()

    svc.record_review(v, "got", "ref_to_text", DAY)
    assert (v.done, v.extra, v.due_date, v.retry_date) == (1, 0, DAY + timedelta(days=1), None)

    svc.record_review(v, "missed", "text_to_ref", DAY + timedelta(days=1))
    assert (v.done, v.extra) == (1, 1)
    assert v.due_date == DAY + timedelta(days=2)
    assert v.retry_date == DAY + timedelta(days=2)
    assert Review.query.filter_by(verse_id=v.id, counted=True).count() == 1


def test_practice_retry_never_counts(app):
    v = svc.add_verse("Alma 32:21").verse
    svc.start_verse(v, DAY)
    v.apply(svc.schedule.Schedule("weekly", 1, 0, DAY + timedelta(days=5), DAY.weekday(), DAY.day))
    v.retry_date = DAY
    db.session.commit()
    svc.record_review(v, "got", "ref_to_text", DAY)
    assert v.done == 1 and v.retry_date is None
    assert Review.query.filter_by(verse_id=v.id).one().practice is True


def test_upkeep_runs_once_per_day(app):
    v = svc.add_verse("Alma 32:21").verse
    svc.start_verse(v, DAY)
    db.session.commit()
    later = DAY + timedelta(days=3)
    svc.daily_upkeep(later)
    svc.daily_upkeep(later)
    assert Review.query.filter_by(result="skipped").count() == 3
    assert v.extra == 3 and v.due_date == later


def test_pages_render(app):
    v = svc.add_verse("Alma 32:21").verse
    client = app.test_client()
    for path in ["/", "/stage/daily", "/stage/backlog", "/add", "/settings", f"/verse/{v.id}"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/stage/nope").status_code == 404


# ---------- quiz ----------

def _start(app, ref="Alma 32:21"):
    v = svc.add_verse(ref).verse
    svc.start_verse(v, DAY)
    db.session.commit()
    return v


def test_first_review_is_ref_to_text_then_alternates(app):
    v = _start(app)
    assert svc.choose_mode(v, DAY) == "ref_to_text"
    svc.record_review(v, "got", "ref_to_text", DAY)
    assert svc.choose_mode(v, DAY + timedelta(days=1)) == "text_to_ref"


def test_quiz_flow_ref_to_text(app):
    v = _start(app)
    c = app.test_client()
    page = c.get("/review?start=1").get_data(as_text=True)
    assert "Recite the verse" in page and "Alma 32:21" in page
    r = c.post(f"/review/{v.id}/grade", data={"result": "got", "mode": "ref_to_text"})
    assert r.status_code == 302
    assert v.done == 1
    done = c.get("/review").get_data(as_text=True)
    assert "All done for today" in done and "1 verse reviewed" in done


def test_quiz_check_grades_typed_reference(app):
    v = _start(app)
    svc.record_review(v, "got", "ref_to_text", DAY)  # next mode: text_to_ref
    c = app.test_client()
    ans = c.post(f"/review/{v.id}/check", data={"answer": "alma 32:21"}).get_data(as_text=True)
    assert "Correct" in ans
    ans = c.post(f"/review/{v.id}/check", data={"answer": "Alma 32:22"}).get_data(as_text=True)
    assert "Right chapter" in ans
    ans = c.post(f"/review/{v.id}/check", data={"answer": "", "gave_up": "1"}).get_data(as_text=True)
    assert "Here it is" in ans


def test_skip_moves_verse_to_end(app):
    a = _start(app, "Alma 32:21")
    b = _start(app, "Ether 12:27")
    c = app.test_client()
    first = c.get("/review?start=1").get_data(as_text=True)
    first_id, second_id = (a.id, b.id) if "Alma 32:21" in first else (b.id, a.id)
    c.post(f"/review/{first_id}/skip")
    nxt = c.get("/review").get_data(as_text=True)
    assert db.session.get(Verse, second_id).display_ref in nxt


def test_grade_rejects_bad_input(app):
    v = _start(app)
    c = app.test_client()
    assert c.post(f"/review/{v.id}/grade", data={"result": "maybe", "mode": "ref_to_text"}).status_code == 400


def test_multiple_choice_flow(app):
    v = _start(app, "Doctrine and Covenants 121:34-36")
    svc.add_verse("Doctrine and Covenants 93:1")
    # A past reference → text review makes today's question text → reference.
    db.session.add(Review(verse_id=v.id, stage="daily", mode="ref_to_text", result="got", counted=True))
    svc.kv_set("last_upkeep", DAY.isoformat())  # today's upkeep already ran
    db.session.commit()
    c = app.test_client()
    page = c.get("/review?start=1&answer=choice").get_data(as_text=True)
    assert "Choose the reference" in page and "Doctrine and Covenants 93:1" in page
    right = c.post(f"/review/{v.id}/check", data={"answer": "Doctrine and Covenants 121:34-36",
                                                   "style": "choice"}).get_data(as_text=True)
    assert "Correct" in right
    wrong = c.post(f"/review/{v.id}/check", data={"answer": "Doctrine and Covenants 93:1",
                                                   "style": "choice"}).get_data(as_text=True)
    assert "Not quite" in wrong and "You chose" in wrong


def test_answer_style_setting(app):
    v = _start(app)
    cfg = svc.get_settings()
    cfg["quiz"]["answer_style"] = "type"
    assert svc.answer_style(v, DAY, cfg) == "type"
    cfg["quiz"]["answer_style"] = "choice"
    assert svc.answer_style(v, DAY, cfg) == "choice"


# ---------- moving and practice ----------

def test_move_forward_and_back_to_backlog(app):
    v = _start(app)  # Daily, started Saturday Nov 21
    svc.add_verse("Ether 12:27")
    svc.move_verse(v, "monthly", DAY + timedelta(days=4), done=3)
    assert (v.stage, v.done, v.extra) == ("monthly", 3, 0)
    assert v.due_date == date(2026, 12, 21)  # keeps its 21st
    svc.move_verse(v, "backlog", DAY)
    assert v.stage == "backlog" and v.due_date is None
    first = svc.stage_view("backlog", DAY)[0].verse
    assert first.id == v.id  # front of the line
    moved = Review.query.filter_by(verse_id=v.id, result="moved").all()
    assert [r.stage for r in moved] == ["monthly", "backlog"]
    page = app.test_client().get(f"/verse/{v.id}").get_data(as_text=True)
    assert "Moved by hand from Monthly" in page


def test_practice_one_verse_never_counts(app):
    v = _start(app)
    svc.record_review(v, "got", "ref_to_text", DAY)  # due tomorrow now
    c = app.test_client()
    c.post("/practice", data={"verse": v.id})
    page = c.get("/review").get_data(as_text=True)
    assert "Practice · doesn" in page
    mode = "text_to_ref"  # second review of this verse
    c.post(f"/review/{v.id}/grade", data={"result": "got", "mode": mode})
    assert v.done == 1 and v.due_date == DAY + timedelta(days=1)
    assert "Practice finished" in c.get("/review").get_data(as_text=True)


def test_practice_stage_runs_each_verse_once(app):
    a = _start(app, "Alma 32:21")
    b = _start(app, "Ether 12:27")
    svc.kv_set("last_upkeep", DAY.isoformat())
    db.session.commit()
    c = app.test_client()
    c.post("/practice", data={"stage": "daily"})
    seen = []
    for _ in range(3):
        page = c.get("/review").get_data(as_text=True)
        if "Practice finished" in page:
            break
        v = a if "Alma 32:21" in page else b
        seen.append(v.id)
        c.post(f"/review/{v.id}/grade", data={"result": "got", "mode": "ref_to_text"})
    assert sorted(seen) == sorted([a.id, b.id])
    # Both were due today, so these counted.
    assert a.done == 1 and b.done == 1


# ---------- make-ups through practice ----------

def _daily_with_extra(app):
    v = _start(app)
    svc.daily_upkeep(DAY + timedelta(days=1))   # Nov 21 slot skipped → 1 extra
    assert v.extra == 1 and v.due_date == DAY + timedelta(days=1)
    svc.record_review(v, "got", "ref_to_text", DAY + timedelta(days=1))  # today's review
    return v


def test_daily_practice_got_it_makes_up_once_per_day(app):
    v = _daily_with_extra(app)
    day = DAY + timedelta(days=1)
    assert svc.makeup_status(v, day).ready
    assert svc.record_review(v, "got", "text_to_ref", day) is True
    assert v.extra == 0 and v.done == 1
    assert not svc.makeup_status(v, day).possible
    assert Review.query.filter_by(verse_id=v.id, made_up=True).count() == 1


def test_shaky_practice_does_not_make_up(app):
    v = _daily_with_extra(app)
    assert svc.record_review(v, "shaky", "text_to_ref", DAY + timedelta(days=1)) is False
    assert v.extra == 1


def test_weekly_make_up_waits_three_days(app):
    v = _start(app)
    sat = date(2026, 11, 28)
    v.apply(svc.schedule.Schedule("weekly", 1, 0, sat, 5, 21))
    db.session.commit()
    svc.record_review(v, "missed", "text_to_ref", sat)   # failed Saturday → 1 extra
    assert v.extra == 1
    st = svc.makeup_status(v, sat + timedelta(days=1))
    assert st.possible and not st.ready and st.opens == sat + timedelta(days=3)
    assert svc.record_review(v, "got", "text_to_ref", sat + timedelta(days=1)) is False
    assert svc.record_review(v, "got", "text_to_ref", sat + timedelta(days=3)) is True
    assert v.extra == 0


def test_monthly_practice_never_makes_up(app):
    v = _start(app)
    v.apply(svc.schedule.Schedule("monthly", 2, 1, date(2026, 12, 21), 5, 21))
    db.session.commit()
    assert not svc.makeup_status(v, DAY).possible
    assert svc.record_review(v, "got", "text_to_ref", DAY) is False
    assert v.extra == 1
