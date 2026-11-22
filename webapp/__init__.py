import os
from datetime import date

from dotenv import load_dotenv
from flask import Flask

from . import keys
from .models import db

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.expanduser("~/featherstone-data")


def _add_missing_columns() -> None:
    """Tiny migration: add columns introduced after a database was created.
    (create_all only makes missing tables, not missing columns.)"""
    from sqlalchemy import inspect, text
    wanted = {
        "reviews": {"made_up": "BOOLEAN NOT NULL DEFAULT 0"},
        "verses": {"pending_text": "TEXT", "text_checked_at": "DATETIME"},
    }
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have:
                with db.engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def create_app(test_config: dict | None = None) -> Flask:
    """Everything has a working default; .env (optional) overrides any of it.
    Personal settings (OpenAI key, notification contact) live in Settings."""
    load_dotenv(os.path.join(REPO_DIR, ".env"))
    app = Flask(__name__)
    data_dir = os.getenv("FEATHERSTONE_DATA_DIR") or os.getenv("SCRIPTURE_DATA_DIR") or DATA_DIR
    app.config.update(
        DATA_DIR=data_dir,
        SQLALCHEMY_DATABASE_URI=os.getenv(
            "DATABASE_URI", f"sqlite:///{os.path.join(data_dir, 'scripture.db')}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # The Reminders agent's config.json seeds the settings on first run.
        AGENT_CONFIG_PATH=os.getenv("AGENT_CONFIG_PATH", os.path.join(REPO_DIR, "config.json")),
        TODAY_OVERRIDE=os.getenv("TODAY_OVERRIDE"),  # YYYY-MM-DD, for testing only
        # Defaults for values you can also set in Settings → Connections.
        VAPID_SUBJECT=os.getenv("VAPID_SUBJECT", "mailto:webpush@example.com"),
    )
    if test_config:
        app.config.update(test_config)

    # Keys the app makes for itself in the data folder (see keys.py).
    data_dir = app.config["DATA_DIR"]
    os.makedirs(data_dir, exist_ok=True)
    # Browsers share cookies across ports, so two copies on one Mac (different
    # ports, same host) would read each other's quiz session. Name the cookie
    # after this copy's data folder to keep them apart.
    import hashlib
    app.config["SESSION_COOKIE_NAME"] = "featherstone_" + hashlib.sha1(
        os.path.abspath(data_dir).encode()).hexdigest()[:8]
    if not app.config.get("SECRET_KEY"):  # Flask pre-defines it as None
        app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or keys.secret_key(data_dir)
    app.config.setdefault("VAPID_PRIVATE_KEY_FILE", os.getenv("VAPID_PRIVATE_KEY_FILE")
                          or os.path.join(data_dir, "vapid_private.pem"))
    keys.ensure_vapid(app.config["VAPID_PRIVATE_KEY_FILE"])
    app.config["VAPID_PUBLIC_KEY"] = (keys.vapid_public_key(app.config["VAPID_PRIVATE_KEY_FILE"])
                                      or app.config.get("VAPID_PUBLIC_KEY", ""))

    db.init_app(app)
    with app.app_context():
        # On a fresh install the app and its scheduled job start together and
        # both create the database; if the other one got there first, carry on.
        from sqlalchemy.exc import OperationalError
        for attempt in range(3):
            try:
                db.create_all()
                _add_missing_columns()
                break
            except OperationalError as e:
                if "already exists" not in str(e) and "duplicate column" not in str(e) or attempt == 2:
                    raise
                db.session.rollback()

    from . import cli, routes
    app.register_blueprint(routes.bp)
    cli.register(app)

    # Cache-busting: a version from the newest static file's modification time,
    # added to script/stylesheet links so phones fetch changes right away.
    static_root = app.static_folder
    newest = max((os.path.getmtime(os.path.join(d, f))
                  for d, _, fs in os.walk(static_root) for f in fs), default=0)
    app.config["ASSET_VERSION"] = str(int(newest))

    @app.context_processor
    def _asset_version():
        return {"asset_version": app.config["ASSET_VERSION"]}

    @app.template_filter("nice_date")
    def nice_date(d: date | None, with_weekday: bool = False) -> str:
        if not d:
            return ""
        fmt = "%a, %b %-d" if with_weekday else "%b %-d"
        if d.year != date.today().year:
            fmt += ", %Y"
        return d.strftime(fmt)

    return app
