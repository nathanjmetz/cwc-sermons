#!/usr/bin/env bash
# One-step setup + run.  ./run.sh            -> start the site on http://localhost:8787
#                        ./run.sh ingest 15  -> pull the newest 15 sermons, then reindex
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -U -r requirements.txt
case "${1:-serve}" in
  ingest) .venv/bin/python -m cwc.ingest --limit "${2:-15}" && .venv/bin/python -m cwc.index ;;
  index)  .venv/bin/python -m cwc.index ;;
  serve)  .venv/bin/python -m cwc.server ;;
  *) echo "usage: ./run.sh [serve|ingest N|index]"; exit 1 ;;
esac
