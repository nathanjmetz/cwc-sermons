"""Search the sermon index. One query box handles scripture references,
dates, and words/phrases/themes, alone or mixed:

    Ezekiel 4:9          -> sermons that cite that verse (or its chapter), at the moment it's cited
    forgiveness          -> moments where it's discussed
    "prodigal son"       -> exact phrase
    grace 2023           -> "grace", only in 2023 sermons
    March 2024 / 2024-03-10
"""
from __future__ import annotations

import calendar
import json
import math
import re
import sqlite3
from datetime import date, timedelta

from .index import DB
from .scripture import find_refs, parse_query_ref

LEAD_IN = 4  # start the video a few seconds before the words, so the sentence isn't cut off
STOP = set("a an the and or of to in on for is are was be it that this with as at by from i you we he she they "
           "what how why when who about do does did my your our his her their me us them".split())
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


def connect(path=DB):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


# ---------- query parsing ----------

def extract_dates(q):
    """Pull a date range out of the query. Returns (rest_of_query, (from, to) or None)."""
    rng = None

    def take(m, a, b):
        nonlocal rng
        rng = (a.isoformat(), b.isoformat())
        return " "

    def iso(m):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return take(m, d - timedelta(days=3), d + timedelta(days=3))

    def us(m):
        y = int(m[3]); y += 2000 if y < 100 else 0
        d = date(y, int(m[1]), int(m[2]))
        return take(m, d - timedelta(days=3), d + timedelta(days=3))

    def month_year(m):
        mo, y = MONTHS[m[1].lower().rstrip(".")], int(m[2])
        return take(m, date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1]))

    def year(m):
        y = int(m[1])
        return take(m, date(y, 1, 1), date(y, 12, 31))

    month_re = "|".join(sorted(MONTHS, key=len, reverse=True))
    for pat, fn in [
        (r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", iso),
        (r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", us),
        (rf"\b({month_re})\.?,?\s+(20\d\d)\b", month_year),
        (r"(?<![:\d])\b(20[0-3]\d)\b(?![:\d])", year),
    ]:
        new = re.sub(pat, fn, q, count=1, flags=re.IGNORECASE)
        if new != q:
            return re.sub(r"\s+", " ", new).strip(), rng
    return q, None


def fts_query(text, mode="and"):
    phrases = re.findall(r'"([^"]+)"', text)
    rest = re.sub(r'"[^"]*"', " ", text)
    terms = [t for t in re.findall(r"[\w']+", rest.lower()) if t not in STOP and len(t) > 1]
    parts = ['"' + p.replace('"', "") + '"' for p in phrases] + ['"' + t.replace("'", "") + '"' for t in terms]
    if not parts:
        return None, []
    words = [w for p in phrases for w in p.lower().split()] + terms
    return (" AND " if mode == "and" else " OR ").join(parts), words


# ---------- helpers ----------

def _video(con, vid, cache):
    if vid not in cache:
        cache[vid] = dict(con.execute("SELECT id, title, date, duration, thumbnail, url, speakers, sermon_from, sermon_to "
                                      "FROM videos WHERE id=?",
                                      (vid,)).fetchone())
        cache[vid]["speakers"] = _split(cache[vid]["speakers"])
    return cache[vid]


_split = lambda s: [n for n in (s or "").split("; ") if n]


class Within(tuple):
    """A (from, to) date range that can also hold the videos a speaker filter allows."""
    only = None


def _date_ok(v, rng):
    if getattr(rng, "only", None) is not None and v["id"] not in rng.only:
        return False
    return not rng or (v["date"] and rng[0] <= v["date"] <= rng[1])


def _time_ok(v, t, rng):
    """With a speaker chosen, a full service only counts while the sermon runs: the worship
    before it was led by someone else."""
    if getattr(rng, "only", None) is None or v.get("sermon_from") is None:
        return True
    return v["sermon_from"] - 30 <= t <= v["sermon_to"] + 30


def _stem(w):
    return re.sub(r"(ing|ed|es|s|ness|ful|ly)$", "", w.lower())[:6] or w.lower()


def _locate(chunk, words):
    """Exact second within a chunk where the first query word appears."""
    toks = chunk["text"].split()
    times = [int(x) / 10 for x in chunk["times"].split(",")]
    stems = {_stem(w) for w in words}
    for i, tok in enumerate(toks):
        clean = re.sub(r"[^\w']", "", tok.lower())
        if clean and any(clean.startswith(s) or _stem(clean) == s for s in stems):
            return times[i] if i < len(times) else chunk["start"]
    return chunk["start"]


def _snippet(text, words, width=260):
    stems = {_stem(w) for w in words}
    toks = text.split()
    hit = next((i for i, t in enumerate(toks)
                if any(re.sub(r"[^\w']", "", t.lower()).startswith(s) for s in stems)), 0)
    lo = max(0, hit - 18)
    s = " ".join(toks[lo: lo + 55])
    return ("… " if lo else "") + s[:width] + " …"


def _moment(t, label, snippet, score):
    return {"t": max(0, int(t - LEAD_IN)), "label": label, "snippet": snippet, "score": round(score, 3)}


def _finish(results, limit):
    out = sorted(results.values(), key=lambda r: (r["score"], r["date"] or ""), reverse=True)[:limit]
    for r in out:
        r["moments"].sort(key=lambda m: -m["score"])
        best = r["moments"][0] if r["moments"] else None
        r["start"] = best["t"] if best else 0
        # keep the best moment first, then the rest in video order
        r["moments"] = ([best] + sorted(r["moments"][1:6], key=lambda m: m["t"])) if best else []
    return out


# ---------- scripture search ----------

def _ref_score(q, row):
    if row["book"] != q.book:
        return 0
    if q.chapter is None:
        return 1.0
    if row["chapter"] != q.chapter:
        return 0.25 if row["chapter"] is None else 0
    if q.verse_start is None:
        return 3.0 if row["vs"] is None else 2.6
    if row["vs"] is None:
        return 1.8  # cited the chapter, not this verse
    qa, qb = q.verse_start, q.verse_end or q.verse_start
    ra, rb = row["vs"], row["ve"] or row["vs"]
    if ra <= qb and qa <= rb:
        return 3.0 if (ra, rb) == (qa, qb) else 2.7
    gap = min(abs(ra - qb), abs(qa - rb))
    return 1.4 if gap <= 3 else 0.9


def search_scripture(con, q, rng, limit):
    cache, results, said = {}, {}, {}
    rows = con.execute("SELECT * FROM refs WHERE book=?", (q.book,)).fetchall()
    for row in rows:
        s = _ref_score(q, row)
        if row["t"] is not None and (row["video_id"] not in said or row["t"] < said[row["video_id"]]["t"]):
            said[row["video_id"]] = row   # earliest time this book comes up out loud, any chapter
        if s <= 0:
            continue
        v = _video(con, row["video_id"], cache)
        if not _date_ok(v, rng) or (row["t"] is not None and not _time_ok(v, row["t"], rng)):
            continue
        r = results.setdefault(v["id"], {**v, "score": 0.0, "moments": [], "cited_in": set(), "hits": 0, "best": 0.0})
        r["best"] = max(r["best"], s)
        label = row["book"] + (f" {row['chapter']}" if row["chapter"] else "") + \
            (f":{row['vs']}" if row["vs"] else "") + (f"-{row['ve']}" if row["ve"] and row["ve"] != row["vs"] else "")
        if row["t"] is None:
            r["cited_in"].add(f"{row['source']}: {label}")
            r["score"] = max(r["score"], s * 0.9)
            r["desc_score"] = max(r.get("desc_score", 0), s)
            continue
        r["hits"] += 1
        # Ties go to the earliest mention: that's where the preacher starts on it.
        r["moments"].append(_moment(row["t"], label, row["context"], s - row["t"] / 1e6))
        r["score"] = max(r["score"], s)
    for r in results.values():
        r["score"] += 0.25 * math.log1p(r.pop("hits")) + 0.5 * (r.pop("desc_score", 0) >= 2.6)
        r["cited_in"] = sorted(r["cited_in"])
        r["kind"] = "scripture"
        # a chapter or verse was asked for, but this sermon only touches the book (or a far-off chapter)
        r["near"] = q.chapter is not None and r.pop("best") < 0.5
        if not r["moments"]:
            # only cited in the description: start where the book is first read or named aloud
            row = said.get(r["id"]) and _ok_row(said[r["id"]], r, rng)
            if row:
                lab = row["book"] + (f" {row['chapter']}" if row["chapter"] else "")
                r["moments"].append(_moment(row["t"], lab, row["context"], 0))
            else:
                hit = _first_said(con, r["id"], q.book.split()[-1])
                if hit:
                    r["moments"].append(_moment(hit[0], q.book, hit[1], 0))
    return _finish(results, limit)


def _ok_row(row, v, rng):
    return row if _time_ok(v, row["t"], rng) else None


def _first_said(con, vid, word):
    """(seconds, snippet) where a word is first spoken in a video, or None."""
    row = con.execute("SELECT c.start, c.text, c.times FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                      "WHERE chunks_fts MATCH ? AND c.video_id = ? ORDER BY c.start LIMIT 1",
                      (f'"{word}"', vid)).fetchone()
    return (_locate(row, [word]), _snippet(row["text"], [word])) if row else None


# ---------- text search ----------

def search_text(con, text, rng, limit):
    out = None
    for mode in ("and", "or"):
        fq, words = fts_query(text, mode)
        if not fq:
            return []
        rows = con.execute(
            "SELECT c.id, c.video_id, c.start, c.text, c.times, bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 3000", (fq,)).fetchall()
        title_hits = {r["id"]: -r["rank"] for r in con.execute(
            "SELECT v.id, bm25(videos_fts) AS rank FROM videos_fts JOIN videos v ON v.rowid = videos_fts.rowid "
            "WHERE videos_fts MATCH ?", (fq,))}
        cache, results = {}, {}
        for row in rows:
            v = _video(con, row["video_id"], cache)
            if not _date_ok(v, rng) or not _time_ok(v, row["start"], rng):
                continue
            r = results.setdefault(v["id"], {**v, "score": 0.0, "moments": [], "hits": 0, "kind": "text"})
            s = -row["rank"]
            # overlapping windows: skip a moment within 20s of one we already have
            if any(abs(m["_c"] - row["start"]) < 20 for m in r["moments"]):
                continue
            t = _locate(row, words)
            m = _moment(t, None, _snippet(row["text"], words), s)
            m["_c"] = row["start"]
            r["moments"].append(m)
            r["hits"] += 1
            r["score"] = max(r["score"], s)
        for vid, r in results.items():
            r["score"] += 0.6 * math.log1p(r.pop("hits")) + (1.5 if vid in title_hits else 0)
            for m in r["moments"]:
                m.pop("_c", None)
        out = _finish(results, limit)
        if len(out) >= 3 or mode == "or":
            for r in out:
                r["matched"] = "all words" if mode == "and" else "some words"
            return out
    return out


def browse_dates(con, rng, limit):
    rows = con.execute("SELECT id, title, date, duration, thumbnail, url, speakers, sermon_from, sermon_to FROM videos "
                       "WHERE date BETWEEN ? AND ? ORDER BY date DESC", tuple(rng)).fetchall()
    rows = [{**dict(r), "speakers": _split(r["speakers"])} for r in rows]
    rows = [r for r in rows if _date_ok(r, rng)][:limit]
    # with a speaker chosen, a full service opens where the sermon starts
    start = lambda r: int(r["sermon_from"]) if getattr(rng, "only", None) is not None and r["sermon_from"] else 0
    return [{**r, "score": 0, "start": start(r), "moments": [], "kind": "date"} for r in rows]


def search(con, q, limit=20, date_from=None, date_to=None, mode="words", jev=True, speaker=None):
    q = (q or "").strip()
    text, rng = extract_dates(q)
    if date_from or date_to:
        rng = (date_from or "0000-00-00", date_to or "9999-12-31")
    info = {"query": q, "date_range": rng, "scripture": None, "speaker": speaker or None}
    if speaker:
        rng = Within(rng or ("0000-00-00", "9999-12-31"))
        rng.only = {r[0] for r in con.execute("SELECT id, speakers FROM videos") if speaker in _split(r[1])}
    info["mode"] = mode
    if mode == "ideas" and text:
        return {**info, **_count(search_ideas(con, text, rng, limit, use_jev=jev))}
    ref = parse_query_ref(text) if text else None
    if ref:
        # Anything left after the reference ("Romans 8 suffering") narrows by words too.
        refs = find_refs(text)
        rest = text if refs else ""
        for r in reversed(refs):
            rest = rest[:r.start] + " " + rest[r.end:]
        info["scripture"] = ref.label()
        results = search_scripture(con, ref, rng, limit)
        if rest.strip():
            keep = {r["id"] for r in search_text(con, rest, rng, 500)}
            results = [r for r in results if r["id"] in keep]
        if not results and ref.chapter is None:
            results = search_text(con, text, rng, limit)  # e.g. "Job" meaning the word
    elif text:
        results = search_text(con, text, rng, limit)
    elif rng:
        results = browse_dates(con, rng, limit)
    else:
        results = []
    return {**info, "count": len(results), "results": results}


def _count(results):
    return {"count": len(results), "results": results}


def stats(con):
    v = con.execute("SELECT COUNT(*), MIN(date), MAX(date), SUM(duration), SUM(words) FROM videos").fetchone()
    top = con.execute("SELECT book, COUNT(DISTINCT video_id) n FROM refs GROUP BY book ORDER BY n DESC LIMIT 12").fetchall()
    return {"videos": v[0], "first": v[1], "last": v[2], "hours": round((v[3] or 0) / 3600, 1),
            "words": v[4] or 0, "top_books": [dict(r) for r in top], "speakers": speaker_counts(con)}


def speaker_counts(con):
    from collections import Counter
    n = Counter(name for (s,) in con.execute("SELECT speakers FROM videos") for name in _split(s))
    return [[name, c] for name, c in sorted(n.items(), key=lambda x: (-x[1], x[0]))]


# ---------- idea search (meaning, not words) ----------

_vec_cache = {}


def _vectors():
    from . import embed
    import numpy as np
    key = embed.VECTORS.stat().st_mtime
    if _vec_cache.get("key") != key:
        meta = json.loads(embed.PASSAGES.read_text())
        vecs = np.fromfile(embed.VECTORS, dtype=np.int8).reshape(-1, meta["dim"])
        _vec_cache.update(key=key, meta=meta["passages"], vecs=vecs)
    return _vec_cache["meta"], _vec_cache["vecs"]


def _refine(words, times, s_ds, e_ds, qvec, encode):
    """Within a 60s passage, find the 25s stretch closest to the idea, so the
    video starts where the thought begins rather than up to 30s early."""
    subs = []
    for a in range(s_ds, max(s_ds + 1, e_ds - 200), 100):
        seg = [w for w, t in zip(words, times) if a <= t < a + 250]
        if len(seg) >= 8:
            subs.append((a, " ".join(seg)))
    if len(subs) < 2:
        return s_ds
    return subs[int((encode([t for _, t in subs]) @ qvec).argmax())][0]


JEV_POOL = 30      # sermons gathered for Jev to judge
JEV_PASSAGES = 60  # passages sent per search (two calls of 30)


def _jev_rerank(con, q, results):
    """Blend Jev's 'does this passage address the query' probability into scores."""
    from . import embed, rerank
    picks = []
    for r in sorted(results.values(), key=lambda r: -r["score"]):
        for m in sorted(r["moments"], key=lambda m: -m["score"])[:2]:
            picks.append((r, m))
    picks = picks[:JEV_PASSAGES]
    words = {}
    texts = []
    for r, m in picks:
        if r["id"] not in words:
            words[r["id"]] = embed.video_words(con, r["id"])
        w, t = words[r["id"]]
        texts.append(" ".join(x for x, y in zip(w, t) if m["_s"] <= y <= m["_e"]))
    probs = rerank.rerank(q, texts)
    if probs is None:
        return
    judged = {id(m) for _, m in picks}
    for (r, m), p in zip(picks, probs):
        m["jev"] = round(p, 3)
        m["score"] = 0.75 * p + 0.25 * m["score"]
    for r in results.values():
        # unjudged moments rank below judged ones; near-misses go when better ones remain
        for m in r["moments"]:
            if id(m) not in judged:
                m["score"] *= 0.25
        keep = [m for m in r["moments"] if m.get("jev", 1) >= 0.05]
        r["moments"] = keep or r["moments"][:1]
        r["score"] = round(max(m["score"] for m in r["moments"]), 4) if r["moments"] else 0
        r["jev"] = max((m.get("jev", 0) for m in r["moments"]), default=0)


def search_ideas(con, q, rng, limit, encode=None, refine=8, use_jev=True):
    """Rank sermons by how close any one-minute passage comes to the meaning of q.
    With a Jev key, a wider pool is re-judged passage by passage before trimming."""
    import numpy as np
    from . import embed, rerank
    encode = encode or embed.encode
    final, jev = limit, use_jev and rerank.api_key()
    if jev:
        limit = max(limit, JEV_POOL)
    meta, vecs = _vectors()
    qvec = encode([q], query=True)[0]
    sims = (vecs.astype(np.float32) @ qvec) / 127.0
    cache, results = {}, {}
    for k in np.argsort(-sims)[:5000]:
        vid, s_ds, e_ds = meta[k]
        v = _video(con, vid, cache)
        if not _date_ok(v, rng) or not _time_ok(v, s_ds / 10, rng):
            continue
        sim = float(sims[k])
        if vid not in results:
            if len(results) >= limit:
                continue
            results[vid] = {**v, "score": sim, "moments": [], "kind": "idea", "_n": 0}
        r = results[vid]
        if sim >= r["score"] - 0.03:
            r["_n"] += 1
        if (sim >= r["score"] - 0.06 and len(r["moments"]) < 6
                and all(abs(m["_s"] - s_ds) >= 450 for m in r["moments"])):
            m = _moment(s_ds / 10, None, None, sim)
            m.update(_s=s_ds, _e=e_ds)
            r["moments"].append(m)
    for r in results.values():
        # sermons that stay on the idea for several minutes edge out a single passing mention
        r["score"] = round(r["score"] + 0.01 * min(r.pop("_n") - 1, 5), 4)
    if jev:
        _jev_rerank(con, q, results)
    out = _finish(results, final)
    for i, r in enumerate(out):
        words, times = embed.video_words(con, r["id"])
        for j, m in enumerate(r["moments"]):
            s_ds, e_ds = m.pop("_s"), m.pop("_e")
            start = _refine(words, times, s_ds, e_ds, qvec, encode) if i < refine and j == 0 else s_ds
            m["t"] = max(0, int(start / 10 - LEAD_IN))
            text = " ".join(w for w, t in zip(words, times) if start <= t <= e_ds)
            m["snippet"] = text[:280] + (" …" if len(text) > 280 else "")
            m["score"] = round(m["score"], 3)
        r["start"] = r["moments"][0]["t"] if r["moments"] else 0
    return out
