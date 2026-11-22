from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from flask_sqlalchemy import SQLAlchemy

from scripture import refs, schedule

db = SQLAlchemy()


class Verse(db.Model):
    __tablename__ = "verses"

    # The verse's permanent ID: the same UUID the agent kept as [sid:…].
    id = db.Column(db.String(36), primary_key=True)
    reference = db.Column(db.String(120), nullable=False)          # "Alma 34:14-15"
    ref_key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    book = db.Column(db.String(60))
    chapter = db.Column(db.Integer)
    verse_start = db.Column(db.Integer)
    verse_end = db.Column(db.Integer)

    text = db.Column(db.Text)
    text_sha = db.Column(db.String(40))
    text_source = db.Column(db.String(30))      # nephi.org | bible-api.com | manual | import
    manual_override = db.Column(db.Boolean, default=False, nullable=False)
    # Daily text check: when the source's text differs from ours, it waits here
    # for you to accept or dismiss instead of silently replacing your text.
    pending_text = db.Column(db.Text)
    text_checked_at = db.Column(db.DateTime)

    stage = db.Column(db.String(10), nullable=False, default="backlog", index=True)
    done = db.Column(db.Integer, nullable=False, default=0)
    extra = db.Column(db.Integer, nullable=False, default=0)
    due_date = db.Column(db.Date, index=True)
    start_date = db.Column(db.Date)
    anchor_weekday = db.Column(db.Integer)
    anchor_day = db.Column(db.Integer)
    retry_date = db.Column(db.Date)             # practice retry after a missed quiz
    hint_salt = db.Column(db.Integer, nullable=False, default=0)

    backlog_pos = db.Column(db.Integer)
    origin = db.Column(db.String(20), default="manual")   # manual | ai | import
    created_at = db.Column(db.DateTime, default=datetime.now)

    reviews = db.relationship("Review", back_populates="verse", cascade="all, delete-orphan",
                              order_by="Review.reviewed_at.desc()")

    # ----- helpers -----
    @property
    def ref(self) -> Optional[refs.Ref]:
        return refs.parse(self.reference)

    @property
    def display_ref(self) -> str:
        r = self.ref
        return r.display if r else self.reference

    @property
    def is_active(self) -> bool:
        return self.stage in schedule.ACTIVE_STAGES

    def sched(self) -> Optional[schedule.Schedule]:
        if not self.is_active or self.due_date is None:
            return None
        return schedule.Schedule(self.stage, self.done, self.extra, self.due_date,
                                 self.anchor_weekday, self.anchor_day)

    def apply(self, s: schedule.Schedule) -> None:
        self.stage, self.done, self.extra, self.due_date = s.stage, s.done, s.extra, s.due
        self.anchor_weekday, self.anchor_day = s.anchor_weekday, s.anchor_day


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    verse_id = db.Column(db.String(36), db.ForeignKey("verses.id"), nullable=False, index=True)
    reviewed_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    slot_date = db.Column(db.Date)                 # the scheduled date this review was for
    stage = db.Column(db.String(10), nullable=False)
    position = db.Column(db.Integer)               # 0-based review number within the stage
    mode = db.Column(db.String(20))                # text_to_ref | ref_to_text | auto
    result = db.Column(db.String(10), nullable=False)   # got | shaky | missed | skipped
    counted = db.Column(db.Boolean, nullable=False, default=False)
    practice = db.Column(db.Boolean, nullable=False, default=False)
    answer = db.Column(db.Text)
    made_up = db.Column(db.Boolean, nullable=False, default=False, server_default="0")  # practice cleared an extra

    verse = db.relationship("Verse", back_populates="reviews")


class PushSubscription(db.Model):
    """A phone or browser that asked for notifications (Web Push)."""
    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    label = db.Column(db.String(120))          # e.g. "iPhone"
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_ok_at = db.Column(db.DateTime)

    def info(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class KeyValue(db.Model):
    """Small app-wide values: settings JSON, last auto-add date, etc."""
    __tablename__ = "kv"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text)


def today() -> date:
    return date.today()
