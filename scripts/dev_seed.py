"""DEV ONLY: fill a throwaway database from the agent's state.json and the live
Reminders due dates, so the app has real verses to show while it's being built.

This is not the real import. It writes only to ~/scripture-data/dev.db (wiped
each run), reads state.json and Reminders without changing them, and guesses
start dates. The real import (with a review report) comes later.

    DATABASE_URI=sqlite:///$HOME/scripture-data/dev.db python scripts/dev_seed.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

DEV_DB = os.path.expanduser("~/scripture-data/dev.db")
os.environ["DATABASE_URI"] = f"sqlite:///{DEV_DB}"

from scripture import refs, schedule  # noqa: E402
from webapp import create_app  # noqa: E402
from webapp.models import Verse, db  # noqa: E402


def reminders_due_dates() -> dict[str, date]:
    """Read-only: due date per verse key from the Featherstone lists."""
    try:
        import scripture_agent as agent
        agent.apply_config(agent.load_or_init_config())
        out = {}
        for ln in (agent.DAILY, agent.WEEKLY, agent.MONTHLY, agent.MASTERED):
            for it in agent.list_reminders(ln):
                m = re.search(r"(\w+ \d{1,2}, \d{4})", it.get("due") or "")
                if m:
                    out[refs.key_for(it["name"])] = datetime.strptime(m.group(1), "%B %d, %Y").date()
        return out
    except Exception as e:  # Reminders not reachable: fall back to guesses
        print(f"(Reminders not read: {e})")
        return {}


def main() -> None:
    app = create_app()
    with app.app_context():
        # Clear tables in place (deleting the file would strand a running dev server
        # on the old, deleted copy).
        db.drop_all()
        db.create_all()
    today = date.today()
    with open(os.path.join(REPO, "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    dues = reminders_due_dates()

    with app.app_context():
        for key, rec in state["verses"].items():
            r = refs.parse(rec.get("title", key))
            if not r:
                continue
            stage = rec.get("stage", "backlog")
            v = Verse(id=rec.get("sid") or __import__("uuid").uuid4().__str__(), reference=str(r),
                      ref_key=r.key, book=r.book, chapter=r.chapter, verse_start=r.start,
                      verse_end=r.end, text=rec.get("full_text"), text_sha=rec.get("full_text_sha"),
                      text_source="import", stage=stage, origin="import", hint_salt=0)
            due = dues.get(key)
            if stage == "daily" or (stage == "backlog" and due):
                c = int(rec.get("daily_count", 0))
                start = today - timedelta(days=c)
                v.apply(schedule.Schedule("daily", c, 0, due or today, start.weekday(), start.day))
                v.start_date = start
            elif stage == "weekly":
                wd = int(rec.get("anchor_weekday", today.weekday()))
                d = due or today + timedelta(days=(wd - today.weekday()) % 7)
                v.apply(schedule.Schedule("weekly", int(rec.get("weekly_count", 0)), 0, d, wd, d.day))
            elif stage in ("monthly", "mastered"):
                wd = int(rec.get("anchor_weekday", today.weekday()))
                d = due or today
                cnt = int(rec.get("monthly_count" if stage == "monthly" else "mastered_count", 0))
                v.apply(schedule.Schedule(stage, cnt, 0, d, wd, d.day))
            else:
                v.stage, v.backlog_pos = "backlog", 1
            db.session.add(v)
        db.session.commit()
        print(f"Dev DB: {DEV_DB}  ({Verse.query.count()} verses)")


if __name__ == "__main__":
    main()
