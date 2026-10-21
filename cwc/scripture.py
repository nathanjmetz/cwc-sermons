"""Find and normalize Bible references in free text and sermon transcripts.

Handles written forms ("Ezek 4:9", "1 Cor. 13:4-7") and the way preachers say
them out loud, as they show up in auto-captions ("Ezekiel chapter four verse
nine", "first Corinthians thirteen", "turn to verse 12").
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical name -> aliases (lowercase). Canonical names are also aliases.
BOOKS = {
    "Genesis": ["gen", "gn"],
    "Exodus": ["exod", "exo", "ex"],
    "Leviticus": ["lev", "lv"],
    "Numbers": ["num", "nm"],
    "Deuteronomy": ["deut", "dt"],
    "Joshua": ["josh"],
    "Judges": ["judg"],
    "Ruth": [],
    "1 Samuel": ["1 sam"],
    "2 Samuel": ["2 sam"],
    "1 Kings": ["1 kgs"],
    "2 Kings": ["2 kgs"],
    "1 Chronicles": ["1 chron", "1 chr"],
    "2 Chronicles": ["2 chron", "2 chr"],
    "Ezra": [],
    "Nehemiah": ["neh"],
    "Esther": ["esth"],
    "Job": [],
    "Psalms": ["psalm", "ps", "psa", "pss"],
    "Proverbs": ["prov", "prv"],
    "Ecclesiastes": ["eccl", "eccles", "qoheleth"],
    "Song of Songs": ["song of solomon", "song of sol", "songs"],
    "Isaiah": ["isa"],
    "Jeremiah": ["jer"],
    "Lamentations": ["lam"],
    "Ezekiel": ["ezek", "eze", "ezekial"],
    "Daniel": ["dan"],
    "Hosea": ["hos"],
    "Joel": [],
    "Amos": [],
    "Obadiah": ["obad"],
    "Jonah": ["jon"],
    "Micah": ["mic"],
    "Nahum": ["nah"],
    "Habakkuk": ["hab", "habakuk"],
    "Zephaniah": ["zeph"],
    "Haggai": ["hag"],
    "Zechariah": ["zech"],
    "Malachi": ["mal"],
    "Matthew": ["matt", "mt"],
    "Mark": ["mk"],
    "Luke": ["lk"],
    "John": ["jn"],
    "Acts": ["acts of the apostles"],
    "Romans": ["rom"],
    "1 Corinthians": ["1 cor"],
    "2 Corinthians": ["2 cor"],
    "Galatians": ["gal"],
    "Ephesians": ["eph"],
    "Philippians": ["phil"],
    "Colossians": ["col"],
    "1 Thessalonians": ["1 thess", "1 thes"],
    "2 Thessalonians": ["2 thess", "2 thes"],
    "1 Timothy": ["1 tim"],
    "2 Timothy": ["2 tim"],
    "Titus": [],
    "Philemon": ["philem"],
    "Hebrews": ["heb"],
    "James": ["jas"],
    "1 Peter": ["1 pet"],
    "2 Peter": ["2 pet"],
    "1 John": ["1 jn"],
    "2 John": ["2 jn"],
    "3 John": ["3 jn"],
    "Jude": [],
    "Revelation": ["rev", "revelations"],
}

# Names that are also everyday words or first names. In transcripts these only
# count when a chapter number follows ("Mark 4", "John 3:16"), never alone.
AMBIGUOUS = {"Job", "Mark", "John", "James", "Acts", "Numbers", "Judges", "Ruth",
             "Amos", "Joel", "Titus", "Jude", "Daniel", "Jonah", "Esther", "Ezra",
             "Songs", "Lamentations", "Romans", "Hebrews", "1 John", "2 John", "3 John"}

ORDINAL_PREFIX = {
    "1": r"(?:1|i|1st|first)", "2": r"(?:2|ii|2nd|second)", "3": r"(?:3|iii|3rd|third)",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
NUMBER_WORDS = {w: i for i, w in enumerate(_ONES)}
NUMBER_WORDS.update(_TENS)

# A number written as digits or spoken ("twenty-three", "a hundred and nineteen").
_ones_re = "|".join(sorted(_ONES[1:], key=len, reverse=True))
_tens_re = "|".join(_TENS)
NUM = (rf"(?:\d{{1,3}}|(?:(?:one|a)\s+hundred(?:\s+and)?\s+)?(?:(?:{_tens_re})(?:[\s-](?:{_ones_re[:]}))?|{_ones_re})"
       rf"|(?:one|a)\s+hundred)")


def words_to_int(s):
    s = s.lower().replace("-", " ").strip()
    if s.isdigit():
        return int(s)
    total = 0
    for w in s.split():
        if w in ("and", "a"):
            continue
        if w == "hundred":
            total = (total or 1) * 100
        elif w in NUMBER_WORDS:
            total += NUMBER_WORDS[w]
        else:
            return None
    return total or None


def _alias_pattern(name):
    """Regex for one alias: '1 cor' -> '(?:1|i|1st|first)\\s*cor\\.?'."""
    m = re.match(r"^([123]) (.+)$", name)
    if m:
        return ORDINAL_PREFIX[m.group(1)] + r"\s*" + re.escape(m.group(2)).replace(r"\ ", r"\s+") + r"\.?"
    return re.escape(name).replace(r"\ ", r"\s+") + r"\.?"


_alias_to_book = {}
for _book, _aliases in BOOKS.items():
    for _a in [_book.lower()] + _aliases:
        _alias_to_book[_a] = _book
_ALIASES = sorted(_alias_to_book, key=len, reverse=True)
BOOK_RE = "(?:" + "|".join(_alias_pattern(a) for a in _ALIASES) + ")"

SEP_CV = r"(?:\s*:\s*|\.(?=\d)|,?\s+verses?\s+|,?\s+vs?\.?\s+)"
RANGE = r"(?:\s*(?:-|–|—|through|thru|to|and)\s*)"

REF_RE = re.compile(
    rf"\b(?P<book>{BOOK_RE})\s*(?P<chkw>,?\s*chapter\s+)?(?P<ch>{NUM})\b"
    rf"(?:{SEP_CV}(?P<v1>{NUM})\b(?:{RANGE}(?P<v2>{NUM})\b)?)?",
    re.IGNORECASE,
)
# "verse 9", "verses 9 through 12" with no book: resolved against recent context.
VERSE_ONLY_RE = re.compile(rf"\bverses?\s+(?P<v1>{NUM})\b(?:{RANGE}(?P<v2>{NUM})\b)?", re.IGNORECASE)
BOOK_ONLY_RE = re.compile(rf"\b(?P<book>{BOOK_RE})\b", re.IGNORECASE)


def normalize_book(raw):
    s = re.sub(r"\s+", " ", raw.lower().rstrip(".")).strip()
    m = re.match(r"^(1|i|1st|first|2|ii|2nd|second|3|iii|3rd|third)\s*(.+)$", s)
    if m and s not in _alias_to_book:
        n = {"1": "1", "i": "1", "1st": "1", "first": "1", "2": "2", "ii": "2", "2nd": "2",
             "second": "2", "3": "3", "iii": "3", "3rd": "3", "third": "3"}[m.group(1)]
        s = f"{n} {m.group(2)}"
    return _alias_to_book.get(s)


@dataclass
class Ref:
    book: str
    chapter: int | None = None
    verse_start: int | None = None
    verse_end: int | None = None
    start: int = 0   # char offset in the source text
    end: int = 0

    def label(self):
        s = self.book
        if self.chapter:
            s += f" {self.chapter}"
            if self.verse_start:
                s += f":{self.verse_start}"
                if self.verse_end and self.verse_end != self.verse_start:
                    s += f"-{self.verse_end}"
        return s


def _valid(ref, raw_book):
    if ref.chapter is None or ref.chapter > 150 or ref.chapter < 1:
        return False
    if ref.verse_start is not None and not (1 <= ref.verse_start <= 176):
        return False
    if ref.verse_end is not None and ref.verse_start is not None and ref.verse_end < ref.verse_start:
        ref.verse_end = None
    # A lowercase single-letter-ish alias ("ex", "mt") in prose is too noisy.
    if len(raw_book.strip(".")) <= 2 and not raw_book[0].isupper():
        return False
    return True


def find_refs(text, strict=False):
    """All chapter-or-verse references in text. strict=True also returns
    book-only mentions for unambiguous books (used for titles/descriptions)."""
    out = []
    taken = []
    for m in REF_RE.finditer(text):
        book = normalize_book(m.group("book"))
        if not book:
            continue
        ch = words_to_int(m.group("ch"))
        v1 = words_to_int(m.group("v1")) if m.group("v1") else None
        v2 = words_to_int(m.group("v2")) if m.group("v2") else None
        ref = Ref(book, ch, v1, v2 or v1, m.start(), m.end())
        # "job one", "mark two things": spoken numbers after an everyday word
        # only count with "chapter" or a verse attached.
        if (book in AMBIGUOUS and not m.group("ch").isdigit()
                and not m.group("chkw") and v1 is None):
            continue
        if _valid(ref, m.group("book")):
            out.append(ref)
            taken.append((m.start(), m.end()))
    if strict:
        for m in BOOK_ONLY_RE.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            book = normalize_book(m.group("book"))
            if book and book not in AMBIGUOUS and len(m.group("book")) > 3:
                out.append(Ref(book, None, None, None, m.start(), m.end()))
    out.sort(key=lambda r: r.start)
    return out


def parse_query_ref(q):
    """If the search query is (mostly) a scripture reference, return it.
    Accepts book-only queries too ("Ezekiel", "Song of Songs")."""
    refs = find_refs(q)
    if refs:
        return refs[0]
    m = BOOK_ONLY_RE.fullmatch(q.strip())
    if m:
        book = normalize_book(m.group("book"))
        if book:
            return Ref(book)
    return None


def find_verse_only(text):
    for m in VERSE_ONLY_RE.finditer(text):
        v1 = words_to_int(m.group("v1"))
        v2 = words_to_int(m.group("v2")) if m.group("v2") else None
        if v1 and 1 <= v1 <= 176:
            yield m.start(), v1, (v2 if v2 and v2 >= v1 else v1)
