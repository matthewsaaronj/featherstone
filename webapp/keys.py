"""Keys the app makes for itself on first run, kept in the data folder.

- secret_key: signs the browser session (quiz progress). Not a password.
- vapid_private.pem: identifies this server to phone push services. The phone
  is given the matching public key when it turns notifications on.

Making these automatically means a fresh copy of the app needs no setup
beyond starting it. Delete a file to make a new one (for the push key, every
device then has to turn notifications on again).
"""
from __future__ import annotations

import base64
import os
import secrets
from typing import Optional


def secret_key(data_dir: str) -> str:
    path = os.path.join(data_dir, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


def ensure_vapid(path: str) -> None:
    """Create the push key pair if it doesn't exist yet."""
    if os.path.exists(path):
        return
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    v.save_key(path)
    os.chmod(path, 0o600)


def vapid_public_key(path: str) -> Optional[str]:
    """The public key phones need (base64url, uncompressed point), read from the PEM."""
    try:
        from cryptography.hazmat.primitives import serialization

        with open(path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    except Exception:
        return None
