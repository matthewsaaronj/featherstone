"""Hide words for recall practice.

Ported from the agent's _obfuscate_text / _ratio_for_monthly_count. The
selection logic is identical (same eligible-word rule, same seeded
random.sample), but instead of returning underscores it returns tokens so
the web page can draw tidy blanks. to_underscores() gives the agent's old
plain-text form.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z’']*")


@dataclass(frozen=True)
class Token:
    text: str
    hidden: bool = False
    is_word: bool = False


def tokenize(full_text: str, visible_ratio: float, seed: int,
             min_word_len: int = 3) -> list[Token]:
    """Split text into tokens, hiding about (1 - visible_ratio) of eligible words."""
    vis = max(0.0, min(1.0, float(visible_ratio)))
    spans = []
    for m in _WORD_RE.finditer(full_text):
        letters = sum(1 for c in m.group(0) if c.isalpha())
        if letters >= min_word_len:
            spans.append((m.start(), m.end()))

    k = 0 if vis >= 0.999 or not spans else int(round((1.0 - vis) * len(spans)))
    to_hide = set(random.Random(seed).sample(range(len(spans)), k)) if k > 0 else set()

    out: list[Token] = []
    last = 0
    for idx, (s0, e0) in enumerate(spans):
        if s0 > last:
            out.append(Token(full_text[last:s0]))
        out.append(Token(full_text[s0:e0], hidden=idx in to_hide, is_word=True))
        last = e0
    if last < len(full_text):
        out.append(Token(full_text[last:]))
    return out


def to_underscores(tokens: list[Token], keep_first_last: bool = False) -> str:
    parts = []
    for t in tokens:
        if not t.hidden:
            parts.append(t.text)
        elif keep_first_last and len(t.text) >= 2:
            core = "".join("_" if c.isalpha() else c for c in t.text[1:-1])
            parts.append(t.text[0] + core + t.text[-1])
        else:
            parts.append("".join("_" if c.isalpha() else c for c in t.text))
    return "".join(parts)


def interpolate(schedule: list[float], position: int, length: int) -> float:
    """Visible ratio at review `position` (0-based) of a stage `length` reviews long.

    Same math as the agent's _ratio_for_monthly_count.
    """
    if not schedule:
        return 1.0
    if len(schedule) == 1:
        return float(schedule[0])
    denom = max(1, length - 1)
    p = max(0.0, min(1.0, float(position) / float(denom)))
    steps = len(schedule) - 1
    x = p * steps
    i = int(x)
    if i >= steps:
        return float(schedule[-1])
    a, b = float(schedule[i]), float(schedule[i + 1])
    return a * (1.0 - (x - i)) + b * (x - i)


def visible_ratio(stage: str, position: int, cfg: dict) -> float:
    """How much of the verse to show as a hint for this stage and review number."""
    cad = cfg["cadence"]
    lengths = {"daily": cad["daily_repeats"], "weekly": cad["weekly_repeats"],
               "monthly": cad["monthly_repeats"], "mastered": 1}
    schedule = cfg.get("hints", {}).get(stage)
    if schedule is None and stage == "monthly":  # older settings: the agent's schedule
        schedule = cfg["obfuscation"].get("schedule")
    if schedule is None:
        return 1.0
    return interpolate(schedule, position, int(lengths.get(stage, 1)))


def seed_for(verse_id: str, stage: str, position: int, salt: int = 0) -> int:
    """Stable seed: the same verse/review shows the same blanks on every load."""
    h = hashlib.sha1(f"{verse_id}|{stage}|{position}|{salt}".encode("utf-8")).hexdigest()[:8]
    return int(h, 16)
