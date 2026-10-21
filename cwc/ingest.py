"""Download video metadata and timestamped captions from the YouTube channel.

Raw files land in data/raw/ (one .info.json and one .json3 per video) so the
index can be rebuilt without hitting YouTube again.

    python -m cwc.ingest --limit 15
    python -m cwc.ingest --channel https://www.youtube.com/@SomeChannel --limit 50
    python -m cwc.ingest --video https://www.youtube.com/watch?v=XXXXXXXXXXX
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CONFIG = ROOT / "config.json"

KEEP_INFO = ["id", "title", "description", "upload_date", "release_timestamp", "timestamp",
             "duration", "channel", "channel_url", "uploader", "webpage_url", "thumbnail",
             "live_status", "chapters", "tags"]


def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def ydl(extra=None, cookies_from_browser=None):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def list_channel(channel_url, tabs, min_minutes, cookies=None):
    """Flat list of videos across channel tabs, newest first, deduped."""
    seen, out = set(), []
    base = channel_url.rstrip("/")
    with ydl({"extract_flat": "in_playlist"}, cookies) as y:
        for tab in tabs:
            info = y.extract_info(f"{base}/{tab}", download=False)
            if not info:
                print(f"  (no {tab} tab)")
                continue
            for e in info.get("entries") or []:
                if not e or e.get("id") in seen:
                    continue
                dur = e.get("duration")
                if dur and dur < min_minutes * 60:
                    continue
                seen.add(e["id"])
                out.append(e)
            print(f"  {tab}: {len(info.get('entries') or [])} entries")
    return out


def pick_caption_track(info):
    """Prefer human-made English subtitles, then English auto-captions.

    Skip machine-translated tracks (URL has tlang=): YouTube's "en" auto list can hold
    translations from other-language auto tracks, which paraphrase and shift timing.
    """
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key) or {}
        langs = ["en-orig", "en", "en-US", "en-GB"]
        langs += [l for l in tracks if l.startswith("en") and l not in langs]
        for lang in langs:
            for f in tracks.get(lang) or []:
                if f.get("ext") == "json3" and "tlang=" not in f.get("url", ""):
                    return key, lang, f["url"]
    return None


def fetch_video(video_id, y, force=False):
    info_path = RAW / f"{video_id}.info.json"
    cap_path = RAW / f"{video_id}.json3"
    if info_path.exists() and cap_path.exists() and not force:
        return "cached"
    info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    if not info:
        return "unavailable"
    if info.get("live_status") in ("is_live", "is_upcoming"):
        return "live/upcoming"
    track = pick_caption_track(info)
    meta = {k: info.get(k) for k in KEEP_INFO}
    meta["caption_source"] = track[0] if track else None
    info_path.write_text(json.dumps(meta, indent=1))
    if not track:
        return "no captions"
    data = y.urlopen(track[2]).read()
    json.loads(data)  # sanity check before saving
    cap_path.write_bytes(data)
    return f"ok ({'manual' if track[0] == 'subtitles' else 'auto'} {track[1]})"


def main(argv=None):
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", default=cfg.get("channel_url"), help="YouTube channel URL")
    ap.add_argument("--video", action="append", default=[], help="Fetch specific video URL(s)/ids")
    ap.add_argument("--limit", type=int, default=15, help="How many videos (newest first). 0 = all")
    ap.add_argument("--tabs", default=",".join(cfg.get("tabs", ["streams", "videos"])))
    ap.add_argument("--min-minutes", type=float, default=cfg.get("min_minutes", 15),
                    help="Skip short clips (sermons/services are long)")
    ap.add_argument("--sleep", type=float, default=3.0, help="Seconds between videos (be gentle)")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="e.g. chrome or safari, if YouTube starts blocking requests")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    RAW.mkdir(parents=True, exist_ok=True)
    ids = [v.split("v=")[-1].split("&")[0].split("/")[-1] for v in a.video]
    if not ids:
        if not a.channel:
            sys.exit("No channel. Set channel_url in config.json or pass --channel.")
        print(f"Listing {a.channel} ...")
        entries = list_channel(a.channel, [t.strip() for t in a.tabs.split(",") if t.strip()],
                               a.min_minutes, a.cookies_from_browser)
        ids = [e["id"] for e in entries]
        print(f"Found {len(ids)} long-form videos")
        if a.limit:
            ids = ids[: a.limit]

    counts = {}
    with ydl({"writesubtitles": True, "writeautomaticsub": True}, a.cookies_from_browser) as y:
        for i, vid in enumerate(ids, 1):
            try:
                status = fetch_video(vid, y, a.force)
            except Exception as e:  # keep going; one bad video shouldn't stop the run
                status = f"error: {e}"
            counts[status.split(" ")[0]] = counts.get(status.split(" ")[0], 0) + 1
            print(f"[{i}/{len(ids)}] {vid}: {status}")
            if status != "cached" and i < len(ids):
                time.sleep(a.sleep + random.random())
    print("Done:", counts)
    print("Next: python -m cwc.index")


if __name__ == "__main__":
    main()
