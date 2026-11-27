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


def test_up_next_jumps_the_backlog(app):
    svc.add_verse("Alma 32:21")
    svc.add_verse("Ether 12:27")
    third = svc.add_verse("Moroni 10:4-5").verse
    assert svc.up_next().reference == "Alma 32:21"
    svc.put_up_next(third)
    db.session.commit()
    assert svc.up_next() == third
    ether = Verse.query.filter_by(reference="Ether 12:27").one()
    svc.put_up_next(ether)                       # the latest pick goes first
    db.session.commit()
    assert svc.up_next() == ether
    assert svc.intake(DAY, svc.get_settings(), force=True) == ether
    assert svc.up_next() == third


def test_up_next_page(app):
    cfg = svc.get_settings()
    cfg["auto_add"]["every_n_days"] = 0          # keep page loads from starting verses
    svc.save_settings(cfg)
    svc.add_verse("Alma 32:21")
    v = svc.add_verse("Ether 12:27").verse
    client = app.test_client()
    assert client.post(f"/verse/{v.id}/up-next").status_code == 302
    assert svc.up_next() == v
    assert "In Backlog · up next" in client.get(f"/verse/{v.id}").get_data(as_text=True)
    rows = client.get("/stage/backlog").get_data(as_text=True)
    assert rows.index("Up next") < rows.index("Alma 32:21")
    svc.start_verse(v, DAY)
    db.session.commit()
    assert client.post(f"/verse/{v.id}/up-next").status_code == 400


def test_verse_page_steps_through_stage(app):
    cfg = svc.get_settings()
    cfg["auto_add"]["every_n_days"] = 0
    svc.save_settings(cfg)
    a = svc.add_verse("Alma 32:21").verse
    b = svc.add_verse("Ether 12:27").verse
    c = svc.add_verse("Moroni 10:4").verse
    db.session.commit()
    order = [it.verse.id for it in svc.stage_view("backlog", DAY)]
    client = app.test_client()
    mid = client.get(f"/verse/{order[1]}").get_data(as_text=True)
    assert "2 of 3" in mid
    assert f'href="/verse/{order[0]}" data-swipe-prev' in mid
    assert f'href="/verse/{order[2]}" data-swipe-next' in mid
    first = client.get(f"/verse/{order[0]}").get_data(as_text=True)
    assert "1 of 3" in first and "\" data-swipe-prev" not in first
    svc.start_verse(a, DAY)
    db.session.commit()
    alone = client.get(f"/verse/{a.id}").get_data(as_text=True)
    assert "\" data-swipe-next" not in alone and "verse-nav\"" not in alone


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


def test_review_is_two_steps_graded_as_one(app):
    v = _start(app)
    c = app.test_client()
    page = c.get("/review?start=1").get_data(as_text=True)
    assert "Which verse is this?" in page
    c.post(f"/review/{v.id}/check", data={"answer": "alma 32:21"})
    page = c.get("/review").get_data(as_text=True)
    assert "Now say the whole verse" in page and "Correct" in page and "Alma 32:21" in page
    r = c.post(f"/review/{v.id}/grade", data={"result": "got", "mode": "both"})
    assert r.status_code == 302 and v.done == 1
    assert v.reviews[0].mode == "both" and v.reviews[0].result == "got"
    done = c.get("/review").get_data(as_text=True)
    assert "All done for today" in done and "1 verse reviewed" in done


def test_wrong_reference_makes_a_good_recitation_shaky(app):
    v = _start(app)
    c = app.test_client()
    c.get("/review?start=1")
    c.post(f"/review/{v.id}/check", data={"answer": "Alma 32:22"})
    page = c.get("/review").get_data(as_text=True)
    assert "Right chapter" in page and "counts as Shaky" in page
    c.post(f"/review/{v.id}/grade", data={"result": "got", "mode": "both"})
    assert v.reviews[0].result == "shaky" and v.done == 1


