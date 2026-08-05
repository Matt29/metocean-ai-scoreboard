"""og.py: one 1200x630 PNG per station, no NaN/None ever rendered as a number."""

from __future__ import annotations

import json

from PIL import Image

from scoreboard import og


def _write_scores(tmp_path, stations):
    path = tmp_path / "scores.json"
    path.write_text(json.dumps({"schema_version": 1, "updated": "2026-08-05T00:00:00Z",
                                 "stations": stations}))
    return path


def test_run_writes_one_png_per_station_at_1200x630(tmp_path):
    scores_path = _write_scores(
        tmp_path,
        [{"id": "brest", "status": "ok", "mae_ia_7d": 0.0534, "mae_baseline_7d": 0.0728}],
    )
    out_dir = tmp_path / "out"
    written = og.run(scores_path, out_dir)

    assert written == [out_dir / "brest" / "og.png"]
    assert written[0].exists()
    with Image.open(written[0]) as img:
        assert img.size == (1200, 630)


def test_run_handles_station_without_scores(tmp_path):
    scores_path = _write_scores(tmp_path, [{"id": "cherbourg", "status": "missing"}])
    out_dir = tmp_path / "out"
    written = og.run(scores_path, out_dir)

    assert written[0].exists()
    with Image.open(written[0]) as img:
        assert img.size == (1200, 630)


def test_mae_line_falls_back_when_scores_missing_or_non_finite():
    # ASCII-folded: the default Pillow bitmap font has no accent glyphs.
    assert og._mae_line({"status": "missing"}, "m") == "Donnees en cours de collecte"
    assert og._mae_line({"mae_ia_7d": None, "mae_baseline_7d": 0.1}, "m") == (
        "Donnees en cours de collecte"
    )
    assert og._mae_line({"mae_ia_7d": float("nan"), "mae_baseline_7d": 0.1}, "m") == (
        "Donnees en cours de collecte"
    )


def test_mae_line_renders_values_with_unit():
    line = og._mae_line({"mae_ia_7d": 0.0534, "mae_baseline_7d": 0.0728}, "m")
    assert line == "MAE 7 j - IA 0.05 m vs prevision physique 0.07 m"


def test_run_no_stations_writes_nothing(tmp_path):
    scores_path = _write_scores(tmp_path, [])
    assert og.run(scores_path, tmp_path / "out") == []


def test_run_missing_scores_file_writes_nothing(tmp_path):
    assert og.run(tmp_path / "does-not-exist.json", tmp_path / "out") == []
