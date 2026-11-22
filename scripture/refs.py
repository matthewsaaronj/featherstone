"""Scripture references: parse, normalize, format, compare.

Ported from the Reminders agent (parse_reference / _normalize_book_name /
ranges_overlap). The parser accepts the same shapes ("Book C:V" and
"Book C:V-V" with hyphen or dash). Book-name normalization now covers common
abbreviations, so quiz answers like "D&C 121:34–36" or "Hel 14:17-18" match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Canonical book names, in standard-works order.
BOOKS: list[str] = [
    # Old Testament
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges",
    "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles",
    "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
    "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations",
    "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah",
    "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    # New Testament
    "Matthew", "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians",
    "2 Corinthians", "Galatians", "Ephesians", "Philippians", "Colossians",
    "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
    "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John",
    "3 John", "Jude", "Revelation",
    # Book of Mormon
    "1 Nephi", "2 Nephi", "Jacob", "Enos", "Jarom", "Omni", "Words of Mormon",
    "Mosiah", "Alma", "Helaman", "3 Nephi", "4 Nephi", "Mormon", "Ether", "Moroni",
    # Doctrine and Covenants, Pearl of Great Price
    "Doctrine and Covenants", "Moses", "Abraham", "Joseph Smith—Matthew",
    "Joseph Smith—History", "Articles of Faith",
]

# Extra spellings → canonical name. Keys are compared after _squash().
_EXTRA_ALIASES: dict[str, str] = {
    "gen": "Genesis", "ex": "Exodus", "exod": "Exodus", "lev": "Leviticus",
    "num": "Numbers", "deut": "Deuteronomy", "josh": "Joshua", "judg": "Judges",
    "1 sam": "1 Samuel", "2 sam": "2 Samuel", "1 kgs": "1 Kings", "2 kgs": "2 Kings",
    "1 chr": "1 Chronicles", "2 chr": "2 Chronicles", "neh": "Nehemiah",
    "esth": "Esther", "ps": "Psalms", "psa": "Psalms", "psalm": "Psalms",
    "prov": "Proverbs", "eccl": "Ecclesiastes", "song": "Song of Solomon",
    "isa": "Isaiah", "jer": "Jeremiah", "lam": "Lamentations", "ezek": "Ezekiel",
    "dan": "Daniel", "hosea": "Hosea", "obad": "Obadiah", "mic": "Micah",
    "hab": "Habakkuk", "zeph": "Zephaniah", "hag": "Haggai", "zech": "Zechariah",
    "mal": "Malachi",
    "matt": "Matthew", "mt": "Matthew", "mk": "Mark", "lk": "Luke", "jn": "John",
    "rom": "Romans", "1 cor": "1 Corinthians", "2 cor": "2 Corinthians",
    "gal": "Galatians", "eph": "Ephesians", "philip": "Philippians", "phil": "Philippians",
    "col": "Colossians", "1 thes": "1 Thessalonians", "2 thes": "2 Thessalonians",
    "1 thess": "1 Thessalonians", "2 thess": "2 Thessalonians",
    "1 tim": "1 Timothy", "2 tim": "2 Timothy", "philem": "Philemon",
    "heb": "Hebrews", "jas": "James", "1 pet": "1 Peter", "2 pet": "2 Peter",
    "1 jn": "1 John", "2 jn": "2 John", "3 jn": "3 John", "rev": "Revelation",
    "revelations": "Revelation",
    "1 ne": "1 Nephi", "2 ne": "2 Nephi", "3 ne": "3 Nephi", "4 ne": "4 Nephi",
    "jac": "Jacob", "jar": "Jarom", "w of m": "Words of Mormon", "wom": "Words of Mormon",
    "mos": "Mosiah", "hel": "Helaman", "morm": "Mormon", "eth": "Ether", "moro": "Moroni",
    "d&c": "Doctrine and Covenants", "d & c": "Doctrine and Covenants",
    "d. & c.": "Doctrine and Covenants", "dc": "Doctrine and Covenants",
    "d and c": "Doctrine and Covenants", "doctrine & covenants": "Doctrine and Covenants",
    "abr": "Abraham", "js-m": "Joseph Smith—Matthew", "js m": "Joseph Smith—Matthew",
    "joseph smith-matthew": "Joseph Smith—Matthew", "js-h": "Joseph Smith—History",
    "js h": "Joseph Smith—History", "joseph smith-history": "Joseph Smith—History",
    "a of f": "Articles of Faith",
}


def _squash(s: str) -> str:
    s = (s or "").casefold().replace("–", "-").replace("—", "-")
    s = s.replace(".", " ").replace("’", "'")
    s = re.sub(r"\s*&\s*", " & ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    # "i nephi" / "first nephi" style prefixes
    s = re.sub(r"^(first|i)\s+", "1 ", s)
    s = re.sub(r"^(second|ii)\s+", "2 ", s)
    s = re.sub(r"^(third|iii)\s+", "3 ", s)
    s = re.sub(r"^(fourth|iv)\s+", "4 ", s)
    s = re.sub(r"^(\d)(?=[a-z])", r"\1 ", s)  # "1ne" → "1 ne"
    return s


_ALIASES: dict[str, str] = {_squash(b): b for b in BOOKS}
_ALIASES.update({_squash(k): v for k, v in _EXTRA_ALIASES.items()})


def canonical_book(book: str) -> str:
    """Canonical book name, or the cleaned input when the book is unknown."""
    key = _squash(book)
    if key in _ALIASES:
        return _ALIASES[key]
    return re.sub(r"\s{2,}", " ", (book or "").strip())


@dataclass(frozen=True)
class Ref:
    book: str
    chapter: int
    start: int
    end: int

    def __str__(self) -> str:  # storage form, e.g. "Alma 34:14-15"
        return format_ref(self)

    @property
    def key(self) -> str:
        return ref_key(self)

    @property
    def display(self) -> str:  # typographic form, e.g. "Alma 34:14–15"
        return format_ref(self, dash="–")


# Chapter and verse may be separated by ":" , "." or just spaces, and a range by
# "-" or spaces, so "Alma 34:14-15", "Alma 34 14-15" and "Alma 34 14 15" all parse.
_REF_PARSE = re.compile(
    r"^\s*([A-Za-z0-9&’'. \-—]+?)\s*(\d+)\s*(?:[:.]\s*|\s+)(\d+)(?:\s*(?:-|\s)\s*(\d+))?\s*$",
    re.IGNORECASE,
)


def _match(ref: str):
    text = (ref or "").replace("–", "-").replace("—", "-")
    text = re.sub(r"[\s:.\-]+$", "", text)  # dangling ":" or "-" from as-you-type formatting
    # Keep the dash in "Joseph Smith—History" from being read as a range:
    text = re.sub(r"(?i)joseph smith\s*-\s*", "Joseph Smith-", text)
    m = _REF_PARSE.match(text)
    if not m:
        return None
    raw_book = m.group(1).strip().rstrip(".")
    if not raw_book or raw_book.isdigit():
        return None
    return canonical_book(raw_book), int(m.group(2)), m.group(3), m.group(4)


def parse(ref: str) -> Optional[Ref]:
    """Parse a reference: "Book C:V", "Book C:V-V", or the same with spaces
    instead of ":" and "-". Returns None when it doesn't look like one."""
    m = _match(ref)
    if not m:
        return None
    book, chapter, start_s, end_s = m
    start = int(start_s)
    end = int(end_s) if end_s else start
    if end < start:
        end = start
    return Ref(book, chapter, start, end)