def test_gave_up_shows_reference_then_recite(app):
    v = _start(app)
    c = app.test_client()
    c.get("/review?start=1")
    c.post(f"/review/{v.id}/check", data={"answer": "", "gave_up": "1"})
    page = c.get("/review").get_data(as_text=True)
    assert "Here it is" in page and "Now say the whole verse" in page
    c.post(f"/review/{v.id}/grade", data={"result": "missed", "mode": "both"})
    assert v.reviews[0].result == "missed" and v.done == 0


def test_clue_avoids_filler():
    from scripture import clues
    alma32 = ("And now as I said concerning faith--faith is not to have a perfect knowledge of things; "
              "therefore if ye have faith ye hope for things which are not seen, which are true. "
              "And now, behold, I say unto you, and I would that ye should remember, that God is "
              "merciful unto all who believe on his name; therefore he desireth, in the first place, "
              "that ye should believe, yea, even on his word.")
    others = ["And now, behold, I say unto you, my brethren, that these things are true.",
              "Behold, I say unto you that ye should remember."]
    df = clues.doc_freq(others)
    assert clues.distinctiveness("which are true. And now, behold, I say unto you", df) < 1
    for seed in range(40):
        ph = clues.phrase(alma32, seed, df)
        assert "which are true. And now, behold, I say unto you" not in ph
        assert clues.distinctiveness(ph, df) >= clues.FAIR_SCORE


def test_clue_shrinks_through_daily():
    from scripture import clues
    text = ("Therefore God gave unto them commandments, after having made known unto them the plan "
            "of redemption, that they should not do evil, the penalty thereof being a second death; "
            "for on such the plan of redemption could have no power.")
    assert [clues.clue_kind("daily", p, 1) for p in range(7)] == ["full"] * 2 + ["sentence"] * 3 + ["phrase"] * 2
    for seed in range(20):
        ph = clues.phrase(text, seed)
        assert 10 <= len(ph.split()) <= 16 and ph in text
        se = clues.sentence(text, seed)
        assert se in text and 7 <= len(se.split()) < len(text.split())
    assert {clues.phrase(text, s) for s in range(20)} != {clues.phrase(text, 0)}   # varies by day
    assert clues.support_kind("daily", 0, 1) == "full" and clues.support_kind("mastered", 3, 1) == "none"
    assert clues.combine("exact", "got") == "got" and clues.combine("wrong", "got") == "shaky"
    assert clues.display(text, "Therefore God gave unto them commandments") == "Therefore God gave unto them commandments…"
    assert clues.display("men are, that they might have joy.", "that they might have joy.") == "…that they might have joy."
    assert clues.combine("exact", "missed") == "missed" and clues.combine("overlap", "shaky") == "shaky"


def test_daily_multiple_choice_ends_after_day_five(app):
    v = _start(app)
    v.done = 4
    assert svc.choices_allowed(v)
    v.done = 5
    assert not svc.choices_allowed(v)


def test_needs_work_and_also_carry(app):
    a, b = _start(app, "Alma 32:21"), _start(app, "Ether 12:27")
    svc.record_review(b, "shaky", "both", DAY)
    svc.record_review(b, "shaky", "both", DAY + timedelta(days=1))
    db.session.commit()
    assert svc.needs_work(b) and not svc.needs_work(a)
    carry, n = svc.also_carry(a)
    assert carry == b and n == 2
    app.config["TODAY_OVERRIDE"] = (DAY + timedelta(days=2)).isoformat()
    svc.pin_memorize(a, DAY)
    db.session.commit()
    c = app.test_client()
    page = c.get("/").get_data(as_text=True)
    assert "Also carry" in page and "needs work" in page
    card = c.get(f"/memorize/{b.id}").get_data(as_text=True)
    assert "Make this my verse of the day" in card and ">Practice<" in card


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
    svc.kv_set("last_upkeep", DAY.isoformat())  # today's upkeep already ran
    db.session.commit()
    c = app.test_client()
    page = c.get("/review?start=1&answer=choice").get_data(as_text=True)
    assert "Choose the reference" in page and "Doctrine and Covenants 93:1" in page
    c.post(f"/review/{v.id}/check", data={"answer": "Doctrine and Covenants 121:34-36", "style": "choice"})
    assert "Correct" in c.get("/review").get_data(as_text=True)
    c.post(f"/review/{v.id}/check", data={"answer": "Doctrine and Covenants 93:1", "style": "choice"})
    wrong = c.get("/review").get_data(as_text=True)
    assert "Not quite" in wrong and "you chose" in wrong


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


