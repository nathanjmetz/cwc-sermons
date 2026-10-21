"""Build data/sermons.db from the raw files in data/raw/.

    python -m cwc.index

Every word in a caption track carries its own timestamp, so a hit on
"Ezekiel 4:9" or "forgiveness" maps back to the second it was said.
"""
from __future__ import annotations

import bisect
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from .scripture import find_refs, find_verse_only

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = Path(os.environ.get("CWC_DB", ROOT / "data" / "sermons.db"))

CHUNK_SECONDS = 30
CHUNK_STEP = 15
VERSE_CONTEXT_SECONDS = 240  # a bare "verse 9" attaches to the book/chapter said within this window

SCHEMA = """
CREATE TABLE videos (
  id TEXT PRIMARY KEY, title TEXT, description TEXT, date TEXT, duration REAL,
  thumbnail TEXT, url TEXT, caption_source TEXT, words INTEGER
);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, video_id TEXT, start REAL, end REAL, text TEXT,
  times TEXT  -- per-word start times (deciseconds, comma-separated), aligned with text.split()
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id',
  tokenize='porter unicode61');
CREATE VIRTUAL TABLE videos_fts USING fts5(title, description, content='videos',
  tokenize='porter unicode61');
CREATE TABLE refs (
  video_id TEXT, book TEXT, chapter INTEGER, vs INTEGER, ve INTEGER,
  t REAL,          -- seconds into the video; NULL for title/description refs
  source TEXT,     -- transcript | title | description
  context TEXT
);
CREATE INDEX refs_book ON refs(book, chapter);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def parse_json3(raw):
    """-> list of (seconds, word). Auto-captions have word-level offsets;
    human captions are per line, so words in a line share its start time."""
    words = []
    for ev in raw.get("events", []):
        segs = ev.get("segs")
        if not segs or ev.get("aAppend"):
            continue
        t0 = ev.get("tStartMs", 0)
        for s in segs:
            txt = (s.get("utf8") or "").replace("\n", " ")
            t = (t0 + s.get("tOffsetMs", 0)) / 1000
            for w in txt.split():
                if w.startswith("[") and w.endswith("]"):  # [Music], [Applause]
                    continue
                words.append((t, w))
    words.sort(key=lambda x: x[0])
    return words


def build_text(words):
    """Join words into one string and remember where each word starts."""
    parts, offsets, pos = [], [], 0
    for _, w in words:
        offsets.append(pos)
        parts.append(w)
        pos += len(w) + 1
    return " ".join(parts), offsets


def make_chunks(words):
    if not words:
        return []
    chunks = []
    end_t = words[-1][0]
    starts = [t for t, _ in words]
    t = 0.0
    while t <= end_t:
        i = bisect.bisect_left(starts, t)
        j = bisect.bisect_left(starts, t + CHUNK_SECONDS)
        if j > i:
            seg = words[i:j]
            chunks.append((seg[0][0], seg[-1][0], " ".join(w for _, w in seg),
                           ",".join(str(int(x * 10)) for x, _ in seg)))
        t += CHUNK_STEP
    return chunks


def transcript_refs(text, offsets, words):
    """Scripture refs with the second each was spoken, plus bare "verse N"
    mentions resolved against the most recent book+chapter."""
    def time_at(char_pos):
        k = max(0, bisect.bisect_right(offsets, char_pos) - 1)
        return words[k][0]

    def context(a, b):
        return text[max(0, a - 120): b + 160]

    out = []
    full = find_refs(text)
    for r in full:
        out.append((r.book, r.chapter, r.verse_start, r.verse_end, time_at(r.start), context(r.start, r.end)))

    spans = [(r.start, r.end) for r in full]
    full_times = [(time_at(r.start), r) for r in full]
    for pos, v1, v2 in find_verse_only(text):
        if any(a <= pos < b for a, b in spans):
            continue
        t = time_at(pos)
        recent = [r for rt, r in full_times if 0 <= t - rt <= VERSE_CONTEXT_SECONDS]
        if recent:
            r = recent[-1]
            out.append((r.book, r.chapter, v1, v2, t, context(pos, pos + 20)))
    return out


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}
NUM_DATE = re.compile(r"(?<![\d.:/])(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})(?![\d.:/]?\d)")
WORD_DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)


def title_date(title):
    """Date written in a title: 10.18.26, 10.18.2026, 10/18/26, October 18, 2026."""
    import datetime as dt
    for m in NUM_DATE.finditer(title or ""):
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            continue
    m = WORD_DATE.search(title or "")
    if m:
        try:
            return dt.date(int(m[3]), MONTHS[m[1].lower()], int(m[2])).isoformat()
        except ValueError:
            pass
    return None


def date_of(info):
    td = title_date(info.get("title"))
    if td:
        return td
    d = info.get("upload_date")
    if info.get("release_timestamp"):
        import datetime as dt
        d = dt.datetime.utcfromtimestamp(info["release_timestamp"]).strftime("%Y%m%d")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else None


def kind_of(title):
    """'sermon' / 'service' from titles like "Sermon | Ordinary Time | 10.18.26"; else None."""
    first = (title or "").split("|")[0].strip().lower()
    return first if first in ("sermon", "service") else None


def _name(title):
    """The middle of "Sermon | The Second Call | Ordinary Time | 06-02-24": "the second call"."""
    parts = [p.strip().lower() for p in (title or "").split("|")]
    return parts[1] if len(parts) >= 2 and parts[1] and not NUM_DATE.search(parts[1]) else None


def skip_duplicate_sermons(infos, has_words):
    """Ids of Sermon cuts whose full Service is also posted. The service wins because it also
    holds what was said and sung in worship. A match is the same date, or the same sermon name
    within three days (some services carry no date in the title). A service with no transcript
    doesn't count, so its sermon cut stays in."""
    import datetime as dt
    day = lambda i: dt.date.fromisoformat(date_of(i)) if date_of(i) else None
    services = [(day(i), _name(i.get("title"))) for i in infos
                if kind_of(i.get("title")) == "service" and has_words(i["id"])]
    skip = set()
    for i in infos:
        if kind_of(i.get("title")) != "sermon" or not day(i):
            continue
        d, name = day(i), _name(i.get("title"))
        if any(sd == d or (name and sn == name and sd and abs((sd - d).days) <= 3) for sd, sn in services):
            skip.add(i["id"])
    return skip


