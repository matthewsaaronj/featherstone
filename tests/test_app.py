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

    day2 = DAY + timedelta(days=1)
    svc.record_review(v, "missed", "text_to_ref", day2)
    # Still open; hidden for the rest of today. Daily's window is same day only,
    # so tomorrow it's counted missed and tomorrow's review is up.
    assert (v.done, v.extra, v.due_date, v.retry_date) == (1, 0, day2, day2 + timedelta(days=1))
    assert v not in svc.review_queue(day2)
    svc.daily_upkeep(day2 + timedelta(days=1))
    assert (v.done, v.extra, v.due_date, v.retry_date) == (1, 1, day2 + timedelta(days=1), None)
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
    assert v.extra == 3 and v.due_date == later and v.sched().required(svc.get_settings()) == 7


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


# ---------- late reviews (grace window) ----------

def _weekly(app, due):
    v = _start(app)
    v.apply(svc.schedule.Schedule("weekly", 1, 0, due, due.weekday(), 21))
    db.session.commit()
    return v


def test_weekly_late_review_counts_within_three_days(app):
    sat = date(2026, 11, 28)
    v = _weekly(app, sat)
    tue = sat + timedelta(days=3)
    svc.daily_upkeep(tue)
    assert v in svc.review_queue(tue) and svc.today_view(tue)["n_overdue"] == 1
    svc.record_review(v, "got", "text_to_ref", tue)
    assert (v.done, v.extra, v.due_date) == (2, 0, date(2026, 12, 5))  # dates stay put


def test_weekly_miss_retries_next_day_then_counts(app):
    sat = date(2026, 11, 28)
    v = _weekly(app, sat)
    svc.record_review(v, "missed", "text_to_ref", sat)
    assert v not in svc.review_queue(sat)
    svc.record_review(v, "got", "text_to_ref", sat)        # same day again: practice only
    assert v.done == 1
    sun = sat + timedelta(days=1)
    assert v in svc.review_queue(sun)
    svc.record_review(v, "got", "text_to_ref", sun)
    assert (v.done, v.extra, v.due_date) == (2, 0, date(2026, 12, 5))


def test_weekly_review_lost_after_window(app):
    sat = date(2026, 11, 28)
    v = _weekly(app, sat)
    svc.record_review(v, "missed", "text_to_ref", sat)
    svc.daily_upkeep(sat + timedelta(days=4))
    assert (v.done, v.extra, v.due_date, v.retry_date) == (1, 1, date(2026, 12, 5), None)
    assert svc.is_practice(v, sat + timedelta(days=4))


def test_grace_days_setting(app):
    c = app.test_client()
    page = c.get("/settings").get_data(as_text=True)
    assert 'name="grace_weekly"' in page and 'name="grace_monthly"' in page
    form = {"grace_weekly": "5", "grace_monthly": "99", "grace_mastered": "10"}
    c.post("/settings", data=form)
    g = svc.get_settings()["grace_days"]
    assert (g["weekly"], g["monthly"], g["mastered"]) == (5, 13, 10)


# ---------- AI suggestions ----------

def _fake_ai(monkeypatch, answers):
    """AI stub that returns `answers` in order and records what it was told to avoid."""
    calls = []
    it = iter(answers)
    def suggest(topic, exclusions, api_key=None):
        calls.append(list(exclusions))
        return next(it, None)
    monkeypatch.setattr(svc.ai, "suggest", suggest)
    monkeypatch.setattr(svc.ai, "available", lambda *a, **k: True)
    return calls


def _post(c, **data):
    return c.post("/add", data=data, follow_redirects=True).get_data(as_text=True)


def test_skip_never_repeats(app, monkeypatch):
    # The AI keeps answering Alma 32:21 first; the second time it must be passed over.
    monkeypatch.setattr(svc.texts, "fetch", lambda ref: (f"Text of {ref}.", "nephi.org"))
    calls = _fake_ai(monkeypatch, ["Alma 32:21", "Alma 32:21", "Ether 12:27"])
    c = app.test_client()
    first = _post(c, action="suggest", topic="faith")
    assert "Alma 32:21" in first and "Text of Alma 32:21." in first
    second = _post(c, action="skip")
    assert "Ether 12:27" in second
    assert "Alma 32:21" in calls[1]            # told to avoid the earlier suggestion
    assert ">faith</textarea>" in second       # theme kept


