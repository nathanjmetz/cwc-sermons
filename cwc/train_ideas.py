"""Teach the Ideas model what "on point" means for these sermons, once, in the background.

    python -m cwc.train_ideas grade    # Jev grades candidate passages for each practice search (cached)
    python -m cwc.train_ideas train    # fine-tune bge-small on those grades -> data/model-ideas/
    python -m cwc.train_ideas eval     # held-out searches, before vs after, graded by Jev
    python -m cwc.train_ideas ship     # re-embed every passage with the new model, export it for the browser

Jev only answers yes/no-with-a-probability, so it can't write better queries; instead its grades
become training pairs (search, passage it said is on point / passage it said is not). Grades are
cached in data/train/grades.jsonl, so a rerun never pays twice for the same judgment.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys

import numpy as np

from . import embed, rerank
from .idea_queries import TEST, TRAIN
from .index import DB

OUT = embed.DATA / "train"
GRADES = OUT / "grades.jsonl"
TUNED = embed.DATA / "model-ideas"
TOP, DEEP, DEEP_FROM, DEEP_TO = 60, 30, 60, 600
GAP_DS = 450   # passages closer than 45s in the same sermon count as the same moment


def passages():
    """(meta [vid, s_ds, e_ds], texts) in the order of data/passages.json."""
    meta = json.loads(embed.PASSAGES.read_text())["passages"]
    con = sqlite3.connect(DB)
    words, texts = {}, []
    for vid, s, e in meta:
        if vid not in words:
            words[vid] = embed.video_words(con, vid)
        w, t = words[vid]
        texts.append(" ".join(x for x, y in zip(w, t) if s <= y <= e))
    return meta, texts


def distinct(order, meta, n, skip=()):
    """The first n passages in `order` that aren't the same moment as one already picked."""
    out, seen = [], {}
    for k in order:
        vid, s, _ = meta[k]
        if k in skip or any(abs(s - s2) < GAP_DS for s2 in seen.get(vid, ())):
            continue
        out.append(int(k))
        seen.setdefault(vid, []).append(s)
        if len(out) >= n:
            break
    return out


def load_grades():
    g = {}
    if GRADES.exists():
        for line in GRADES.read_text().splitlines():
            r = json.loads(line)
            g[(r["q"], r["k"])] = r["p"]
    return g


def grade(q, ks, texts, grades):
    """Jev's probability for each passage index in ks, asking only for the ones not yet cached."""
    todo = [k for k in ks if (q, k) not in grades]
    if todo:
        probs = rerank.rerank(q, [texts[k] for k in todo], timeout=30)
        if probs is None:
            raise SystemExit("Jev didn't answer; stopping (grades so far are saved)")
        OUT.mkdir(parents=True, exist_ok=True)
        with GRADES.open("a") as f:
            for k, p in zip(todo, probs):
                grades[(q, k)] = p
                f.write(json.dumps({"q": q, "k": k, "p": round(p, 4)}) + "\n")
    return [grades[(q, k)] for k in ks]


def vectors_for():
    vecs = np.fromfile(embed.VECTORS, dtype=np.int8).reshape(-1, embed.DIM).astype(np.float32) / 127
    return vecs


def query_vec(model, q):
    return model.encode([embed.QUERY_PREFIX + q], normalize_embeddings=True)[0]


def cmd_grade():
    from sentence_transformers import SentenceTransformer
    meta, texts = passages()
    vecs, model, grades = vectors_for(), SentenceTransformer(embed.MODEL), load_grades()
    rnd = random.Random(7)
    for i, q in enumerate(TRAIN):
        order = np.argsort(-(vecs @ query_vec(model, q)))
        top = distinct(order, meta, TOP)
        deep = distinct(order[DEEP_FROM * 3:DEEP_TO * 3], meta, DEEP * 4, skip=set(top))
        ks = top + rnd.sample(deep, min(DEEP, len(deep)))
        ps = grade(q, ks, texts, grades)
        print(f"{i + 1:3d}/{len(TRAIN)}  {sum(p >= .5 for p in ps):2d} on point of {len(ks)}  {q}", flush=True)


