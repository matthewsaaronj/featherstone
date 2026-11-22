"""Multiple-choice options for the "Which verse is this?" quiz.

All options come from the same book as the answer, so you have to recall the
chapter and verses, not just the book. Distractors are, in order of preference:

  1. other verses you're memorizing from that book (the most confusable)
  2. nearby passages: same verses in a nearby chapter, or the same chapter with
     the verses shifted

Every generated option spans the same number of verses as the answer, so length
gives nothing away, and none overlaps the answer or each other.
"""
from __future__ import annotations

import random
from typing import Iterable

from .refs import Ref, overlaps

# Chapters per book (standard works).
CHAPTERS: dict[str, int] = {
    "Genesis": 50, "Exodus": 40, "Leviticus": 27, "Numbers": 36, "Deuteronomy": 34,
    "Joshua": 24, "Judges": 21, "Ruth": 4, "1 Samuel": 31, "2 Samuel": 24,
    "1 Kings": 22, "2 Kings": 25, "1 Chronicles": 29, "2 Chronicles": 36, "Ezra": 10,
    "Nehemiah": 13, "Esther": 10, "Job": 42, "Psalms": 150, "Proverbs": 31,
    "Ecclesiastes": 12, "Song of Solomon": 8, "Isaiah": 66, "Jeremiah": 52,
    "Lamentations": 5, "Ezekiel": 48, "Daniel": 12, "Hosea": 14, "Joel": 3, "Amos": 9,
    "Obadiah": 1, "Jonah": 4, "Micah": 7, "Nahum": 3, "Habakkuk": 3, "Zephaniah": 3,
    "Haggai": 2, "Zechariah": 14, "Malachi": 4,
    "Matthew": 28, "Mark": 16, "Luke": 24, "John": 21, "Acts": 28, "Romans": 16,
    "1 Corinthians": 16, "2 Corinthians": 13, "Galatians": 6, "Ephesians": 6,
    "Philippians": 4, "Colossians": 4, "1 Thessalonians": 5, "2 Thessalonians": 3,
    "1 Timothy": 6, "2 Timothy": 4, "Titus": 3, "Philemon": 1, "Hebrews": 13,
    "James": 5, "1 Peter": 5, "2 Peter": 3, "1 John": 5, "2 John": 1, "3 John": 1,
    "Jude": 1, "Revelation": 22,
    "1 Nephi": 22, "2 Nephi": 33, "Jacob": 7, "Enos": 1, "Jarom": 1, "Omni": 1,
    "Words of Mormon": 1, "Mosiah": 29, "Alma": 63, "Helaman": 16, "3 Nephi": 30,
    "4 Nephi": 1, "Mormon": 9, "Ether": 15, "Moroni": 10,
    "Doctrine and Covenants": 138, "Moses": 8, "Abraham": 5,
    "Joseph Smith—Matthew": 1, "Joseph Smith—History": 1, "Articles of Faith": 1,
}

# Keep invented verse numbers modest so they almost certainly exist.
_MAX_VERSE = 30


def _nearby(answer: Ref, rnd: random.Random) -> list[Ref]:
    span = answer.end - answer.start
    max_ch = CHAPTERS.get(answer.book, answer.chapter + 6)
    out: list[Ref] = []
    # Same verses, nearby chapters (feels right, wrong chapter).
    offsets = [d for d in range(-8, 9) if d and 1 <= answer.chapter + d <= max_ch]
    rnd.shuffle(offsets)
    for d in offsets[:6]:
        out.append(Ref(answer.book, answer.chapter + d, answer.start, answer.end))
    # Same chapter, verses shifted clear of the answer (feels right, wrong verses).
    # Prefer earlier verses: they always exist, while later ones might run past
    # the end of a short chapter.
    for _ in range(6):
        gap = rnd.randint(1, 6)
        if answer.start - gap - span >= 1:
            start = answer.start - gap - span
        else:
            start = answer.end + rnd.randint(1, 3)
        if start + span <= max(_MAX_VERSE, answer.end + 8):
            out.append(Ref(answer.book, answer.chapter, start, start + span))
    # Different chapter and verses.
    for _ in range(4):
        ch = rnd.randint(1, max_ch)
        start = rnd.randint(1, max(1, min(_MAX_VERSE, answer.end + 8) - span))
        out.append(Ref(answer.book, ch, start, start + span))
    rnd.shuffle(out)
    return out


def options(answer: Ref, program: Iterable[Ref], seed: int, n: int = 4) -> list[Ref]:
    """Return n options (answer included), shuffled with a stable seed."""
    rnd = random.Random(seed)
    picked: list[Ref] = [answer]

    def ok(r: Ref) -> bool:
        return r.book == answer.book and all(not overlaps(r, p) for p in picked)

    same_book = [r for r in program if r.book == answer.book]
    rnd.shuffle(same_book)
    for r in same_book:
        if len(picked) >= min(n, 3):  # leave room for at least one generated option
            break
        if ok(r):
            picked.append(r)
    for r in _nearby(answer, rnd):
        if len(picked) >= n:
            break
        if ok(r):
            picked.append(r)
    rnd.shuffle(picked)
    return picked