def test_suggestion_skips_verses_already_in_program(app, monkeypatch):
    svc.add_verse("Alma 32:21")
    _fake_ai(monkeypatch, ["Alma 32:21-22", "Moroni 10:4"])
    page = _post(app.test_client(), action="suggest", topic="faith")
    assert "Moroni 10:4" in page and "overlaps" not in page


def test_suggestion_gives_up_gracefully(app, monkeypatch):
    _fake_ai(monkeypatch, ["Alma 32:21", "Alma 32:21", "Alma 32:21", "Alma 32:21", "Alma 32:21"])
    c = app.test_client()
    _post(c, action="suggest", topic="faith")
    assert "find a new verse" in _post(c, action="skip")


def test_add_keeps_going_with_next_suggestion(app, monkeypatch):
    _fake_ai(monkeypatch, ["Alma 32:21", "Ether 12:27", "Moroni 10:4"])
    c = app.test_client()
    _post(c, action="suggest", topic="faith")
    page = _post(c, action="add", reference="Alma 32:21", origin="ai")
    assert "Ether 12:27" in page and "Added just now" in page   # next one is up
    page = _post(c, action="today", reference="Ether 12:27", origin="ai")
    assert "Moroni 10:4" in page and "Daily, starting today" in page
    a = Verse.query.filter_by(reference="Alma 32:21").one()
    e = Verse.query.filter_by(reference="Ether 12:27").one()
    assert (a.stage, a.origin, e.stage, e.due_date) == ("backlog", "ai", "daily", DAY)


def test_typed_reference_stays_on_add_page(app):
    c = app.test_client()
    page = _post(c, action="add", reference="moro 10 4")
    assert "Moroni 10:4" in page and "Added just now" in page
    page = _post(c, action="add", reference="Moroni 10:4-5")
    assert "overlaps" in page and 'value="Moroni 10:4-5"' in page   # error keeps the text
    vid = Verse.query.one().id
    c.post(f"/verse/{vid}/delete", data={"next": "add"})
    assert Verse.query.count() == 0


# ---------- Daily limit, quiz difficulty, struggling, delete ----------

def test_intake_waits_while_daily_is_full(app):
    cfg = svc.get_settings()
    cfg["auto_add"].update(every_n_days=7, daily_limit=2)
    for ref in ("Alma 32:21", "Ether 12:27"):
        svc.start_verse(svc.add_verse(ref).verse, DAY)
    svc.add_verse("Moroni 10:4")
    db.session.commit()
    assert svc.intake(DAY, cfg) is None              # Daily full
    assert svc.intake(DAY, cfg, force=True) is not None  # by hand still works
    cfg["auto_add"]["daily_limit"] = 0               # no limit
    svc.add_verse("Mosiah 2:17")
    assert svc.intake(DAY + timedelta(days=7), cfg) is not None


def test_multiple_choice_gets_rarer_and_never_in_mastered(app):
    cfg = svc.get_settings()
    cfg["quiz"]["answer_style"] = "mix"
    v = _start(app)
    days = [DAY + timedelta(days=i) for i in range(400)]
    share = {}
    for st in ("daily", "weekly", "monthly", "mastered"):
        v.stage = st
        share[st] = sum(svc.answer_style(v, d, cfg) == "choice" for d in days) / len(days)
    assert 0.4 < share["daily"] < 0.6
    assert 0.15 < share["weekly"] < 0.35
    assert 0.03 < share["monthly"] < 0.18
    assert share["mastered"] == 0
    cfg["quiz"]["answer_style"] = "choice"
    assert svc.answer_style(v, DAY, cfg) == "type"   # even when set to always choose


def test_mastered_quiz_has_no_choices_link(app):
    v = _start(app)
    svc.move_verse(v, "mastered", DAY)
    v.due_date = DAY
    db.session.commit()
    c = app.test_client()
    for _ in range(4):  # whichever direction it picks, the choice view never appears
        page = c.get("/review?start=1&answer=choice").get_data(as_text=True)
        assert "Show choices instead" not in page and 'name="style" value="choice"' not in page


