from __future__ import annotations

import json
import re
from datetime import date

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)

from scripture import ai, refs, schedule, texts

from . import automation
from . import services as svc
from .models import PushSubscription, Verse, db

bp = Blueprint("main", __name__)


def today() -> date:
    override = current_app.config.get("TODAY_OVERRIDE")
    return date.fromisoformat(override) if override else date.today()


@bp.before_app_request
def _upkeep():
    if request.endpoint and request.endpoint != "static":
        svc.daily_upkeep(today())


THEMES = ("system", "light", "dark")


@bp.app_context_processor
def _globals():
    theme = svc.get_settings().get("appearance", {}).get("theme", "system")
    return {"today": today(), "STAGE_TITLES": svc.STAGE_TITLES, "first_letters": svc.first_letters,
            "theme": theme if theme in THEMES else "system"}


@bp.get("/")
def index():
    view = svc.today_view(today())
    return render_template("today.html", tab="today", **view)


@bp.get("/stage/<stage>")
def stage(stage: str):
    if stage not in schedule.STAGES:
        abort(404)
    items = svc.stage_view(stage, today())
    return render_template("stage.html", tab="verses", stage=stage, items=items,
                           counts=svc.stage_counts(), n_due=svc.today_view(today())["n_due"])


@bp.get("/verses")
def verses():
    return redirect(url_for("main.stage", stage="daily"))


