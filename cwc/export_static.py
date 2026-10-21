"""Export the index as static files for GitHub Pages (no server needed).

    python -m cwc.export_static          # -> docs/  (GitHub Pages serves this folder)

Search then runs in the visitor's browser (static/engine.js). It fetches only
what a query needs: one scripture-book file, or the word-index shards for the
query's words, plus the transcripts of the top results for snippets.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from .embed import PASSAGES, VECTORS
from .index import DB, ROOT, video_words
from .scripture import AMBIGUOUS, BOOKS
from .search import STOP, stats

SITE = ROOT / "docs"
STATIC = ROOT / "static"


def stem(w):
    """Must match stem() in static/engine.js."""
    w = re.sub(r"[^a-z0-9']", "", w.lower()).replace("'", "")
    if len(w) > 4:
        w = re.sub(r"(ing|ed|es|s|ness|ful|ly)$", "", w)
    return w[:7]


def slug(book):
    return re.sub(r"[^a-z0-9]+", "-", book.lower()).strip("-")


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def export(db_path=DB, site=SITE):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(STATIC, site)
    data = site / "data"

    videos = [dict(r) for r in con.execute(
        "SELECT id, title, description, date, duration, thumbnail, speakers FROM videos ORDER BY date DESC")]
    vidx = {v["id"]: i for i, v in enumerate(videos)}

    # word index: stem -> {video index: [deciseconds, ...]}, sharded by the stem's first two characters
    postings = defaultdict(lambda: defaultdict(list))
    for v in videos:
        words, times = video_words(con, v["id"])
        dump(data / "tx" / f"{v['id']}.json", {"w": " ".join(words), "t": times})
        i = vidx[v["id"]]
        for w, t in zip(words, times):
            s = stem(w)
            if len(s) > 1 and s not in STOP and re.sub(r"[^a-z]", "", w.lower()) not in STOP:
                postings[s][i].append(t)
        v["words"] = len(words)

    shards = defaultdict(dict)
    for s, by_video in postings.items():
        shards[s[:2]][s] = [[i, *ts] for i, ts in by_video.items()]
    for key, terms in shards.items():
        dump(data / "terms" / f"{key}.json", terms)

    refs = defaultdict(list)
    for r in con.execute("SELECT * FROM refs"):
        refs[r["book"]].append([vidx[r["video_id"]], r["chapter"], r["vs"], r["ve"],
                                None if r["t"] is None else round(r["t"], 1), r["source"],
                                (r["context"] or "")[:320]])
    for book, rows in refs.items():
        dump(data / "refs" / f"{slug(book)}.json", rows)

    aliases = {}
    for book, al in BOOKS.items():
        for a in [book.lower()] + al:
            aliases[a] = book
    dump(data / "meta.json", {
        "videos": [[v["id"], v["title"], v["date"], v["duration"], v["thumbnail"],
                    " ".join(sorted({stem(w) for w in re.findall(r"[\w']+", (v["title"] or "") + " " + (v["description"] or "")[:600])})),
                    [n for n in (v.get("speakers") or "").split("; ") if n]]
                   for v in videos],
        "books": aliases, "ambiguous": sorted(AMBIGUOUS), "slugs": {b: slug(b) for b in BOOKS},
        "stop": sorted(STOP), "stats": stats(_ro(db_path)),
    })

    # the page calls /api/...; engine.js answers those calls in the browser
    page = (site / "index.html").read_text()
    page = page.replace("<script>", '<script src="engine.js"></script>\n<script>', 1)
    (site / "index.html").write_text(page)
    # config.json "jev_url": the small function that holds the Jev key (jev-proxy/); empty skips Jev
    jev_url = json.loads((ROOT / "config.json").read_text()).get("jev_url", "")
    (site / "engine.js").write_text((ROOT / "cwc" / "engine.js").read_text().replace("__JEV_URL__", jev_url))
    if VECTORS.exists() and PASSAGES.exists():
        p = json.loads(PASSAGES.read_text())
        # passages reference videos by index into meta.videos (smaller than ids)
        dump(data / "passages.json", {**p, "passages": [[vidx[v], s, e] for v, s, e in p["passages"]]})
        shutil.copy(VECTORS, data / "vectors.i8")
    (site / ".nojekyll").write_text("")
    size = sum(p.stat().st_size for p in site.rglob("*") if p.is_file())
    print(f"Exported {len(videos)} sermons, {len(postings)} words, {sum(map(len, refs.values()))} refs "
          f"-> {site} ({size / 1e6:.1f} MB)")


def _ro(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


if __name__ == "__main__":
    export()
