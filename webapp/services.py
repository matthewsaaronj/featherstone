"""App operations: settings, adding verses, intake from Backlog, daily catch-up,
recording reviews. Routes stay thin and call into here."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from flask import current_app
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from scripture import ai, choices, clues, hints, refs, schedule, texts
from scripture.settings import DEFAULTS, from_agent_config, merged

from .models import KeyValue, Review, Verse, db

STAGE_TITLES = {"backlog": "Backlog", "daily": "Daily", "weekly": "Weekly",
                "monthly": "Monthly", "mastered": "Mastered"}


# ---------------- key/value + settings ----------------

def kv_get(key: str) -> Optional[str]:
    row = db.session.get(KeyValue, key)
    return row.value if row else None


def kv_set(key: str, value: Optional[str]) -> None:
    # Atomic upsert: two first requests at once must not both INSERT.
    stmt = sqlite_insert(KeyValue).values(key=key, value=value)
    db.session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))


def get_settings() -> dict:
    raw = kv_get("settings")
    if raw is None:
        seed_path = current_app.config.get("AGENT_CONFIG_PATH")
        cfg = from_agent_config(seed_path) if seed_path and os.path.exists(seed_path) else merged({})
        # Insert only if still missing; a simultaneous request may have seeded it.
        stmt = sqlite_insert(KeyValue).values(key="settings", value=json.dumps(cfg))
        db.session.execute(stmt.on_conflict_do_nothing(index_elements=["key"]))
        db.session.commit()
        return merged(json.loads(kv_get("settings")))
    return merged(json.loads(raw))


# ---------------- connections (personal parameters, set in Settings) ----------------

def openai_key() -> Optional[str]:
    """The OpenAI key from Settings, else OPENAI_API_KEY from .env/the environment."""
    return (kv_get("secret.openai_api_key") or os.environ.get("OPENAI_API_KEY", "")).strip() or None


def openai_key_source() -> Optional[str]:
    if kv_get("secret.openai_api_key"):
        return "settings"
    return "env" if os.environ.get("OPENAI_API_KEY", "").strip() else None


def push_contact() -> str:
    """Contact sent to phone push services with each notification. They require
    one but don't check it; the default placeholder works (VAPID_SUBJECT)."""
    return current_app.config["VAPID_SUBJECT"]


def save_settings(cfg: dict) -> None:
    kv_set("settings", json.dumps(merged(cfg)))
    db.session.commit()


# ---------------- adding verses ----------------

def find_overlap(r: refs.Ref, exclude_id: Optional[str] = None) -> Optional[Verse]:
    for v in Verse.query.filter(Verse.book == r.book, Verse.chapter == r.chapter).all():
        if v.id != exclude_id and refs.overlaps(r, v.ref):
            return v
    return None


@dataclass
class AddResult:
    verse: Optional[Verse] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def add_verse(reference: str, *, origin: str = "manual", text: Optional[str] = None) -> AddResult:
    """Add a verse to the end of the Backlog, fetching its text."""
    r = refs.parse(reference)
    if r is None:
        return AddResult(error="That doesn't look like a reference. Try “Alma 34:14–15”.")
    clash = find_overlap(r)
    if clash:
        where = STAGE_TITLES.get(clash.stage, clash.stage)
        return AddResult(error=f"{r.display} overlaps {clash.display_ref}, already in {where}.")

    v = Verse(id=str(uuid.uuid4()), reference=str(r), ref_key=r.key, book=r.book,
              chapter=r.chapter, verse_start=r.start, verse_end=r.end, stage="backlog",
              origin=origin, created_at=datetime.now())
    res = AddResult(verse=v)
    if text:
        set_text(v, text, "manual", manual=True)
    else:
        fetched = texts.fetch(str(r))
        if fetched:
            set_text(v, *fetched)
        else:
            res.warnings.append("Couldn't find the text online. You can paste it on the verse page.")

    last = db.session.query(db.func.max(Verse.backlog_pos)).scalar() or 0
    v.backlog_pos = last + 1
    db.session.add(v)
    db.session.commit()
    return res


@dataclass
class Suggestion:
    ref: Optional[refs.Ref] = None
    seen: list[str] = field(default_factory=list)   # every reference suggested this round
    error: Optional[str] = None


