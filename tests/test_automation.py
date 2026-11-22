import os
from datetime import date, datetime, timedelta

import pytest

from webapp import automation, create_app
from webapp import services as svc
from webapp.models import PushSubscription, Verse, db

DAY = date(2026, 11, 21)


@pytest.fixture()
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(svc.texts, "fetch", lambda ref: (f"Text of {ref}.", "nephi.org"))
    monkeypatch.setattr(svc.ai, "available", lambda *a, **k: False)
    key = tmp_path / "vapid.pem"
    key.write_text("not a real key")
    app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'scripture.db'}",
                      "TODAY_OVERRIDE": DAY.isoformat(), "TESTING": True,
                      "DATA_DIR": str(tmp_path),
                      "VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY_FILE": str(key)})
    with app.app_context():
        yield app


def _active(ref="Alma 32:21", text=None):
    v = svc.add_verse(ref, text=text).verse
    svc.start_verse(v, DAY)
    db.session.commit()
    return v


# ---------- text check ----------

def test_text_check_fills_verifies_and_flags(app):
    a = svc.add_verse("Alma 32:21").verse            # fetched: "Text of Alma 32:21."
    b = svc.add_verse("Ether 12:27").verse
    c = svc.add_verse("Moroni 10:4").verse
    mine = svc.add_verse("Mosiah 2:17", text="My own wording.").verse
    a.text_source = "import"
    b.text, b.text_sha = None, None
    c.text = "Old  wording -- here."
    db.session.commit()

    def fetch(ref):
        return ({"Moroni 10:4": "New wording — here."}.get(ref, f"Text of {ref}."), "nephi.org")

    out = automation.check_texts(fetch=fetch, pause=0)
    assert a.text_source == "nephi.org" and "Alma 32:21" in out.verified      # import now verified
    assert b.text == "Text of Ether 12:27." and "Ether 12:27" in out.filled
    assert c.pending_text == "New wording — here." and c.text == "Old  wording -- here."
    assert mine.text == "My own wording." and out.checked == 3               # manual text skipped


def test_trivial_differences_are_not_flagged(app):
    v = svc.add_verse("Alma 32:21").verse
    v.text = "Text  of   Alma 32:21."
    db.session.commit()
    automation.check_texts(fetch=lambda r: ("Text of Alma 32:21.", "nephi.org"), pause=0)
    assert v.pending_text is None


def test_accept_and_dismiss_pending_text(app):
    v = svc.add_verse("Alma 32:21").verse
    v.pending_text = "Newer text."
    db.session.commit()
    c = app.test_client()
    assert "text has changed" in c.get(f"/verse/{v.id}").get_data(as_text=True)
    c.post(f"/verse/{v.id}/pending", data={"action": "accept"})
    assert v.text == "Newer text." and v.pending_text is None


# ---------- backups ----------

def test_backup_keeps_newest(app):
    for i in range(4):
        automation.backup_db(DAY + timedelta(days=i), keep=2)
    folder = os.path.join(os.path.dirname(db.engine.url.database), "backups")
    assert sorted(os.listdir(folder)) == ["scripture-2026-11-23.db", "scripture-2026-11-24.db"]


# ---------- notifications ----------

def test_message_counts_due_and_overdue(app):
    assert automation.message_for(DAY) is None
    _active()
    assert automation.message_for(DAY)["body"] == "1 verse due today."
    assert automation.message_for(DAY, evening=True)["body"] == "1 verse still to review today."


@pytest.fixture()
def sent(monkeypatch):
    log = []
    monkeypatch.setattr(automation, "send_push", lambda msg: log.append(msg["body"]) or (1, 0))
    return log


def test_morning_and_evening_once_each(app, sent):
    _active()
    svc.kv_set("last_upkeep", DAY.isoformat())
    db.session.commit()
    at = lambda h, m=0: datetime.combine(DAY, datetime.min.time()).replace(hour=h, minute=m)
    automation.notify_due(at(6, 45))
    assert sent == []
    automation.notify_due(at(7, 0))
    automation.notify_due(at(7, 15))
    assert sent == ["1 verse due today."]
    automation.notify_due(at(21, 0))
    automation.notify_due(at(21, 30))
    assert sent == ["1 verse due today.", "1 verse still to review today."]


def test_no_evening_nudge_when_done(app, sent):
    v = _active()
    svc.record_review(v, "got", "ref_to_text", DAY)
    automation.notify_due(datetime.combine(DAY, datetime.min.time()).replace(hour=21))
    assert sent == []


def test_late_wake_skips_morning_message(app, sent):
    _active()
    automation.notify_due(datetime.combine(DAY, datetime.min.time()).replace(hour=22))
    assert sent == ["1 verse still to review today."]