def _miss(v, n, result="missed"):
    from datetime import datetime
    for _ in range(n):
        db.session.add(Review(verse_id=v.id, stage=v.stage, mode="text_to_ref", result=result,
                              reviewed_at=datetime.now()))
    db.session.commit()
    db.session.expire(v, ["reviews"])


def test_struggling_after_three_misses_until_moved(app):
    v = _start(app)
    svc.move_verse(v, "weekly", DAY)
    _miss(v, 2)
    assert not svc.struggling(v)
    _miss(v, 1)
    assert svc.struggling(v)
    page = app.test_client().get(f"/verse/{v.id}").get_data(as_text=True)
    assert "Move back to Daily" in page
    svc.move_verse(v, "daily", DAY)
    assert not svc.struggling(v)


def test_delete_verse_from_any_stage(app):
    v = _start(app)
    _miss(v, 2)
    vid = v.id
    r = app.test_client().post(f"/verse/{vid}/delete")
    assert r.status_code == 302
    assert db.session.get(Verse, vid) is None
    assert Review.query.filter_by(verse_id=vid).count() == 0


def test_dots_show_misses_and_urgent_verses_come_first(app):
    a, b = _start(app, "Alma 32:21"), _start(app, "Ether 12:27")
    svc.daily_upkeep(DAY + timedelta(days=2))   # both lose two days
    day = DAY + timedelta(days=2)
    b.extra, a.extra = 2, 0                    # only Ether has misses
    db.session.commit()
    view = svc.today_view(day)
    first = view["due_by_stage"]["daily"][0]
    assert first.verse.id == b.id and first.urgent
    assert first.dots == ["d-miss"] * 2 + ["d-todo"] * 7
    assert not view["due_by_stage"]["daily"][1].urgent
    app.config["TODAY_OVERRIDE"] = day.isoformat()
    page = app.test_client().get("/").get_data(as_text=True)
    assert 'class="row urgent"' in page and "d-miss" in page


def test_late_weekly_is_urgent_only_on_last_day(app):
    sat = date(2026, 11, 28)
    v = _weekly(app, sat)
    assert not svc.today_view(sat + timedelta(days=2))["overdue"][0].urgent
    assert svc.today_view(sat + timedelta(days=3))["overdue"][0].urgent


def test_blank_search_uses_default_theme_and_descriptions_reach_ai(app, monkeypatch):
    told = []
    monkeypatch.setattr(svc.ai, "available", lambda *a, **k: True)
    monkeypatch.setattr(svc.ai, "suggest", lambda topic, ex, api_key=None: told.append(topic) or "Moroni 10:4")
    c = app.test_client()
    _post(c, action="suggest", topic="  ")
    assert told[-1] == svc.get_settings()["auto_add"]["topic_default"]
    desc = "A verse about how missionary work can cover a multitude of sins"
    _post(c, action="suggest", topic=desc)
    assert told[-1] == desc


def test_ai_prompt_treats_topic_as_description(monkeypatch):
    from scripture import ai
    seen = {}
    monkeypatch.setattr(ai, "_chat", lambda prompt, key=None: seen.setdefault("p", prompt) and "Alma 5:14")
    ai.suggest("missionary work covering sins", [], api_key="x")
    assert "description" in seen["p"] and "missionary work covering sins" in seen["p"]


def test_pages_have_balanced_divs(app):
    from html.parser import HTMLParser
    class P(HTMLParser):
        depth = 0
        def handle_starttag(self, tag, a):
            if tag == "div": self.depth += 1
        def handle_endtag(self, tag):
            if tag == "div":
                self.depth -= 1
                assert self.depth >= 0, "stray </div>"
    v = svc.add_verse("Alma 32:21").verse
    c = app.test_client()
    for path in ["/", "/add", "/settings", "/stage/daily", f"/verse/{v.id}"]:
        p = P(); p.feed(c.get(path).get_data(as_text=True))
        assert p.depth == 0, path
