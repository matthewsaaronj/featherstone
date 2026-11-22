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

# GPT-5 mini is much better at matching a description to the right passage.
# If it fails (an outage, or a key without access), fall back to gpt-4.1-mini.
MODEL = "gpt-5-mini"
FALLBACK_MODEL = "gpt-4.1-mini"
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


def _request(model: str, prompt: str, api_key: str) -> Optional[str]:
    body = {"model": model,
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": prompt}]}
    if model.startswith("gpt-5"):
        body["reasoning_effort"] = "low"   # quicker; plenty for picking one passage
    else:
        body["temperature"] = 0.9          # some variety, so "Suggest another" gets new verses
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8"),
    )
    # Short limits: the web page waits on this, and the server gives up at 60s.
    with urllib.request.urlopen(req, timeout=20 if model.startswith("gpt-5") else 10) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


def _chat(prompt: str, api_key: Optional[str] = None) -> Optional[str]:
    api_key = _key(api_key)
    if not api_key:
        return None
    for model in (MODEL, FALLBACK_MODEL):
        try:
            out = _request(model, prompt, api_key)
            if out:
                return out
        except Exception:
            continue
    return None


def extract_reference(s: str) -> Optional[str]:
    m = _REF_RE.search((s or "").replace("–", "-"))
    if not m:
        return None
    return f"{re.sub(r'\s+', ' ', m.group(1)).strip()} {m.group(2)}:{m.group(3)}"


def suggest(topic: Optional[str], exclusions: list[str], api_key: Optional[str] = None) -> Optional[str]:
    avoid = ""
    if exclusions:
        joined = "\n".join(f"- {t}" for t in exclusions[:150])
        avoid = ("Avoid suggesting any scripture that is the same as OR overlaps any of the "
                 "following references (treat overlaps as sharing any verse in common):\n"
                 f"{joined}\n\n")
    want = ""
    if topic and topic.strip():
        want = ("The user is looking for a verse matching this. It may be a short theme "
                "or a description of the idea, story or teaching they have in mind; "
                "pick the passage that best fits it:\n"
                f"{topic.strip()}\n\n")
    prompt = (want + avoid +
              "Return exactly ONE Latter-day Saint scripture reference that is a single "
              "contiguous passage (e.g., 'Mosiah 2:21-22'), short enough to memorize: "
              "1 to 3 verses, at most 4. For a story, pick its key verse or two. Do NOT include multiple "
              "disjoint references. Do NOT include commentary—reply with only the reference.")
    return extract_reference(_chat(prompt, api_key) or "")