def test_dots_in_date_order_and_only_missed_yesterday_is_urgent(app):
    a, b = _start(app, "Alma 32:21"), _start(app, "Ether 12:27")
    svc.record_review(a, "got", "ref_to_text", DAY)      # day 1: both good
    svc.record_review(b, "got", "ref_to_text", DAY)
    svc.daily_upkeep(DAY + timedelta(days=2))             # day 2: both missed
    day = DAY + timedelta(days=2)
    svc.record_review(a, "got", "ref_to_text", day)       # day 3: only Alma reviewed
    db.session.commit()
    # Next day: Alma has an old miss but was reviewed yesterday; Ether missed yesterday.
    nxt = day + timedelta(days=1)
    svc.daily_upkeep(nxt)
    view = svc.today_view(nxt)
    items = {i.verse.id: i for i in view["due_by_stage"]["daily"]}
    assert items[a.id].dots[:3] == ["d-done", "d-miss", "d-done"]
    assert not items[a.id].urgent
    assert items[b.id].dots[:3] == ["d-done", "d-miss", "d-miss"]
    assert items[b.id].urgent
    assert view["due_by_stage"]["daily"][0].verse.id == b.id
    app.config["TODAY_OVERRIDE"] = nxt.isoformat()
    page = app.test_client().get("/").get_data(as_text=True)
    assert page.count('class="row urgent"') == 1 and "d-miss" in page


def test_dots_fill_in_reviews_from_before_history(app):
    v = _start(app)
    v.done, v.extra = 2, 1      # counts carried over, no review rows
    db.session.commit()
    it = svc.today_view(DAY)["due_by_stage"]["daily"][0]
    assert it.dots[:3] == ["d-done", "d-done", "d-miss"] and not it.urgent


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


# ---------- verse of the day ----------

def test_verse_of_the_day_is_newest_daily_until_pinned(app):
    a = _start(app, "Alma 32:21")
    b = svc.add_verse("Ether 12:27").verse
    svc.start_verse(b, DAY + timedelta(days=1))
    db.session.commit()
    assert svc.memorize_verse() == b
    svc.pin_memorize(a, DAY + timedelta(days=1))
    db.session.commit()
    assert svc.memorize_verse() == a
    c = svc.add_verse("Moroni 10:4").verse        # a newer verse starts: back to newest
    svc.start_verse(c, DAY + timedelta(days=2))
    db.session.commit()
    assert svc.memorize_verse() == c


def test_first_letters_keeps_punctuation():
    assert svc.first_letters("And now, my brethren; the Lord’s 2 words.") == "A n, m b; t L 2 w."


def test_memorizing_row_on_today_practice_on_verse_page(app):
    v = _start(app)
    app.config["TODAY_OVERRIDE"] = DAY.isoformat()
    c = app.test_client()
    page = c.get("/").get_data(as_text=True)
    assert "Memorizing" in page and "Tap to practice" in page and 'data-view' not in page
    card = c.get("/memorize").get_data(as_text=True)
    assert "memorize-page" in card and "Alma 32:21" in card and 'data-view="letters"' in card
    assert '<span class="w">Text</span> <span class="w">of</span>' in card and "Next phrase" in card
    assert 'class="tabbar"' not in card
    vp = c.get(f"/verse/{v.id}").get_data(as_text=True)
    assert 'data-view="letters"' in vp and "T o A 32:21." in vp and "Show the verse" in vp
    other = _start(app, "Ether 12:27")
    assert "Make this my verse of the day" in c.get(f"/verse/{v.id}").get_data(as_text=True)
    c.post(f"/verse/{v.id}/memorize")
    assert svc.memorize_verse() == v
    assert "this is your verse of the day" in c.get(f"/verse/{v.id}").get_data(as_text=True)


def test_notecard_without_a_verse_goes_back_to_today(app):
    r = app.test_client().get("/memorize")
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