def video_words(con, vid):
    """The transcript as (words, deciseconds), each word once. Chunks are
    overlapping windows, so each chunk's shared prefix with the previous one is skipped."""
    words, times, prev = [], [], Counter()
    for r in con.execute("SELECT text, times FROM chunks WHERE video_id=? ORDER BY start", (vid,)):
        pairs = list(zip(r[0].split(), (int(x) for x in r[1].split(",")))) if r[1] else []
        seen, k = prev.copy(), 0
        while k < len(pairs) and seen[pairs[k]] > 0:
            seen[pairs[k]] -= 1
            k += 1
        for w, t in pairs[k:]:
            words.append(w)
            times.append(t)
        prev = Counter(pairs)
    return words, times


def build(db_path=DB, raw_dir=RAW, verbose=True):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    n_videos = n_refs = 0
    infos = [json.loads(p.read_text()) for p in sorted(raw_dir.glob("*.info.json"))]
    has_words = lambda vid: (raw_dir / f"{vid}.json3").exists()
    skip = skip_duplicate_sermons(infos, has_words)
    for info in infos:
        vid = info["id"]
        if vid in skip:
            if verbose:
                print(f"  {vid}  skipped (the full service is posted)  {info.get('title')}")
            continue
        cap_path = raw_dir / f"{vid}.json3"
        words = parse_json3(json.loads(cap_path.read_text())) if cap_path.exists() else []
        con.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?)", (
            vid, info.get("title"), info.get("description") or "", date_of(info), info.get("duration"),
            info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            info.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            info.get("caption_source"), len(words)))
        for c in make_chunks(words):
            con.execute("INSERT INTO chunks (video_id, start, end, text, times) VALUES (?,?,?,?,?)", (vid, *c))
        rows = []
        if words:
            text, offsets = build_text(words)
            rows += [(vid, *r[:5], "transcript", r[5]) for r in transcript_refs(text, offsets, words)]
        for field in ("title", "description"):
            for r in find_refs(info.get(field) or "", strict=True):
                rows.append((vid, r.book, r.chapter, r.verse_start, r.verse_end, None, field, None))
        con.executemany("INSERT INTO refs VALUES (?,?,?,?,?,?,?,?)", rows)
        n_videos += 1
        n_refs += len(rows)
        if verbose:
            print(f"  {vid}  {len(words):6d} words  {len(rows):3d} refs  {info.get('title')}")
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    con.execute("INSERT INTO videos_fts(videos_fts) VALUES('rebuild')")
    con.commit()
    con.close()
    tmp.replace(db_path)
    if verbose:
        print(f"Indexed {n_videos} videos ({len(skip)} sermon cuts skipped for their full service), {n_refs} scripture references -> {db_path}")


if __name__ == "__main__":
    build()
