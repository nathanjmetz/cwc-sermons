"""Optional second pass for idea search: ask Jev (TypeSafe's decision model)
whether each candidate passage really addresses what the person described.

Each passage gets a calibrated probability, used to reorder results and drop
near-misses. Recipe from github.com/hev/jev-rerank: one Noul question per
passage, up to 30 passages per call, all answered in parallel.

The key lives in TYPESAFE_API_KEY or in .env.typesafe (git-ignored). Without
a key, or if the call fails, search simply skips this step.
"""
from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
PER_CALL = 30
KEY_FILE = Path(__file__).resolve().parent.parent / ".env.typesafe"

QUESTION = ("Passage `passages.{id}` is relevant to `query`: in it the preacher speaks to what the query "
            "describes, directly or by clear implication.")
CRITERIA = {
    "true": "The passage addresses the idea, theme, or situation the query describes.",
    "false": "The passage is only loosely related, merely shares a word or topic, or does not address the query.",
}


def api_key():
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return key or None


def _call(key, query, texts, timeout):
    ids = [f"P{i:02d}" for i in range(len(texts))]
    body = {
        "model": MODEL,
        "state": {"query": query, "passages": dict(zip(ids, texts))},
        "questions": {pid: {"type": "noul", "instructions": QUESTION.format(id=pid), "criteria": CRITERIA}
                      for pid in ids},
    }
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        answers = json.loads(r.read())["answers"]
    return [float(answers[pid]["noul"]) for pid in ids]


def rerank(query, texts, timeout=8.0):
    """Probability (0-1) that each text addresses the query, or None if unavailable."""
    key = api_key()
    if not key or not texts:
        return None
    batches = [texts[i:i + PER_CALL] for i in range(0, len(texts), PER_CALL)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(lambda b: _call(key, query, b, timeout), batches))
    except Exception as e:  # network, quota, or API change: fall back to meaning-only ranking
        print(f"rerank skipped: {e}")
        return None
    return [p for part in parts for p in part]