def suggest_verse(topic: Optional[str], seen: list[str], tries: int = 3) -> Suggestion:
    """Ask the AI for a verse that isn't in the program and wasn't already
    suggested this round ("Suggest another"). Retries when it repeats itself."""
    out = Suggestion(seen=[s for s in seen if refs.parse(s)])
    program = [x.reference for x in Verse.query.all()]
    started = time.monotonic()
    for attempt in range(tries):
        if attempt and time.monotonic() - started > 25:
            break  # don't keep the page waiting; "Try again" is quicker
        raw = ai.suggest(topic, program + out.seen, api_key=openai_key())
        r = refs.parse(raw or "")
        if r is None:
            continue
        repeat = any(refs.overlaps(r, refs.parse(s)) for s in out.seen)
        if str(r) not in out.seen:
            out.seen.append(str(r))
        if repeat or find_overlap(r):
            continue
        out.ref = r
        return out
    out.error = ("Couldn't find a new verse for that theme just now. "
                 "Try again, or try a different theme.")
    return out


def set_text(v: Verse, text: str, source: str, manual: bool = False) -> None:
    v.text = text.strip()
    v.text_sha = texts.sha1(v.text)
    v.text_source = source
    v.manual_override = manual


def start_verse(v: Verse, on: date) -> None:
    """Move a Backlog verse into Daily, with `on` as its start date."""
    v.apply(schedule.start(on))
    v.start_date = on
    v.backlog_pos = None
    v.retry_date = None


# ---------------- daily upkeep ----------------

def catch_up_all(on: date, cfg: dict) -> int:
    """Close reviews whose grace window has passed. Returns how many were lost."""
    total = 0
    for v in Verse.query.filter(Verse.stage.in_(schedule.ACTIVE_STAGES)).all():
        s = v.sched()
        if s is None:
            continue
        before = s
        s, missed = schedule.catch_up(s, on, cfg)
        for i, d in enumerate(missed):
            db.session.add(Review(verse_id=v.id, slot_date=d, stage=before.stage,
                                  position=before.done, mode="auto", result="skipped",
                                  counted=False, reviewed_at=datetime.now()))
        if missed:
            v.apply(s)
            v.retry_date = None
            total += len(missed)
    return total


def _intake_allowed(on: date, cfg: dict) -> bool:
    n = int(cfg["auto_add"].get("every_n_days", 0))
    if n <= 0:
        return False
    limit = int(cfg["auto_add"].get("daily_limit", 0) or 0)
    if limit and Verse.query.filter_by(stage="daily").count() >= limit:
        return False  # Daily is full; try again on a later day
    last = kv_get("last_auto_added")
    if not last:
        return True
    return (on - date.fromisoformat(last[:10])).days >= n


def intake(on: date, cfg: dict, force: bool = False) -> Optional[Verse]:
    """Bring one verse into Daily: next in Backlog, else ChatGPT if allowed.

    Gated by auto_add.every_n_days unless force=True (the "Start now" button).
    """
    if not force and not _intake_allowed(on, cfg):
        return None
    nxt = (Verse.query.filter_by(stage="backlog")
           .order_by(Verse.backlog_pos.is_(None), Verse.backlog_pos, Verse.created_at).first())
    if nxt is None and cfg["auto_add"].get("chatgpt_fallback") and ai.available(openai_key()):
        exclusions = [v.reference for v in Verse.query.all()]
        suggestion = ai.suggest(cfg["auto_add"].get("topic_default"), exclusions, api_key=openai_key())
        if suggestion:
            res = add_verse(suggestion, origin="ai")
            nxt = res.verse
    if nxt is None:
        return None
    start_verse(nxt, on)
    kv_set("last_auto_added", on.isoformat())
    db.session.commit()
    return nxt


def _claim_day(key: str, on: date) -> bool:
    """Atomically mark `key` as done for `on`. True only for the one caller that
    changed it, so simultaneous requests can't both run the daily upkeep."""
    day = on.isoformat()
    stmt = sqlite_insert(KeyValue).values(key=key, value=day)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": day},
                                      where=(KeyValue.value != day))
    claimed = db.session.execute(stmt).rowcount == 1
    db.session.commit()
    return claimed


def daily_upkeep(on: date) -> None:
    """Run once per day on first page load: catch up missed slots, then intake."""
    if kv_get("last_upkeep") == on.isoformat() or not _claim_day("last_upkeep", on):
        return
    cfg = get_settings()
    catch_up_all(on, cfg)
    db.session.commit()
    intake(on, cfg)


