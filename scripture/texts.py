"""Fetch clean verse text.

Ported from the Reminders agent: nephi.org first (all standard works), then
bible-api.com for the Bible. Same cleanup (verse numbers, footnote letters,
non-breaking spaces) and the same paragraph-per-verse format. Keeps the
curl-style User-Agent that fixed the Cloudflare 403.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional

NEPHI_API_BASE = "https://api.nephi.org/scriptures/"
BIBLE_API_BASE = "https://bible-api.com/"
_HEADERS = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}

_LEADING_VERSE_NUM_RE = re.compile(r"^\s*(\d+[: \s]+)?(\d+)\s+")
_BRACKETED_NUM_RE = re.compile(r"^\s*\[?\d+\]?\s*")


def sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _http_get_json(url: str, timeout: float = 10.0) -> Optional[dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def clean_line(s: str) -> str:
    s = s.replace(" ", " ").strip()
    s = _LEADING_VERSE_NUM_RE.sub("", s)
    s = _BRACKETED_NUM_RE.sub("", s)
    s = re.sub(r"^[a-z]\s+", "", s)  # leading footnote letters like 'a'
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def format_paragraphs(verses: list[str]) -> str:
    cleaned = [clean_line(v) for v in verses if v]
    return "\n\n".join(c for c in cleaned if c)


def _verses_from(data: Optional[dict], field: str) -> list[str]:
    if not data:
        return []
    items = data.get(field)
    if not isinstance(items, list):
        return []
    return [v.get("text") for v in items if isinstance(v.get("text"), str) and v["text"].strip()]


def _try_nephi(reference: str) -> Optional[str]:
    ref = re.sub(r"\s+", " ", reference.strip().replace("–", "-"))
    url = f"{NEPHI_API_BASE}?{urllib.parse.urlencode({'q': ref})}"
    verses = _verses_from(_http_get_json(url), "scriptures")
    return format_paragraphs(verses) if verses else None


def _try_bible(reference: str) -> Optional[str]:
    url = f"{BIBLE_API_BASE}{urllib.parse.quote(reference.strip())}"
    verses = _verses_from(_http_get_json(url), "verses")
    return format_paragraphs(verses) if verses else None


def fetch(reference: str) -> Optional[tuple[str, str]]:
    """Return (text, source) or None. Source is "nephi.org" or "bible-api.com"."""
    ref = (reference or "").strip()
    if not ref:
        return None
    txt = _try_nephi(ref)
    if txt:
        return txt, "nephi.org"
    txt = _try_bible(ref)
    if txt:
        return txt, "bible-api.com"
    return None