def candidates(ref: str) -> list[Ref]:
    """Every sensible reading of a typed reference.

    Besides the plain reading, a run of digits typed without a separator may be
    a range: "Alma 34 56" could be 34:56 or 34:5-6, and "Alma 34 1415" could be
    34:1415 or 34:14-15. Used when grading, where the expected answer decides.
    """
    m = _match(ref)
    if not m:
        return []
    book, chapter, start_s, end_s = m
    plain = parse(ref)
    out = [plain] if plain else []
    if end_s is None and len(start_s) >= 2:
        for i in range(1, len(start_s)):
            a, b = start_s[:i], start_s[i:]
            if b.startswith("0"):
                continue
            if 1 <= int(a) < int(b):
                out.append(Ref(book, chapter, int(a), int(b)))
    return out


def format_ref(r: Ref, dash: str = "-") -> str:
    verses = f"{r.start}" if r.start == r.end else f"{r.start}{dash}{r.end}"
    return f"{r.book} {r.chapter}:{verses}"


def ref_key(r: Ref) -> str:
    """Stable lowercase key, matching the agent's state.json keys ("alma 34:14-15")."""
    return format_ref(r).casefold()


def key_for(ref: str) -> str:
    """Key for a reference string; falls back to the agent's title normalization."""
    r = parse(ref)
    if r:
        return ref_key(r)
    return (ref or "").strip().casefold().replace("–", "-")


def overlaps(a: Optional[Ref], b: Optional[Ref]) -> bool:
    if a is None or b is None:
        return False
    if a.book.casefold() != b.book.casefold() or a.chapter != b.chapter:
        return False
    return not (a.end < b.start or b.end < a.start)


_GRADE_RANK = {"exact": 3, "overlap": 2, "chapter": 1, "wrong": 0}


def _grade_one(got: Ref, expected: Ref) -> str:
    if (got.book, got.chapter, got.start, got.end) == (
        expected.book, expected.chapter, expected.start, expected.end
    ):
        return "exact"
    if overlaps(got, expected):
        return "overlap"
    if got.book == expected.book and got.chapter == expected.chapter:
        return "chapter"
    return "wrong"


def grade(answer: str, expected: Ref) -> tuple[str, Optional[Ref]]:
    """Grade a typed reference, trying every reading of it (see candidates()).

    Returns (grade, the reading that scored best). Grades: "exact", "overlap"
    (right passage, different verses), "chapter" (right book and chapter), "wrong".
    """
    best, best_ref = "wrong", None
    for got in candidates(answer):
        g = _grade_one(got, expected)
        if best_ref is None or _GRADE_RANK[g] > _GRADE_RANK[best]:
            best, best_ref = g, got
    return best, best_ref


def compare(answer: str, expected: Ref) -> str:
    return grade(answer, expected)[0]