# ---------------- reviews ----------------

def _stamp(on: date) -> datetime:
    """Review timestamp on the app's day `on` (matters when TODAY_OVERRIDE is set)."""
    return datetime.combine(on, datetime.now().time())


def is_practice(v: Verse, on: date) -> bool:
    """Practice never counts: the verse has no open review, or it was already
    tried today and missed (it comes back tomorrow, while its window is open)."""
    s = v.sched()
    return s is None or not schedule.is_due(s, on) or bool(v.retry_date and v.retry_date > on)


def record_review(v: Verse, result: str, mode: str, on: date, answer: Optional[str] = None) -> None:
    """Record a quiz result. Got it / Shaky on an open review counts and moves on.
    Missed leaves the review open and brings the verse back tomorrow; if its
    window closes first, catch-up counts it missed (see schedule.catch_up)."""
    cfg = get_settings()
    s = v.sched()
    if s is None:
        return
    practice = is_practice(v, on)
    counted = result in ("got", "shaky")
    db.session.add(Review(verse_id=v.id, slot_date=None if practice else s.due, stage=s.stage,
                          position=s.done, mode=mode, result=result,
                          counted=counted and not practice, practice=practice,
                          answer=answer, reviewed_at=_stamp(on)))
    if practice:
        if counted and v.retry_date and v.retry_date <= on:
            v.retry_date = None  # an old practice retry, now done
    else:
        if counted:
            v.apply(schedule.record(s, counted=True, cfg=cfg))
            v.retry_date = None
            v.hint_salt = 0
        else:
            v.retry_date = on + timedelta(days=1)
    db.session.commit()


# ---------------- views ----------------

