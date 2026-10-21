"""Idea search: embed every ~60s stretch of every sermon so searches can match
meaning, not just words ("the woman at the well and how Jesus treated women"
finds passages about dignity, Samaria, or women in ministry even when those
exact words never come up).

    python -m cwc.embed        # after cwc.index; writes data/passages.json + data/vectors.i8

Uses BAAI/bge-small-en-v1.5. The static site embeds queries in the browser
with the same model (Xenova/bge-small-en-v1.5 via transformers.js).
"""
from __future__ import annotations

import json
import sqlite3
import numpy as np

from .index import DB, video_words  # noqa: F401  (re-exported for search/export)

MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
PASSAGE_SECONDS = 60
PASSAGE_STEP = 30
DATA = DB.parent
PASSAGES = DATA / "passages.json"
VECTORS = DATA / "vectors.i8"
DIM = 384

_model = None


def model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL)
    return _model


def encode(texts, query=False):
    if query:
        texts = [QUERY_PREFIX + t for t in texts]
    return model().encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=not query,
                          convert_to_numpy=True).astype(np.float32)


def passages_for(words, times):
    """[(start_ds, end_ds, text)] in 60s windows every 30s."""
    out, i, n = [], 0, len(words)
    if not n:
        return out
    t = times[0]
    while t <= times[-1]:
        while i < n and times[i] < t:
            i += 1
        j = i
        while j < n and times[j] < t + PASSAGE_SECONDS * 10:
            j += 1
        if j - i >= 25:  # skip near-silent stretches (music, prayer pauses)
            out.append((times[i], times[j - 1], " ".join(words[i:j])))
        t += PASSAGE_STEP * 10
    return out


def quantize(vecs):
    """Unit vectors -> int8 (x127). Dot products stay proportional to cosine similarity."""
    return np.clip(np.round(vecs * 127), -127, 127).astype(np.int8)


def build(db_path=DB, out_dir=DATA, encoder=None):
    con = sqlite3.connect(db_path)
    vids = [r[0] for r in con.execute("SELECT id FROM videos ORDER BY date DESC")]
    meta, texts = [], []
    for vid in vids:
        for s, e, text in passages_for(*video_words(con, vid)):
            meta.append([vid, s, e])
            texts.append(text)
    print(f"Embedding {len(texts)} passages from {len(vids)} sermons ...")
    vecs = (encoder or encode)(texts)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / PASSAGES.name).write_text(json.dumps({"model": MODEL, "dim": int(vecs.shape[1]), "passages": meta},
                                                    separators=(",", ":")))
    quantize(vecs).tofile(out_dir / VECTORS.name)
    print(f"Wrote {out_dir / VECTORS.name} ({vecs.shape[0] * vecs.shape[1] / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
