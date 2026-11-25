"""What each two-step review shows.

Step 1, "Which verse is this?": a clue that shrinks as a verse advances, from
the whole verse to one sentence to a stretch of at least 10 words, picked
fresh each day.

Step 2, "Now say the whole verse": the help on offer rotates (full text, first
letters, words with blanks, or nothing) and thins out as a verse advances.

Pure functions: the caller passes a seed (stable per verse and day) so a page
looks the same on reload.
"""
from __future__ import annotations

import random
import re

CLUE_KINDS = ("full", "sentence", "phrase")
SUPPORT_KINDS = ("full", "letters", "blanks", "none")

# A sentence ends at . ; : ? ! (a closing quote or bracket may follow).
_SENTENCE_END = re.compile(r"(?<=[.;:?!])[’”'\")\]]*\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:.?!])[’”'\")\]]*\s+")
_TRAILING = ",;:.?!—–-"


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _words(s: str) -> list[str]:
    return s.split()


def clue_kind(stage: str, position: int, seed: int) -> str:
    """position = good reviews so far in this stage (0 = first)."""
    if stage == "daily":
        return "full" if position < 2 else "sentence" if position < 5 else "phrase"
    if stage == "weekly":
        return "sentence" if position == 0 or random.Random(seed).random() < 0.4 else "phrase"
    return "phrase"


def _window(words: list[str], lo: int, hi: int, rnd: random.Random) -> list[str]:
    size = min(len(words), rnd.randint(lo, hi))
    start = rnd.randint(0, len(words) - size)
    return words[start:start + size]


MIN_WORDS = 10   # a clue shorter than a full sentence is at least this long


def _clause_runs(flat: str, lo: int, hi: int) -> list[str]:
    """Runs of whole clauses, each starting at a clause start, lo to hi words."""
    clauses = [c for c in _CLAUSE_END.split(flat) if c]
    runs = []
    for i in range(len(clauses)):
        n, j = 0, i
        while j < len(clauses) and n < lo:
            n += len(_words(clauses[j]))
            j += 1
        if lo <= n <= hi:
            runs.append(clauses[i:j])
    # Prefer runs that don't trail off into a stub ("And now…") or start with
    # one: both end clauses at least 4 words.
    full = [r for r in runs if len(_words(r[-1])) >= 4 and len(_words(r[0])) >= 3]
    return [" ".join(r) for r in (full or runs)]


def sentence(text: str, seed: int) -> str:
    """One full sentence from the verse (or, in one long sentence, a run of
    whole clauses at least MIN_WORDS long)."""
    rnd = random.Random(seed)
    flat = _flat(text)
    words = _words(flat)
    if len(words) <= 14:
        return flat
    parts = [p for p in _SENTENCE_END.split(flat) if len(_words(p)) >= 7]
    good = [p for p in parts if len(_words(p)) <= 24]
    if len(parts) > 1 and good:
        return rnd.choice(good).rstrip(",;:—– ")
    runs = _clause_runs(flat, MIN_WORDS, 18)
    if runs:
        return rnd.choice(runs).rstrip(",;:—– ")
    return " ".join(_window(words, MIN_WORDS, 16, rnd))


def phrase(text: str, seed: int) -> str:
    """The hardest clue: a stretch of whole clauses, MIN_WORDS to 16 words,
    from anywhere in the verse (it may cross a sentence break)."""
    rnd = random.Random(seed)
    flat = _flat(text)
    words = _words(flat)
    if len(words) <= MIN_WORDS + 2:
        return flat.strip(_TRAILING + " ")
    runs = _clause_runs(flat, MIN_WORDS, 16)
    picked = rnd.choice(runs) if runs else " ".join(_window(words, MIN_WORDS, 14, rnd))
    # Keep a closing . ? ! so a clue that ends a sentence doesn't get "…".
    return picked.strip(" ").rstrip(",;:—–- ").lstrip(_TRAILING + " ")


def clue(text: str, kind: str, seed: int) -> str:
    if kind == "sentence":
        return sentence(text, seed)
    if kind == "phrase":
        return phrase(text, seed)
    return text


# Help for step 2, as weights per stage band.
_SUPPORT_WEIGHTS = {
    "daily-early": {"letters": 0.5, "blanks": 0.5},
    "daily-late": {"letters": 0.35, "blanks": 0.45, "none": 0.2},
    "weekly": {"letters": 0.3, "blanks": 0.4, "none": 0.3},
    "monthly": {"letters": 0.2, "blanks": 0.3, "none": 0.5},
}


def support_kind(stage: str, position: int, seed: int) -> str:
    if stage == "mastered":
        return "none"
    if stage == "daily":
        if position == 0:
            return "full"
        band = "daily-early" if position < 4 else "daily-late"
    else:
        band = stage if stage in _SUPPORT_WEIGHTS else "monthly"
    weights = _SUPPORT_WEIGHTS[band]
    roll, total = random.Random(seed).random(), 0.0
    for kind, w in weights.items():
        total += w
        if roll < total:
            return kind
    return list(weights)[-1]


def combine(ref_grade: str, recite: str) -> str:
    """One result for the whole review. ref_grade is the reference check
    (exact | overlap | chapter | wrong); recite is the self-grade."""
    if recite == "missed":
        return "missed"
    if ref_grade == "exact" and recite == "got":
        return "got"
    return "shaky"


def display(text: str, part: str) -> str:
    """The clue with "…" where it cuts into the verse (not at its start or end,
    and not after a full stop)."""
    flat, part = _flat(text), _flat(part)
    lead = "" if flat.startswith(part) else "…"
    tail = "" if flat.endswith(part) or part[-1:] in ".?!" else "…"
    return f"{lead}{part}{tail}"