@dataclass
class Item:
    verse: Verse
    label: str
    due: Optional[date]
    overdue: bool = False
    practice: bool = False
    # Progress marker: "dots" (Daily/Weekly: one per review) or
    # "bar" (Monthly: too many reviews for dots).
    marker: Optional[str] = None
    dots: list[str] = field(default_factory=list)   # CSS classes: d-done | d-todo
    done: int = 0
    total: int = 0
    struggling: bool = False
    needs_work: bool = False
    urgent: bool = False                # do it today or the stage gets longer (again)
    open_until: Optional[date] = None   # last day a late review still counts

    @property
    def pct(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


def _item(v: Verse, on: date, cfg: dict, practice: bool = False) -> Item:
    s = v.sched()
    if s is None:
        return Item(v, STAGE_TITLES.get(v.stage, v.stage), None)
    it = Item(v, schedule.label(s, cfg), s.due, overdue=schedule.is_overdue(s, on),
              practice=practice, struggling=struggling(v), open_until=schedule.window_end(s, cfg))
    it.needs_work = not it.struggling and needs_work(v)
    total = s.required(cfg)
    if total is None:  # Mastered: no fixed length
        return it
    it.done, it.total = s.done, total
    line = _timeline(v, s)
    if schedule.is_due(s, on) and not practice:
        if s.stage == "daily":
            # Missed yesterday: do it today so it doesn't slip a second day.
            it.urgent = bool(line) and line[-1][1] == "d-miss" and line[-1][0] >= on - timedelta(days=1)
        else:
            # Late, and today is the last day it still counts.
            it.urgent = it.overdue and schedule.window_end(s, cfg) <= on
    if s.stage in ("daily", "weekly"):
        # One dot per review the stage now takes, in date order: blue for a good
        # review, red for a missed one (each made the stage one longer), then gray to go.
        it.marker = "dots"
        it.dots = _dot_order(line, s.done, s.extra) + ["d-todo"] * (total - s.done)
    else:
        it.marker = "bar"
    return it


def _timeline(v: Verse, s: schedule.Schedule) -> list[tuple[date, str]]:
    """This stage's closed review slots, oldest first: (slot date, "d-done" | "d-miss").
    A slot is done if a counted review landed on it, missed if it was lost or
    failed and never made good. The open slot (s.due) isn't settled yet."""
    slots: dict[date, str] = {}
    for r in v.reviews:  # newest first
        if r.mode == "manual":
            break  # moved by hand: what came before belongs to another run
        if r.stage != s.stage or r.practice or r.slot_date is None or r.slot_date >= s.due:
            continue
        if r.counted:
            slots[r.slot_date] = "d-done"
        elif r.result in ("skipped", "missed"):
            slots.setdefault(r.slot_date, "d-miss")
    return sorted(slots.items())


def _dot_order(line: list[tuple[date, str]], done: int, extra: int) -> list[str]:
    """Dots for the reviews so far, in the order they happened. The counts are
    the truth; history fills in the order. Reviews older than the history (from
    before it was kept) go first."""
    seq = [kind for _, kind in line]
    for kind, want in (("d-done", done), ("d-miss", extra)):
        while seq.count(kind) > want:
            seq.remove(kind)  # drops the oldest
    return (["d-done"] * (done - seq.count("d-done")) +
            ["d-miss"] * (extra - seq.count("d-miss")) + seq)


# ---------------- verse of the day ----------------
# The notecard in your pocket: the newest verse in Daily, until the next one
# starts. Pinning another verse holds until a newer Daily verse starts.

def memorize_verse() -> Optional[Verse]:
    newest = (Verse.query.filter_by(stage="daily")
              .order_by(Verse.start_date.desc(), Verse.done, Verse.created_at.desc()).first())
    pin = (kv_get("memorize_pin") or "").split("|")
    if len(pin) == 2:
        v = db.session.get(Verse, pin[0])
        pinned_on = date.fromisoformat(pin[1])
        if v is not None and v.is_active and not (
                newest and newest.start_date and newest.start_date > pinned_on):
            return v
    return newest


def pin_memorize(v: Optional[Verse], on: date) -> None:
    kv_set("memorize_pin", f"{v.id}|{on.isoformat()}" if v else None)


_WORD = re.compile(r"[A-Za-z][A-Za-z’']*")


def first_letters(text: str) -> str:
    """'And now, my brethren' -> 'A n, m b'. Punctuation and line breaks stay."""
    out = _WORD.sub(lambda m: m.group(0)[0], text or "")
    return re.sub(r"[ \t]{2,}", " ", out)


def today_view(on: date) -> dict:
    cfg = get_settings()
    active = Verse.query.filter(Verse.stage.in_(schedule.ACTIVE_STAGES)).all()
    overdue, due_by_stage, practice = [], {k: [] for k in schedule.ACTIVE_STAGES}, []
    for v in active:
        s = v.sched()
        if s is None:
            continue
        if v.retry_date and v.retry_date > on:
            continue  # missed today; back tomorrow
        if schedule.is_overdue(s, on):
            overdue.append(_item(v, on, cfg))
        elif schedule.is_due(s, on):
            due_by_stage[s.stage].append(_item(v, on, cfg))
        elif v.retry_date and v.retry_date <= on:
            practice.append(_item(v, on, cfg, practice=True))
    # Urgent first (last chance before the stage gets longer), most missed first.
    overdue.sort(key=lambda i: (not i.urgent, i.due))
    for lst in due_by_stage.values():
        lst.sort(key=lambda i: (not i.urgent, -i.verse.extra, i.verse.reference))
    n_due = len(overdue) + sum(len(l) for l in due_by_stage.values())
    return {"overdue": overdue, "due_by_stage": due_by_stage, "practice": practice,
            "memorize": (m := memorize_verse()), "also_carry": also_carry(m), "n_due": n_due, "n_overdue": len(overdue), "counts": stage_counts()}


def stage_counts() -> dict[str, int]:
    rows = db.session.query(Verse.stage, db.func.count()).group_by(Verse.stage).all()
    out = {k: 0 for k in schedule.STAGES}
    out.update({k: n for k, n in rows})
    return out


def stage_view(stage: str, on: date) -> list[Item]:
    cfg = get_settings()
    q = Verse.query.filter_by(stage=stage)
    if stage == "backlog":
        q = q.order_by(Verse.backlog_pos.is_(None), Verse.backlog_pos, Verse.created_at)
        return [_item(v, on, cfg) for v in q.all()]
    items = [_item(v, on, cfg) for v in q.all()]
    items.sort(key=lambda i: (i.due or date.max, i.verse.reference))
    return items


def progress(v: Verse, cfg: dict) -> list[dict]:
    """Per-stage progress bars for the verse page."""
    cad = cfg["cadence"]
    order = ["daily", "weekly", "monthly"]
    base = {"daily": cad["daily_repeats"], "weekly": cad["weekly_repeats"],
            "monthly": cad["monthly_repeats"]}
    out = []
    cur = order.index(v.stage) if v.stage in order else (3 if v.stage == "mastered" else -1)
    for i, st in enumerate(order):
        if i < cur:
            out.append({"name": STAGE_TITLES[st], "done": base[st], "total": base[st], "pct": 100, "state": "done"})
        elif i == cur:
            total = base[st]
            out.append({"name": STAGE_TITLES[st], "done": v.done, "total": total, "extra": v.extra,
                        "pct": round(100 * v.done / total) if total else 0, "state": "current"})
        else:
            out.append({"name": STAGE_TITLES[st], "done": 0, "total": base[st], "pct": 0, "state": "todo"})
    out.append({"name": "Mastered", "done": v.done if v.stage == "mastered" else 0, "total": None,
                "pct": 100 if v.stage == "mastered" else 0,
                "state": "current" if v.stage == "mastered" else "todo"})
    return out


def rhythm(v: Verse) -> Optional[str]:
    if v.stage == "daily":
        return "Every day"
    if v.stage == "weekly" and v.anchor_weekday is not None:
        return ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays",
                "Saturdays", "Sundays"][v.anchor_weekday]
    if v.stage in ("monthly", "mastered") and v.anchor_day:
        d = v.anchor_day
        suffix = "th" if 11 <= d <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"The {d}{suffix} monthly" if v.stage == "monthly" else f"On the {d}{suffix}"
    return None


