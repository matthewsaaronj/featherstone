"""Scheduled work, run by launchd every 15 minutes via `flask tick`.

Each tick is cheap and idempotent; every daily task claims its day once:
  - daily upkeep (missed reviews, intake from Backlog): first tick of the day
  - text check and database backup: first tick after 3:00 AM
  - morning notification, and an evening one if verses are still due
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from flask import current_app

from scripture import texts

from . import services as svc
from .models import PushSubscription, Verse, db

MAINTENANCE_HOUR = 3


# ---------------- text check ----------------

def _normalize(s: str) -> str:
    """Compare texts ignoring trivial differences (spacing, dash styles, quotes)."""
    s = (s or "").replace("--", "—").replace("–", "—").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class TextCheck:
    checked: int = 0
    filled: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"checked {self.checked}, filled {len(self.filled)}, verified {len(self.verified)}, "
                f"changed {len(self.changed)}, unreachable {len(self.failed)}")


def check_texts(fetch: Callable[[str], Optional[tuple[str, str]]] = texts.fetch,
                pause: float = 0.3) -> TextCheck:
    """Compare every verse's text with its source.

    - Missing text is filled in.
    - Matching text is marked verified (imported text gets its real source).
    - Different text is NOT applied: it's kept as pending_text for you to
      accept or dismiss on the verse page. Your own edited text is skipped.
    """
    out = TextCheck()
    for v in Verse.query.order_by(Verse.reference).all():
        if v.manual_override:
            continue
        got = fetch(v.reference)
        out.checked += 1
        if not got:
            out.failed.append(v.reference)
            continue
        text, source = got
        if not v.text:
            svc.set_text(v, text, source)
            out.filled.append(v.reference)
        elif _normalize(text) == _normalize(v.text):
            if v.text_source in (None, "import"):
                v.text_source = source
            v.pending_text = None
            out.verified.append(v.reference)
        else:
            v.pending_text = text
            out.changed.append(v.reference)
        v.text_checked_at = datetime.now()
        db.session.commit()
        if pause:
            time.sleep(pause)
    return out


# ---------------- backups ----------------

def backup_db(on: date, keep: int = 30) -> Optional[str]:
    """Copy the SQLite database to <data dir>/backups/scripture-YYYY-MM-DD.db,
    keeping the newest `keep` copies. Uses SQLite's online backup, so it's safe
    while the app is running."""
    path = db.engine.url.database
    if not path or path == ":memory:" or not os.path.exists(path):
        return None
    folder = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(folder, f"{stem}-{on.isoformat()}.db")
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    copies = sorted(f for f in os.listdir(folder) if f.startswith(stem + "-") and f.endswith(".db"))
    for old in copies[:-keep]:
        os.remove(os.path.join(folder, old))
    return dest


# ---------------- notifications ----------------

def vapid_ready() -> bool:
    cfg = current_app.config
    return bool(cfg.get("VAPID_PUBLIC_KEY") and os.path.exists(cfg.get("VAPID_PRIVATE_KEY_FILE", "")))


def _snippet(text: str, limit: int = 160) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + "…"


def message_for(on: date, evening: bool = False) -> Optional[dict]:
    view = svc.today_view(on)
    n, late = view["n_due"], view["n_overdue"]
    verses = f"{n} verse{'s' if n != 1 else ''}"
    if evening:
        if not n:
            return None
        return {"title": "Featherstone", "body": f"{verses} still to review today.", "url": "/"}
    # Morning: what's due, then the verse you're memorizing, for the lock screen.
    m = view["memorize"]
    if not n and not (m and m.text):
        return None
    lines = [f"{verses} due today" + (f" · {late} overdue" if late else "") + "."] if n else []
    if m and m.text:
        lines.append(f"{m.display_ref}: {_snippet(m.text)}")
    return {"title": "Featherstone", "body": "\n".join(lines), "url": "/"}


def midday_message(on: date) -> Optional[dict]:
    m = svc.memorize_verse()
    if not m:
        return None
    return {"title": f"Say {m.display_ref} to yourself",
            "body": _snippet(svc.first_letters(m.text or ""), 160) or "Open Featherstone to practice.",
            "url": "/"}


def send_push(message: dict, errors: Optional[list] = None) -> tuple[int, int]:
    """Send to every subscribed device. Returns (sent, failed). Devices whose
    subscription has expired (404/410) are removed. Error reasons are appended
    to `errors` when given."""
    from pywebpush import WebPushException, webpush

    if not vapid_ready():
        return 0, 0
    cfg = current_app.config
    sent = failed = 0
    for sub in PushSubscription.query.all():
        try:
            webpush(subscription_info=sub.info(), data=json.dumps(message),
                    vapid_private_key=cfg["VAPID_PRIVATE_KEY_FILE"],
                    vapid_claims={"sub": svc.push_contact()}, ttl=12 * 3600, timeout=15)
            sub.last_ok_at = datetime.now()
            sent += 1
            current_app.logger.warning("push sent to %s", sub.label or "device")
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            body = (getattr(e.response, "text", "") or "")[:200]
            if status in (404, 410):
                db.session.delete(sub)
            failed += 1
            reason = f"push service answered {status}" + (f" ({body})" if body else "")
            current_app.logger.warning("push failed: %s", reason)
            if errors is not None:
                errors.append(reason)
        except Exception as e:  # network trouble: try again next time
            failed += 1
            current_app.logger.warning("push error: %s", e)
            if errors is not None:
                errors.append(str(e))
    db.session.commit()
    return sent, failed


def _at(cfg_time: str) -> tuple[int, int]:
    h, m = (cfg_time or "07:00").split(":")
    return int(h), int(m)


def notify_due(now: datetime) -> list[str]:
    """Send the morning and evening notifications when their time has come."""
    cfg = svc.get_settings().get("notify", {})
    if not cfg.get("enabled", True):
        return []
    on, log = now.date(), []
    morning = now.replace(hour=_at(cfg.get("morning"))[0], minute=_at(cfg.get("morning"))[1],
                          second=0, microsecond=0)
    evening = None
    if cfg.get("evening_enabled", True) and cfg.get("evening"):
        eh, em = _at(cfg["evening"])
        evening = now.replace(hour=eh, minute=em, second=0, microsecond=0)

    if now >= morning and svc._claim_day("last_push_morning", on):
        # If the Mac was asleep all day, skip straight to the evening message.
        if evening is None or now < evening:
            msg = message_for(on)
            if msg:
                log.append("morning: sent %d, failed %d" % send_push(msg))
    if cfg.get("midday_enabled") and cfg.get("midday"):
        mh, mm = _at(cfg["midday"])
        midday = now.replace(hour=mh, minute=mm, second=0, microsecond=0)
        if now >= midday and (evening is None or now < evening) and svc._claim_day("last_push_midday", on):
            msg = midday_message(on)
            if msg:
                log.append("midday: sent %d, failed %d" % send_push(msg))
    if evening is not None and now >= evening and svc._claim_day("last_push_evening", on):
        msg = message_for(on, evening=True)
        if msg:
            log.append("evening: sent %d, failed %d" % send_push(msg))
    return log


# ---------------- the tick ----------------

def tick(now: Optional[datetime] = None) -> list[str]:
    now = now or datetime.now()
    on, log = now.date(), []
    svc.daily_upkeep(on)
    if now.hour >= MAINTENANCE_HOUR and svc._claim_day("last_maintenance", on):
        log.append("text check: " + check_texts().summary())
        dest = backup_db(on)
        log.append(f"backup: {dest or 'skipped'}")
    log += notify_due(now)
    return log
