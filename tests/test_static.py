"""Static export: every word kept exactly once, files laid out for engine.js."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cwc import export_static
from test_pipeline import build, SERMONS


def test_export(tmp_path):
    build(tmp_path)
    site = tmp_path / "docs"
    export_static.export(tmp_path / "s.db", site)
    tx = json.loads((site / "data/tx/AAAAAAAAAAA.json").read_text())
    expected = " ".join(t for _, t in SERMONS["AAAAAAAAAAA"][3])
    assert tx["w"] == expected
    assert len(tx["t"]) == len(tx["w"].split())
    terms = json.loads((site / "data/terms/fo.json").read_text())
    assert terms["forgive"][0][0] in (0, 1, 2)
    meta = json.loads((site / "data/meta.json").read_text())
    assert len(meta["videos"]) == 3 and meta["books"]["ezekial"] == "Ezekiel"
    assert (site / "data/refs/ezekiel.json").exists()
    assert '<script src="engine.js"></script>' in (site / "index.html").read_text()
