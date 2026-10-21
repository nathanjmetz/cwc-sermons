"""Idea search plumbing, with a toy encoder standing in for the real model
(which needs a download): words map to shared 'concept' dimensions."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
from cwc import embed, search
from test_pipeline import build

CONCEPTS = {"forgive": 0, "forgiveness": 0, "mercy": 0, "pardon": 0, "grace": 0, "decision": 0,
            "shepherd": 1, "lord": 1, "sheep": 1, "guide": 1,
            "prodigal": 2, "father": 2, "son": 2, "home": 2, "ran": 2, "lost": 2}


def toy(texts, query=False):
    out = np.zeros((len(texts), embed.DIM), np.float32)
    for i, t in enumerate(texts):
        for w in t.lower().split():
            out[i, CONCEPTS.get(w, 10 + hash(w) % 300)] += 1
        out[i] /= max(np.linalg.norm(out[i]), 1e-9)
    return out


def test_ideas(tmp_path, monkeypatch):
    build(tmp_path)
    monkeypatch.setattr(embed, "VECTORS", tmp_path / "vectors.i8")
    monkeypatch.setattr(embed, "PASSAGES", tmp_path / "passages.json")
    con = search.connect(tmp_path / "s.db")
    # passages need >= 25 words; fixture sermons are short, so lower the bar
    orig = embed.passages_for
    monkeypatch.setattr(embed, "passages_for", lambda w, t: [(t[0], t[-1], " ".join(w))] if w else [])
    embed.build(tmp_path / "s.db", tmp_path, encoder=toy)
    res = search.search_ideas(con, "a father welcoming his lost child back", None, 10, encode=toy)
    assert res[0]["id"] == "BBBBBBBBBBB"
    assert res[0]["moments"][0]["snippet"]
    assert search.search_ideas(con, "mercy and pardon", None, 10, encode=toy)[0]["id"] in ("AAAAAAAAAAA", "BBBBBBBBBBB")
    # passages_for itself: 60s windows every 30s, nothing for short silence
    monkeypatch.setattr(embed, "passages_for", orig)
    words = [f"w{i}" for i in range(300)]
    times = [i * 5 for i in range(300)]  # one word every half second, 150s
    ps = orig(words, times)
    assert ps[0][0] == 0 and ps[1][0] == 300 and all(p[1] - p[0] < 600 for p in ps)


def test_jev_rerank(tmp_path, monkeypatch):
    """Jev's judgment reorders sermons; with no key, or on error, search runs as before."""
    from cwc import rerank
    build(tmp_path)
    monkeypatch.setattr(embed, "VECTORS", tmp_path / "vectors.i8")
    monkeypatch.setattr(embed, "PASSAGES", tmp_path / "passages.json")
    monkeypatch.setattr(embed, "passages_for", lambda w, t: [(t[0], t[-1], " ".join(w))] if w else [])
    embed.build(tmp_path / "s.db", tmp_path, encoder=toy)
    con = search.connect(tmp_path / "s.db")
    q = "a father welcoming his lost child back"
    monkeypatch.setattr(rerank, "api_key", lambda: None)
    plain = search.search_ideas(con, q, None, 10, encode=toy)
    assert plain[0]["id"] == "BBBBBBBBBBB" and "jev" not in plain[0]

    sent = []
    def fake_call(key, query, texts, timeout):
        sent.append(len(texts))
        return [0.9 if "shepherd" in t.lower() else 0.02 for t in texts]
    monkeypatch.setattr(rerank, "api_key", lambda: "test")
    monkeypatch.setattr(rerank, "_call", fake_call)
    judged = search.search_ideas(con, q, None, 10, encode=toy)
    assert sent and judged[0]["jev"] == 0.9 and judged[0]["id"] != "BBBBBBBBBBB"
    assert all("_s" not in m for r in judged for m in r["moments"])

    def boom(*a):
        raise OSError("down")
    monkeypatch.setattr(rerank, "_call", boom)
    assert [r["id"] for r in search.search_ideas(con, q, None, 10, encode=toy)] == [r["id"] for r in plain]