def cmd_train(epochs=1):
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader
    meta, texts = passages()
    grades, rnd = load_grades(), random.Random(11)
    by_q = {}
    for (q, k), p in grades.items():
        if q in TRAIN:
            by_q.setdefault(q, []).append((k, p))
    examples = []
    for q, kp in by_q.items():
        pos = [k for k, p in kp if p >= 0.6]
        neg = [k for k, p in kp if p <= 0.2]
        if not pos or not neg:
            continue
        for k in pos[:10]:
            examples.append(InputExample(texts=[embed.QUERY_PREFIX + q, texts[k], texts[rnd.choice(neg)]]))
    rnd.shuffle(examples)
    print(f"{len(examples)} training triplets from {len(by_q)} searches")
    model = SentenceTransformer(embed.MODEL)
    loader = DataLoader(examples, shuffle=True, batch_size=32)
    model.fit(train_objectives=[(loader, losses.MultipleNegativesRankingLoss(model))], epochs=epochs,
              warmup_steps=max(1, len(loader) // 10), optimizer_params={"lr": 1e-5}, show_progress_bar=True)
    model.save(str(TUNED))
    print("saved", TUNED)


def cmd_eval():
    """Grade each held-out search's results from both models: the top 10 sermons by meaning alone,
    and the top 10 after Jev's usual second look at 60 passages."""
    from sentence_transformers import SentenceTransformer
    meta, texts = passages()
    grades = load_grades()
    base = SentenceTransformer(embed.MODEL)
    tuned = SentenceTransformer(str(TUNED))
    vb = vectors_for()
    tv_path = OUT / "tuned_vectors.npy"
    if tv_path.exists():
        vt = np.load(tv_path)
    else:
        vt = tuned.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=True).astype(np.float32)
        np.save(tv_path, vt)
    report = {"base": [], "tuned": []}
    for q in TEST:
        line = []
        for name, model, vecs in (("base", base, vb), ("tuned", tuned, vt)):
            sims = vecs @ query_vec(model, q)
            order = np.argsort(-sims)
            pool = distinct(order, meta, TOP)
            ps = dict(zip(pool, grade(q, pool, texts, grades)))
            per_sermon, raw = set(), []
            for k in pool:                      # meaning alone: best passage of each of the top 10 sermons
                if meta[k][0] not in per_sermon:
                    per_sermon.add(meta[k][0]); raw.append(ps[k])
            raw = raw[:10]
            blended = sorted(pool, key=lambda k: -(0.75 * ps[k] + 0.25 * float(sims[k])))
            seen, after = set(), []
            for k in blended:                   # what the site shows: Jev's second look, one per sermon
                if meta[k][0] not in seen:
                    seen.add(meta[k][0]); after.append(ps[k])
            after = after[:10]
            row = {"q": q, "raw_on_point": sum(p >= .5 for p in raw), "raw_mean": float(np.mean(raw)),
                   "jev_on_point": sum(p >= .5 for p in after), "jev_mean": float(np.mean(after)),
                   "top3": [round(p, 2) for p in after[:3]]}
            report[name].append(row)
            line.append(f"{name} {row['raw_on_point']:2d}/10 raw, {row['jev_on_point']:2d}/10 after Jev")
        print(f"{q[:44]:44s}  " + "  |  ".join(line), flush=True)
    for name in report:
        rows = report[name]
        print(f"{name:5s}: on point in top 10, meaning alone {np.mean([r['raw_on_point'] for r in rows]):.2f}, "
              f"after Jev {np.mean([r['jev_on_point'] for r in rows]):.2f}; mean score {np.mean([r['jev_mean'] for r in rows]):.3f}")
    (OUT / "eval.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    {"grade": cmd_grade, "train": cmd_train, "eval": cmd_eval}[sys.argv[1]]()
