"""Command-line tasks:  flask --app wsgi:app <command>

  tick        what launchd runs every 15 minutes (upkeep, checks, notifications)
  upkeep      run today's upkeep, text check and backup right now
  push-test   send a test notification to every subscribed device
  vapid-keys  create the Web Push key pair (once, at setup)
"""
from __future__ import annotations

import base64
import os
from datetime import datetime

import click

from . import automation
from . import services as svc


def register(app) -> None:
    @app.cli.command("tick")
    def tick_cmd():
        """Scheduled tick: safe to run as often as you like."""
        for line in automation.tick():
            click.echo(f"[{datetime.now():%Y-%m-%d %H:%M}] {line}")

    @app.cli.command("upkeep")
    def upkeep_cmd():
        """Run today's upkeep, text check and backup now."""
        today = datetime.now().date()
        svc.daily_upkeep(today)
        click.echo("upkeep: done")
        click.echo("text check: " + automation.check_texts().summary())
        click.echo(f"backup: {automation.backup_db(today) or 'skipped'}")

    @app.cli.command("push-test")
    def push_test_cmd():
        """Send a test notification."""
        if not automation.vapid_ready():
            raise click.ClickException("Push keys aren't set up. Run `flask vapid-keys` first.")
        sent, failed = automation.send_push({"title": "Featherstone",
                                             "body": "Test notification — it works.", "url": "/"})
        click.echo(f"sent {sent}, failed {failed}")

    @app.cli.command("vapid-keys")
    def vapid_keys_cmd():
        """Create the Web Push key pair. Prints the line to add to .env."""
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid

        path = app.config["VAPID_PRIVATE_KEY_FILE"]
        if os.path.exists(path):
            raise click.ClickException(f"{path} already exists; delete it first to make new keys "
                                       "(every device would have to subscribe again).")
        v = Vapid()
        v.generate_keys()
        v.save_key(path)
        os.chmod(path, 0o600)
        raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        click.echo(f"Private key saved to {path}")
        click.echo(f"Add to .env:\nVAPID_PUBLIC_KEY={pub}")
