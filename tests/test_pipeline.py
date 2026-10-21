"""End-to-end: fake caption tracks -> index -> search, no network needed."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from cwc import index, search


def json3(lines):
    """lines: [(start_seconds, "text")] -> YouTube json3 with word-level offsets."""
    events = []
    for t, text in lines:
        segs = [{"utf8": (" " if i else "") + w, "tOffsetMs": i * 400} for i, w in enumerate(text.split())]
        events.append({"tStartMs": int(t * 1000), "dDurationMs": 4000, "segs": segs})
    return {"events": events}


SERMONS = {
    "AAAAAAAAAAA": ("Bread of Exile", "20240310", "Scripture: Ezekiel 4", [
        (10, "welcome everybody good morning"),
        (600, "turn with me to ezekiel chapter four"),
        (640, "look at verse nine take wheat and barley beans and lentils"),
        (900, "forgiveness is not a feeling it is a decision"),
    ]),
    "BBBBBBBBBBB": ("The Prodigal Son", "20230115", "Luke 15:11-32", [
        (30, "good morning church"),
        (1200, "the prodigal son came home and the father ran"),
        (1500, "and you remember ezekiel 4:9 the prophet ate bread baked over dung"),
        (1800, "grace grace grace and forgiveness"),
    ]),
    "CCCCCCCCCCC": ("Shepherd Psalm", "20231105", "", [
        (100, "psalm twenty three verse one the lord is my shepherd"),
        (400, "my job one this week is rest"),
    ]),
}


def build(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    for vid, (title, d, desc, lines) in SERMONS.items():
        (raw / f"{vid}.info.json").write_text(json.dumps({"id": vid, "title": title, "upload_date": d,
                                                          "description": desc, "duration": 2400}))
        (raw / f"{vid}.json3").write_text(json.dumps(json3(lines)))
    db = tmp_path / "s.db"
    index.build(db, raw, verbose=False)
    return search.connect(db)


def test_scripture_search(tmp_path):
    con = build(tmp_path)
    res = search.search(con, "Ezekial 4:9")
    assert res["scripture"] == "Ezekiel 4:9"
    ids = [r["id"] for r in res["results"]]
    assert set(ids) == {"AAAAAAAAAAA", "BBBBBBBBBBB"}
    a = next(r for r in res["results"] if r["id"] == "AAAAAAAAAAA")
    assert 630 <= a["start"] <= 645, a["start"]   # bare "verse nine" resolved to Ezekiel 4:9
    b = next(r for r in res["results"] if r["id"] == "BBBBBBBBBBB")
    assert 1494 <= b["start"] <= 1505, b["start"]


def test_text_and_dates(tmp_path):
    con = build(tmp_path)
    res = search.search(con, "forgiveness")
    assert {r["id"] for r in res["results"]} == {"AAAAAAAAAAA", "BBBBBBBBBBB"}
    a = next(r for r in res["results"] if r["id"] == "AAAAAAAAAAA")
    assert 890 <= a["start"] <= 900
    res = search.search(con, "forgiveness 2023")
    assert [r["id"] for r in res["results"]] == ["BBBBBBBBBBB"]
    res = search.search(con, '"prodigal son"')
    assert res["results"][0]["id"] == "BBBBBBBBBBB"
    res = search.search(con, "March 2024")
    assert [r["id"] for r in res["results"]] == ["AAAAAAAAAAA"]


def test_psalm_and_no_false_job(tmp_path):
    con = build(tmp_path)
    res = search.search(con, "Psalm 23:1")
    assert [r["id"] for r in res["results"]] == ["CCCCCCCCCCC"]
    assert search.search(con, "Job 1")["count"] == 0
    st = search.stats(con)
    assert st["videos"] == 3


def test_caption_picker_skips_machine_translations():
    from cwc.ingest import pick_caption_track
    info = {"automatic_captions": {"en": [
        {"ext": "json3", "url": "https://x/timedtext?lang=ar&tlang=en&fmt=json3"},
        {"ext": "json3", "url": "https://x/timedtext?lang=en&fmt=json3"},
    ]}}
    assert pick_caption_track(info) == ("automatic_captions", "en", "https://x/timedtext?lang=en&fmt=json3")


def test_title_dates():
    assert index.title_date("Sermon | Ordinary Time | 10.18.26") == "2026-10-18"
    assert index.title_date("Service | Advent | 12.1.2024") == "2024-12-01"
    assert index.title_date("Easter 4/20/25") == "2025-04-20"
    assert index.title_date("Christmas Eve - December 24, 2023") == "2023-12-24"
    assert index.title_date("Ezekiel 4:9 and John 3.16") is None
    # title date beats the (next-day) upload date
    assert index.date_of({"title": "Sermon | 10.18.26", "upload_date": "20261019"}) == "2026-10-18"
    assert index.date_of({"title": "Special Event", "upload_date": "20261019"}) == "2026-10-19"


def test_sermon_cut_replaces_same_day_service(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    videos = {"SERMON00001": "Sermon | Ordinary Time | 10.18.26",
              "SERVICE0001": "Service | Ordinary Time | 10.18.26",
              "SERVICE0002": "Service | Ordinary Time | 1.7.18",   # no sermon cut that day: keep
              "SPECIAL0001": "Night of Worship"}
    for vid, title in videos.items():
        (raw / f"{vid}.info.json").write_text(json.dumps({"id": vid, "title": title, "upload_date": "20261019"}))
        (raw / f"{vid}.json3").write_text(json.dumps(json3([(5, "forgiveness is a gift")])))
    db = tmp_path / "s.db"
    index.build(db, raw, verbose=False)
    con = search.connect(db)
    ids = {r[0] for r in con.execute("SELECT id FROM videos")}
    assert ids == {"SERMON00001", "SERVICE0002", "SPECIAL0001"}
    dates = dict(con.execute("SELECT id, date FROM videos"))
    assert dates["SERMON00001"] == "2026-10-18" and dates["SERVICE0002"] == "2018-01-07"