@bp.get("/verse/<vid>")
def verse(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    cfg = svc.get_settings()
    s = v.sched()
    return render_template(
        "verse.html", tab="verses", v=v, s=s, cfg=cfg, practice=svc.is_practice(v, today()),
        open_until=schedule.window_end(s, cfg) if s else None,
        label=schedule.label(s, cfg) if s else None,
        progress=svc.progress(v, cfg), rhythm=svc.rhythm(v),
        reviews=v.reviews[:8], overdue=bool(s and schedule.is_overdue(s, today())),
        struggling=svc.struggling(v), move_back=svc.MOVE_BACK.get(v.stage),
        memorizing=(svc.memorize_verse() == v),
    )


@bp.get("/memorize")
def memorize():
    v = svc.memorize_verse()
    if v is None:
        flash("No verse to memorize yet. Start one from your Backlog.")
        return redirect(url_for("main.index"))
    return render_template("memorize.html", tab="today", v=v)


@bp.post("/verse/<vid>/memorize")
def verse_memorize(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    if not v.is_active:
        abort(400)
    svc.pin_memorize(v, today())
    db.session.commit()
    flash(f"{v.display_ref} is your verse of the day.")
    return redirect(url_for("main.index"))


@bp.post("/verse/<vid>/start")
def verse_start(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    if v.stage != "backlog":
        abort(400)
    svc.start_verse(v, today())
    db.session.commit()
    flash(f"{v.display_ref} is in Daily, starting today.")
    return redirect(url_for("main.verse", vid=v.id))


@bp.post("/verse/<vid>/text")
def verse_text(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    action = request.form.get("action")
    if action == "refetch":
        fetched = texts.fetch(v.reference)
        if not fetched:
            flash("Couldn't fetch the text right now.", "error")
        elif texts.sha1(fetched[0]) == v.text_sha:
            flash("Text checked: it matches the source.")
        else:
            svc.set_text(v, *fetched)
            db.session.commit()
            flash("Text updated from the source.")
    elif action == "save":
        body = (request.form.get("text") or "").strip()
        if body:
            svc.set_text(v, body, "manual", manual=True)
            db.session.commit()
            flash("Your text is saved and won't be replaced automatically.")
    return redirect(url_for("main.verse", vid=v.id))


@bp.post("/verse/<vid>/delete")
def verse_delete(vid: str):
    """Delete a verse from any stage, with its review history."""
    v = db.session.get(Verse, vid) or abort(404)
    name, stage = v.display_ref, v.stage
    db.session.delete(v)
    db.session.commit()
    flash(f"Deleted {name}.")
    if request.form.get("next") == "add":
        return redirect(url_for("main.add"))
    return redirect(url_for("main.stage", stage=stage))


# The Add page keeps a "round" in the session so you can work through
# suggestions without starting over: the theme, every verse already suggested
# (never offered twice), the suggestion on screen, and what you've added.
# Every action posts and redirects back here, so a refresh never adds twice.

def _round() -> dict:
    r = session.get("adding")
    if not r or r.get("day") != today().isoformat():
        r = {"day": today().isoformat(), "topic": None, "seen": [], "current": None,
             "text": None, "added": [], "error": None}
    return r


def _save_round(r: dict) -> None:
    session["adding"] = r
    session.modified = True


def _next_suggestion(r: dict) -> None:
    sug = svc.suggest_verse(r["topic"], r["seen"])
    r["seen"] = sug.seen[-150:]
    r["error"] = sug.error
    r["current"] = sug.ref.display if sug.ref else None
    fetched = texts.fetch(str(sug.ref)) if sug.ref else None
    r["text"] = fetched[0] if fetched else None


@bp.route("/add", methods=["GET", "POST"])
def add():
    cfg = svc.get_settings()
    r = _round()
    ai_ready = ai.available(svc.openai_key())
    if request.method == "POST":
        action = request.form.get("action", "add")
        if action in ("suggest", "skip"):
            if not ai_ready:
                flash("Add an OpenAI API key in Settings → Connections to use AI suggestions.", "error")
            else:
                if action == "suggest":
                    # Blank: the default theme from Settings.
                    r["topic"] = ((request.form.get("topic") or "").strip()
                                  or cfg["auto_add"].get("topic_default", "").strip())
                _next_suggestion(r)
        elif action == "clear":
            r = _round() | {"topic": r["topic"], "seen": r["seen"]}
            r["added"], r["current"], r["text"], r["error"] = [], None, None, None
        elif action in ("add", "today"):
            reference = request.form.get("reference", "")
            from_ai = request.form.get("origin") == "ai"
            res = svc.add_verse(reference, origin="ai" if from_ai else "manual")
            if res.error:
                flash(res.error, "error")
                if not from_ai:
                    session["add_reference"] = reference  # keep what was typed
            else:
                v = res.verse
                for w in res.warnings:
                    flash(w, "error")
                if action == "today":
                    svc.start_verse(v, today())
                    db.session.commit()
                    n, limit = svc.stage_counts()["daily"], int(cfg["auto_add"].get("daily_limit", 0) or 0)
                    if limit and n > limit:
                        flash(f"Daily now has {n} verses, over your limit of {limit}.", "error")
                r["added"] = ([v.id] + [x for x in r["added"] if x != v.id])[:20]
                if from_ai and ai_ready:
                    _next_suggestion(r)  # straight on to the next one
        _save_round(r)
        return redirect(url_for("main.add"))

    added = [v for v in (db.session.get(Verse, i) for i in r["added"]) if v is not None]
    return render_template(
        "add.html", tab="add", ai_ready=ai_ready, r=r, added=added,
        topic=r["topic"] or "", default_topic=cfg["auto_add"].get("topic_default", ""),
        reference=session.pop("add_reference", ""),
        daily_count=svc.stage_counts()["daily"],
        daily_limit=int(cfg["auto_add"].get("daily_limit", 0) or 0))


def _int(name: str, default: int, lo: int = 0, hi: int = 999) -> int:
    try:
        return max(lo, min(hi, int(request.form.get(name, default))))
    except (TypeError, ValueError):
        return default


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = svc.get_settings()
    if request.method == "POST":
        cfg["cadence"]["daily_repeats"] = _int("daily_repeats", cfg["cadence"]["daily_repeats"], 1, 60)
        cfg["cadence"]["weekly_repeats"] = _int("weekly_repeats", cfg["cadence"]["weekly_repeats"], 1, 60)
        cfg["cadence"]["monthly_repeats"] = _int("monthly_repeats", cfg["cadence"]["monthly_repeats"], 1, 120)
        try:
            months = [int(x) for x in request.form.get("review_months", "").replace(" ", "").split(",") if x]
            if months and all(1 <= m <= 60 for m in months):
                cfg["mastered"]["review_months"] = months
        except ValueError:
            flash("Mastered checks should be months separated by commas, like 3, 6, 12.", "error")
        cfg["mastered"]["yearly_interval"] = _int("yearly_interval", cfg["mastered"]["yearly_interval"], 1, 60)
        cfg["auto_add"]["every_n_days"] = _int("every_n_days", cfg["auto_add"]["every_n_days"], 0, 365)
        g = cfg.setdefault("grace_days", {})
        for st in ("weekly", "monthly", "mastered"):
            g[st] = _int(f"grace_{st}", schedule.grace_days(st, cfg), 0, schedule.MAX_GRACE_DAYS[st])
        cfg["auto_add"]["daily_limit"] = _int("daily_limit", cfg["auto_add"].get("daily_limit", 7), 0, 100)
        cfg["auto_add"]["topic_default"] = (request.form.get("topic_default") or "").strip()
        cfg["auto_add"]["chatgpt_fallback"] = request.form.get("chatgpt_fallback") == "on"
        cfg["obfuscation"]["enabled"] = request.form.get("hints_enabled") == "on"
        n = cfg.setdefault("notify", {})
        n["enabled"] = request.form.get("notify_enabled") == "on"
        n["evening_enabled"] = request.form.get("notify_evening_enabled") == "on"
        n["midday_enabled"] = request.form.get("notify_midday_enabled") == "on"
        for key in ("morning", "midday", "evening"):
            val = (request.form.get(f"notify_{key}") or "").strip()
            if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", val):
                n[key] = val
        # Connections: personal parameters
        if request.form.get("openai_remove") == "on":
            svc.kv_set("secret.openai_api_key", None)
        elif (request.form.get("openai_key") or "").strip():
            svc.kv_set("secret.openai_api_key", request.form["openai_key"].strip())
        if request.form.get("theme") in THEMES:
            cfg.setdefault("appearance", {})["theme"] = request.form["theme"]
        if request.form.get("answer_style") in svc.ANSWER_STYLES:
            cfg.setdefault("quiz", {})["answer_style"] = request.form["answer_style"]
        svc.save_settings(cfg)
        flash("Settings saved.")
        return redirect(url_for("main.settings"))
    key = svc.openai_key()
    return render_template("settings.html", tab="settings", cfg=cfg, ai_ready=ai.available(key),
                           openai_hint=("…" + key[-4:]) if key else None,
                           openai_source=svc.openai_key_source(),
                           last_auto_added=svc.kv_get("last_auto_added"),
                           vapid_key=current_app.config["VAPID_PUBLIC_KEY"] if automation.vapid_ready() else "",
                           devices=PushSubscription.query.count())


# ---------------- quiz ----------------
#
# The queue isn't stored: after a review the verse's due date moves on, so it
# drops out of review_queue() by itself. The session only keeps this sitting's
# progress (how many done, results, verses skipped for now).
#
# A practice sitting instead has a fixed "set" of verse ids (one verse or a
# whole stage) and works through each once. A verse that happens to be due
# counts as its review; any other verse is practice and never counts.

def _new_sitting(verse_ids: list[str] | None = None) -> dict:
    return {"day": today().isoformat(), "done": 0, "total": len(verse_ids or []),
            "skipped": [], "seen": [], "set": verse_ids,
            "results": {"got": 0, "shaky": 0, "missed": 0}}


def _sitting() -> dict:
    s = session.get("sitting")
    if not s or s.get("day") != today().isoformat():
        s = _new_sitting()
    s.setdefault("seen", [])
    s.setdefault("set", None)
    return s


def _save_sitting(s: dict) -> None:
    session["sitting"] = s
    session.modified = True


def _current(s: dict):
    if s.get("set") is not None:
        seen = set(s["seen"])
        queue = [v for v in (db.session.get(Verse, i) for i in s["set"])
                 if v is not None and v.id not in seen and v.sched() is not None]
    else:
        queue = svc.review_queue(today())
    skipped = set(s["skipped"])
    ordered = [v for v in queue if v.id not in skipped] + [v for v in queue if v.id in skipped]
    s["total"] = max(s["total"], s["done"] + len(ordered))
    return ordered[0] if ordered else None, len(ordered)


def _quiz_ctx(v: Verse, s: dict, mode: str) -> dict:
    cfg = svc.get_settings()
    sch = v.sched()
    practice = svc.is_practice(v, today())
    return {"tab": "today", "v": v, "mode": mode, "cfg": cfg, "practice": practice,
            "open_until": schedule.window_end(sch, cfg),
            "stage_label": schedule.label(sch, cfg),
            "overdue": schedule.is_overdue(sch, today()),
            "position": s["done"] + 1, "total": max(s["total"], 1),
            "pct": round(100 * s["done"] / max(s["total"], 1))}


@bp.get("/review")
def review():
    if request.args.get("start"):
        session.pop("sitting", None)
    s = _sitting()
    v, remaining = _current(s)
    _save_sitting(s)
    if v is None:
        return render_template("review_done.html", tab="today", s=s,
                               counts=svc.stage_counts(), practice_set=s.get("set") is not None)
    mode = svc.choose_mode(v, today())
    ctx = _quiz_ctx(v, s, mode)
    if mode == "ref_to_text":
        ctx["tokens"] = svc.hint_tokens(v, ctx["cfg"])
    else:
        # ?answer=type|choice switches style for this question only.
        style = request.args.get("answer")
        if style not in ("type", "choice"):
            style = svc.answer_style(v, today(), ctx["cfg"])
        if not svc.choices_allowed(v):
            style = "type"
        ctx["style"] = style
        ctx["choices_allowed"] = svc.choices_allowed(v)
        if style == "choice":
            ctx["options"] = svc.choice_options(v, today())
        ctx["books"] = refs.BOOKS
    return render_template("review.html", **ctx)


@bp.post("/review/<vid>/check")
def review_check(vid: str):
    """Text → reference: grade the typed reference and show the answer."""
    v = db.session.get(Verse, vid) or abort(404)
    s = _sitting()
    answer = (request.form.get("answer") or "").strip()
    gave_up = request.form.get("gave_up") == "1"
    chose = request.form.get("style") == "choice"
    if gave_up or not v.ref:
        grade, reading = "wrong", None
    else:
        grade, reading = refs.grade(answer, v.ref)
    suggested = {"exact": "got", "overlap": "shaky"}.get(grade, "missed")
    ctx = _quiz_ctx(v, s, "text_to_ref")
    ctx.update(answer=answer, grade=grade, suggested=suggested, gave_up=gave_up,
               parsed=reading, chose=chose)
    return render_template("review_answer.html", **ctx)


@bp.post("/review/<vid>/grade")
def review_grade(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    result = request.form.get("result")
    mode = request.form.get("mode")
    if result not in ("got", "shaky", "missed") or mode not in svc.QUIZ_MODES:
        abort(400)
    if v.sched() is None:
        return redirect(url_for("main.review"))
    svc.record_review(v, result, mode, today(), answer=request.form.get("answer") or None)
    s = _sitting()
    s["done"] += 1
    s["results"][result] += 1
    s["skipped"] = [x for x in s["skipped"] if x != v.id]
    s["seen"].append(v.id)
    _save_sitting(s)
    return redirect(url_for("main.review"))


@bp.post("/practice")
def practice():
    """Start a practice sitting for one verse (verse=<id>) or a stage (stage=<name>)."""
    if request.form.get("verse"):
        v = db.session.get(Verse, request.form["verse"]) or abort(404)
        ids = [v.id] if v.sched() is not None else []
    else:
        stage = request.form.get("stage")
        if stage not in schedule.ACTIVE_STAGES:
            abort(400)
        ids = [i.verse.id for i in svc.stage_view(stage, today())]
    if not ids:
        flash("Nothing to practice there yet.", "error")
        return redirect(request.referrer or url_for("main.index"))
    session["sitting"] = _new_sitting(ids)
    session.modified = True
    return redirect(url_for("main.review"))


@bp.post("/verse/<vid>/move")
def verse_move(vid: str):
    v = db.session.get(Verse, vid) or abort(404)
    stage = request.form.get("stage")
    if stage not in schedule.STAGES:
        abort(400)
    done = _int("done", 0, 0, 200)
    svc.move_verse(v, stage, today(), done=done)
    where = svc.STAGE_TITLES[stage]
    flash(f"Moved {v.display_ref} to {where}." if stage == "backlog" else
          f"{v.display_ref} is now in {where}; next review {v.due_date.strftime('%a, %b %-d')}.")
    return redirect(url_for("main.verse", vid=v.id))


@bp.post("/review/<vid>/skip")
def review_skip(vid: str):
    s = _sitting()
    if vid not in s["skipped"]:
        s["skipped"].append(vid)
    _save_sitting(s)
    return redirect(url_for("main.review"))


# ---------------- installable app + notifications ----------------

ICON_VERSION = "2"  # bump when the icon changes so phones fetch the new one


@bp.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": "Featherstone", "short_name": "Featherstone", "start_url": "/",
        "scope": "/", "display": "standalone",
        "background_color": "#F7F7F5", "theme_color": "#F7F7F5",
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png", v=ICON_VERSION), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png", v=ICON_VERSION), "sizes": "512x512", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png", v=ICON_VERSION), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return current_app.response_class(json.dumps(data), mimetype="application/manifest+json")


@bp.get("/sw.js")
def service_worker():
    # Served from the root so it controls the whole app.
    resp = current_app.send_static_file("js/sw.js")
    resp.headers["Content-Type"] = "text/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.post("/push/subscribe")
def push_subscribe():
    data = request.get_json(silent=True) or {}
    keys = data.get("keys") or {}
    endpoint, p256dh, auth = data.get("endpoint"), keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth) or not endpoint.startswith("https://"):
        abort(400)
    sub = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=endpoint, p256dh=p256dh, auth=auth)
        db.session.add(sub)
    sub.p256dh, sub.auth = p256dh, auth
    sub.label = (data.get("label") or "")[:120] or None
    db.session.commit()
    current_app.logger.warning("push subscription saved for %s", sub.label or "device")
    return {"ok": True}


@bp.post("/push/unsubscribe")
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    sub = PushSubscription.query.filter_by(endpoint=data.get("endpoint")).first()
    if sub:
        db.session.delete(sub)
        db.session.commit()
    return {"ok": True}


@bp.post("/push/test")
def push_test():
    if not automation.vapid_ready():
        return {"ok": False, "error": "Notifications aren't set up on the server yet."}, 503
    if PushSubscription.query.count() == 0:
        return {"ok": False, "error": "no devices are signed up on the server"}
    errors: list[str] = []
    sent, failed = automation.send_push({"title": "Featherstone",
                                         "body": "Test notification — you're all set.", "url": "/"}, errors)
    return {"ok": sent > 0, "sent": sent, "failed": failed, "error": "; ".join(errors) or None}


@bp.post("/verse/<vid>/pending")
def verse_pending(vid: str):
    """Accept or dismiss text the daily check found changed at the source."""
    v = db.session.get(Verse, vid) or abort(404)
    if v.pending_text and request.form.get("action") == "accept":
        svc.set_text(v, v.pending_text, "nephi.org" if v.text_source in (None, "import") else v.text_source)
        flash("Updated to the source's current text.")
    else:
        flash("Kept your current text.")
    v.pending_text = None
    db.session.commit()
    return redirect(url_for("main.verse", vid=v.id))
