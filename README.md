# College Wes Sermon Library (local dev)

Search every College Wesleyan Church sermon on YouTube by scripture reference,
word, phrase, theme, or date, and jump straight to the second it's preached.

## Run it

```bash
./run.sh ingest 15   # pull the newest 15 sermons' captions from YouTube, then build the index
./run.sh             # http://localhost:8787
```

Set the channel in `config.json` (`channel_url`), or pass `--channel` to `python -m cwc.ingest`.
`./run.sh ingest 0` pulls the whole channel (slow; YouTube rate-limits, so it sleeps between videos and
resumes where it left off). If YouTube starts refusing requests, add `--cookies-from-browser chrome`.

## How it works

1. **`cwc/ingest.py`** lists the channel's long-form videos (livestreams + uploads) with yt-dlp and saves each
   video's metadata and YouTube caption track (`json3`, which has a timestamp for every word) to `data/raw/`.
2. **`cwc/index.py`** builds `data/sermons.db` (SQLite):
   - 30-second overlapping transcript windows in a full-text index (stemmed, so "forgive" finds "forgiveness").
   - Every scripture reference, with the second it was spoken. `cwc/scripture.py` understands written forms
     ("Ezek 4:9", "1 Cor 13:4-7") and spoken ones from captions ("first Corinthians thirteen verses four through
     seven"), and attaches a bare "look at verse nine" to the book and chapter the preacher just named.
   - References in titles and descriptions (sermon-level, no timestamp).
3. **`cwc/search.py`** reads the query: a scripture reference ranks exact-verse citations first, then the same
   chapter, then nearby verses; dates ("2023", "March 2024", "3/10/2024") filter; everything else is a word or
   "quoted phrase" search. Each result starts a few seconds before the matching words.
4. **`cwc/server.py`** serves `static/index.html` and `/api/search`. Standard library only.

## Tests

```bash
.venv/bin/pip install pytest && .venv/bin/python -m pytest -q
```

## Next steps

- Semantic search, so "anxiety" also finds "worry" and "fear" (local embeddings over the transcript windows).
- Speaker and sermon-series fields (from titles/descriptions or collegewes.com).
- Detect where the sermon starts inside full-service livestreams.