def test_notifications_can_be_turned_off(app, sent):
    _active()
    cfg = svc.get_settings()
    cfg["notify"]["enabled"] = False
    svc.save_settings(cfg)
    automation.notify_due(datetime.combine(DAY, datetime.min.time()).replace(hour=8))
    assert sent == []


def test_subscribe_and_unsubscribe(app):
    c = app.test_client()
    sub = {"endpoint": "https://web.push.apple.com/abc", "keys": {"p256dh": "k", "auth": "a"},
           "label": "iPhone"}
    assert c.post("/push/subscribe", json=sub).status_code == 200
    assert c.post("/push/subscribe", json=sub).status_code == 200      # same device: no duplicate
    assert PushSubscription.query.count() == 1
    assert c.post("/push/subscribe", json={"endpoint": "http://x"}).status_code == 400
    c.post("/push/unsubscribe", json={"endpoint": sub["endpoint"]})
    assert PushSubscription.query.count() == 0


def test_expired_subscriptions_are_removed(app, monkeypatch):
    import pywebpush

    class Gone:
        status_code = 410

    def fake(**kw):
        raise pywebpush.WebPushException("gone", response=Gone())

    monkeypatch.setattr(pywebpush, "webpush", fake)
    db.session.add(PushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a"))
    db.session.commit()
    assert automation.send_push({"title": "t", "body": "b"}) == (0, 1)
    assert PushSubscription.query.count() == 0


def test_manifest_and_service_worker(app):
    c = app.test_client()
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json["display"] == "standalone"
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "showNotification" in sw.get_data(as_text=True)
    assert sw.headers["Service-Worker-Allowed"] == "/"


def test_tick_runs_maintenance_once(app, sent, monkeypatch):
    calls = []
    monkeypatch.setattr(automation, "check_texts", lambda: calls.append("check") or automation.TextCheck())
    automation.tick(datetime.combine(DAY, datetime.min.time()).replace(hour=2))
    assert calls == []                                   # before 3 AM
    automation.tick(datetime.combine(DAY, datetime.min.time()).replace(hour=3, minute=5))
    automation.tick(datetime.combine(DAY, datetime.min.time()).replace(hour=3, minute=20))
    assert calls == ["check"]


# ---------- connections ----------

def test_connections_saved_in_settings(app, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    c = app.test_client()
    assert svc.openai_key() is None
    c.post("/settings", data={"openai_key": "sk-test-1234"})
    assert svc.openai_key() == "sk-test-1234" and svc.openai_key_source() == "settings"
    page = c.get("/settings").get_data(as_text=True)
    assert "sk-test-1234" not in page and "…1234" in page      # key never echoed back
    c.post("/settings", data={"openai_remove": "on"})
    assert svc.openai_key() is None
    assert "Notification contact" not in c.get("/settings").get_data(as_text=True)


def test_theme_setting(app):
    c = app.test_client()
    assert "data-theme" not in c.get("/").get_data(as_text=True).split(">")[1]   # system by default
    c.post("/settings", data={"theme": "dark"})
    assert '<html lang="en" data-theme="dark">' in c.get("/").get_data(as_text=True)
    c.post("/settings", data={"theme": "light"})
    assert '<html lang="en" data-theme="light">' in c.get("/stage/daily").get_data(as_text=True)
    c.post("/settings", data={"theme": "neon"})                                  # ignored
    assert svc.get_settings()["appearance"]["theme"] == "light"
    c.post("/settings", data={"theme": "system"})
    assert "data-theme" not in c.get("/").get_data(as_text=True).split(">")[1]


def test_keys_are_made_on_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr("webapp.load_dotenv", lambda *a, **k: None)   # ignore the real .env
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY_FILE", raising=False)
    a = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x.db'}", "DATA_DIR": str(tmp_path),
                    "TESTING": True})
    assert (tmp_path / "secret_key").exists() and (tmp_path / "vapid_private.pem").exists()
    assert len(a.config["VAPID_PUBLIC_KEY"]) > 80 and a.config["SECRET_KEY"]
    b = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x.db'}", "DATA_DIR": str(tmp_path),
                    "TESTING": True})
    assert b.config["VAPID_PUBLIC_KEY"] == a.config["VAPID_PUBLIC_KEY"]   # stable across restarts


def test_push_test_explains_no_devices(app):
    r = app.test_client().post("/push/test")
    assert r.json["ok"] is False and "no devices" in r.json["error"]


def test_each_copy_has_its_own_session_cookie(tmp_path):
    a = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'a.db'}",
                    "DATA_DIR": str(tmp_path / "a"), "TESTING": True})
    b = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'b.db'}",
                    "DATA_DIR": str(tmp_path / "b"), "TESTING": True})
    assert a.config["SESSION_COOKIE_NAME"] != b.config["SESSION_COOKIE_NAME"]
    assert a.config["SESSION_COOKIE_NAME"].startswith("featherstone_")