def move_verse(v: Verse, stage: str, on: date, done: int = 0) -> None:
    """Move a verse to any stage by hand, logged in its review history.

    Backlog: clears its schedule and puts it at the front of the Backlog, so it
    is the next verse to start. Other stages keep the verse's weekday and
    day-of-month rhythm (today's, if it never had one) and start the stage at
    `done` reviews.
    """
    cfg = get_settings()
    before = v.stage
    if stage == "backlog":
        first = db.session.query(db.func.min(Verse.backlog_pos)).scalar()
        v.stage, v.done, v.extra, v.due_date, v.retry_date = "backlog", 0, 0, None, None
        v.backlog_pos = (first or 1) - 1
    else:
        wd = v.anchor_weekday if v.anchor_weekday is not None else on.weekday()
        day = v.anchor_day or on.day
        v.apply(schedule.place(stage, on, wd, day, cfg, done=done))
        v.retry_date = None
        v.backlog_pos = None
        if v.start_date is None:
            v.start_date = on
    db.session.add(Review(verse_id=v.id, stage=stage, position=v.done, mode="manual",
                          result="moved", counted=False, answer=before,
                          reviewed_at=datetime.now()))
    db.session.commit()


QUIZ_MODES = ("both", "text_to_ref", "ref_to_text")


def review_queue(on: date) -> list[Verse]:
    """Verses to quiz now, in Today's order: overdue, due by stage, then practice retries."""
    view = today_view(on)
    items = list(view["overdue"])
    for st in schedule.ACTIVE_STAGES:
        items += view["due_by_stage"][st]
    items += view["practice"]
    return [i.verse for i in items]


def _seed(v: Verse, on: date, what: str) -> int:
    return int(texts.sha1(f"{v.id}|{on.isoformat()}|{what}")[:8], 16)


@dataclass
class ReviewPlan:
    """What a two-step review shows: the clue for "Which verse is this?", then
    the help on offer for "Now say the whole verse"."""
    clue_kind: str          # full | sentence | phrase
    clue: str
    support: str            # full | letters | blanks | none
    tokens: list            # words with blanks, for support == "blanks"


def review_plan(v: Verse, on: date, cfg: dict) -> ReviewPlan:
    pos = v.done or 0
    text = v.text or ""
    kind = clues.clue_kind(v.stage, pos, _seed(v, on, "cluekind"))
    clue = clues.clue(text, kind, _seed(v, on, "clue")) if text else ""
    if kind != "full" and " ".join(clue.split()) == " ".join(text.split()):
        kind = "full"  # a short verse: the "sentence" is the whole thing
    support = "none"
    if text and cfg["obfuscation"].get("enabled", True):
        support = clues.support_kind(v.stage, pos, _seed(v, on, "support"))
    tokens = []
    if support == "blanks":
        ratio = min(hints.visible_ratio(v.stage, pos, cfg), 0.7)
        ratio = max(ratio, 0.1)
        tokens = hints.tokenize(text, ratio, hints.seed_for(v.id, v.stage, pos, v.hint_salt),
                                int(cfg["obfuscation"].get("min_word_len", 3)))
    return ReviewPlan(kind if text else "full", clue or text, support, tokens)


