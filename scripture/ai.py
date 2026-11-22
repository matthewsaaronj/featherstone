"""Optional ChatGPT verse suggestion by theme (ported from the agent).

Needs an OpenAI API key (Settings → Connections, or OPENAI_API_KEY). Returns None on any failure so the
caller can carry on without it.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

MODEL = "gpt-4o-mini"
_SYSTEM = (
    "You are a concise assistant that only replies with a single contiguous "
    "Latter-day Saint scripture reference in the format 'Book Chapter:Verse' or "
    "'Book Chapter:Start-End'. No commentary."
)
_REF_RE = re.compile(r"([A-Za-z0-9&’' .\-]+)\s+(\d+):(\d+(?:-\d+)?)")


def _key(api_key: Optional[str]) -> str:
    return (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()


def available(api_key: Optional[str] = None) -> bool:
    return bool(_key(api_key))


def _chat(prompt: str, api_key: Optional[str] = None) -> Optional[str]:
    api_key = _key(api_key)
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps({
                "model": MODEL,
                "messages": [{"role": "system", "content": _SYSTEM},
                             {"role": "user", "content": prompt}],
                "temperature": 0.5,
            }).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    except Exception:
        return None


def extract_reference(s: str) -> Optional[str]:
    m = _REF_RE.search((s or "").replace("–", "-"))
    if not m:
        return None
    return f"{re.sub(r'\s+', ' ', m.group(1)).strip()} {m.group(2)}:{m.group(3)}"


def suggest(topic: Optional[str], exclusions: list[str], api_key: Optional[str] = None) -> Optional[str]:
    avoid = ""
    if exclusions:
        joined = "\n".join(f"- {t}" for t in exclusions[:50])
        avoid = ("Avoid suggesting any scripture that is the same as OR overlaps any of the "
                 "following references (treat overlaps as sharing any verse in common):\n"
                 f"{joined}\n\n")
    prompt = ((f"Topic: {topic.strip()}\n" if topic else "") + avoid +
              "Return exactly ONE Latter-day Saint scripture reference that is a single "
              "contiguous passage (e.g., 'Mosiah 2:21-22'). Do NOT include multiple "
              "disjoint references. Do NOT include commentary—reply with only the reference.")
    return extract_reference(_chat(prompt, api_key) or "")
