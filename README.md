# Featherstone

A private, phone-friendly app for memorizing scripture with the **Featherstone** program:

- **Backlog:** verses waiting to start
- **Daily:** 7 days, starting the day a verse begins
- **Weekly:** 4 weeks, on that same weekday
- **Monthly:** 24 months, on that same day of the month
- **Mastered:** checks at 3, 6 and 12 months, then yearly

Review dates are fixed from each verse's start date. A missed or failed review adds one extra review at the end of that stage, on the same rhythm. A verse never drops back a stage.

**→ To install it, follow [INSTALL.md](INSTALL.md).** No technical background needed.

## Features

- **Quiz in both directions:** recite a verse from its reference (with hints that fade as you progress), or name the reference from the text, either by typing it (the colon and dash fill in as you type) or by multiple choice from the same book.
- **Today view,** like a smart list, with lists for each stage and each verse's progress, history and schedule.
- **Practice** any verse any time. In Daily and Weekly, a practice "Got it" can make up a missed review.
- **Move a verse** to any stage by hand.
- **Add verses** by reference, with text fetched from nephi.org or bible-api.com, or have AI suggest one by theme (optional OpenAI key).
- **Automatic daily upkeep:** new verses from the Backlog on your schedule, a nightly check of verse text against its source, and nightly backups.
- **Phone notifications:** a morning summary and an evening nudge when verses are due.
- **Light and dark mode,** installable on the iPhone Home Screen.

## How it's built

A small Flask + SQLite web app that runs on a Mac, kept running by launchd and reached from the phone through Tailscale.

| Path | What's there |
|---|---|
| `scripture/` | Core logic with no web code: reference parsing, text fetching, hints, the fixed-date schedule, multiple choice |
| `webapp/` | The Flask app, templates, styles, the scheduled tasks (`automation.py`) and command-line tasks (`cli.py`) |
| `install.sh`, `uninstall.sh` | Mac setup |
| `tests/` | Run with `python -m pytest` |

Command-line tasks, run from the app folder:

```bash
flask --app wsgi:app tick
```

`tick` is what the scheduled job runs. Also available: `upkeep`, `push-test` and `vapid-keys`.

To develop locally:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/flask --app wsgi:app run --debug
```

## Legacy: the Apple Reminders agent

`scripture_agent.py` is the original version of this program. It ran the same cadence through Apple Reminders lists with AppleScript, on a daily launchd job. The web app replaces it; it's kept for reference.

## License

MIT. Use freely and adapt as you see fit.