ANSWER_STYLES = ("type", "choice", "mix")


def answer_style(v: Verse, on: date, cfg: dict) -> str:
    """How to answer "Which verse is this?": "type" or "choice" (multiple choice).
    With the "mix" setting it alternates by a stable per-verse, per-day coin flip."""
    if not choices_allowed(v, cfg):
        return "type"
    quiz = cfg.get("quiz", {})
    style = quiz.get("answer_style", "mix")
    if style in ("type", "choice"):
        return style
    share = float(quiz.get("choice_share", {}).get(v.stage, 0.5))
    roll = int(texts.sha1(f"{v.id}|{on.isoformat()}|style")[:4], 16) / 0x10000
    return "choice" if roll < share else "type"


def choices_allowed(v: Verse, cfg: Optional[dict] = None) -> bool:
    """Mastered verses are always typed: no multiple choice to lean on. In Daily,
    choices stop after the first few days (quiz.choice_days_daily)."""
    if v.stage == "mastered" or v.ref is None:
        return False
    if v.stage == "daily":
        days = int((cfg or get_settings()).get("quiz", {}).get("choice_days_daily", 5))
        return (v.done or 0) < days
    return True


# A verse is "struggling" after this many misses in its last few quiz reviews
# (counting only reviews since it was last moved by hand).
STRUGGLE_MISSES, STRUGGLE_WINDOW = 3, 5
MOVE_BACK = {"daily": "daily", "weekly": "daily", "monthly": "weekly", "mastered": "monthly"}


def struggling(v: Verse) -> bool:
    if v.stage not in MOVE_BACK:
        return False
    recent = []
    for r in v.reviews:  # newest first
        if r.mode == "manual":
            break
        if r.mode in QUIZ_MODES and not r.practice:
            recent.append(r)
            if len(recent) == STRUGGLE_WINDOW:
                break
    return sum(r.result == "missed" for r in recent) >= STRUGGLE_MISSES


# A verse "needs work" when NEEDS_WORK of its last NEEDS_WORK_WINDOW quiz
# reviews were Shaky or Missed. Softer than struggling: no move is suggested.
NEEDS_WORK, NEEDS_WORK_WINDOW = 2, 4


def shaky_count(v: Verse) -> int:
    """Shaky or Missed among the last few quiz reviews (since the last manual move)."""
    recent = []
    for r in v.reviews:  # newest first
        if r.mode == "manual":
            break
        if r.mode in QUIZ_MODES and not r.practice:
            recent.append(r)
            if len(recent) == NEEDS_WORK_WINDOW:
                break
    return sum(r.result in ("shaky", "missed") for r in recent)


def needs_work(v: Verse) -> bool:
    return v.is_active and shaky_count(v) >= NEEDS_WORK


def also_carry(exclude: Optional[Verse]) -> Optional[tuple[Verse, int]]:
    """The one verse most worth extra practice besides the verse of the day."""
    best = None
    for v in Verse.query.filter(Verse.stage.in_(schedule.ACTIVE_STAGES)).all():
        if exclude is not None and v.id == exclude.id:
            continue
        n = shaky_count(v)
        if n >= NEEDS_WORK:
            key = (n, -schedule.ACTIVE_STAGES.index(v.stage), v.extra)
            if best is None or key > best[0]:
                best = (key, v, n)
    return (best[1], best[2]) if best else None


def choice_options(v: Verse, on: date) -> list[refs.Ref]:
    program = [x.ref for x in Verse.query.filter(Verse.id != v.id).all() if x.ref]
    seed = int(texts.sha1(f"{v.id}|{on.isoformat()}|choices")[:8], 16)
    return choices.options(v.ref, program, seed)


def hint_tokens(v: Verse, cfg: dict):
    """Partly hidden verse for the reference → text quiz. Empty means no hint
    (hints turned off, or a stage that shows none, like Mastered)."""
    if not v.text or not v.is_active or not cfg["obfuscation"].get("enabled", True):
        return []
    ratio = hints.visible_ratio(v.stage, v.done, cfg)
    if ratio <= 0.0:
        return []
    seed = hints.seed_for(v.id, v.stage, v.done, v.hint_salt)
    return hints.tokenize(v.text, ratio, seed, int(cfg["obfuscation"].get("min_word_len", 3)))
