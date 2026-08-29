"""Contrat public des séries d'observations des bouées Météo-France."""

from __future__ import annotations

import json

import pandas as pd

from scoreboard import archive, archive_obs, publish
from scoreboard.sources import mfbuoy


def _obs(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_write_buoy_series_publishes_latest_observation_with_explicit_nulls(tmp_path):
    observations = _obs(
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-08-03T07:00:00+00:00",
            "haut_vag": 1.2,
            "per_moy_vag": 5.0,
            "dir_vag": 190.0,
        },
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-08-03T08:00:00+00:00",
            "haut_vag": None,
            "per_moy_vag": None,
            "dir_vag": None,
        },
    )

    publish.write_buoy_series(tmp_path, observations)

    assert json.loads((tmp_path / "buoys" / "6101001" / "latest.json").read_text()) == {
        "schema_version": 1,
        "buoy": "6101001",
        "updated": "2026-08-03T08:00:00Z",
        "series": [
            {
                "t": "2026-08-03T08:00:00Z",
                "hs": None,
                "period": None,
                "direction": None,
            }
        ],
    }


def test_history_is_sorted_deduplicated_and_bounded_to_trailing_30_days(tmp_path):
    observations = _obs(
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-07-04T07:59:59+00:00",
            "haut_vag": 9.9,
            "per_moy_vag": 9.0,
            "dir_vag": 99.0,
        },
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-08-03T08:00:00+00:00",
            "haut_vag": 1.4,
            "per_moy_vag": 5.2,
            "dir_vag": 192.0,
        },
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-07-04T08:00:00+00:00",
            "haut_vag": 1.0,
            "per_moy_vag": 4.0,
            "dir_vag": 180.0,
        },
        {
            "geo_id_wmo": "6101001",
            "validity_time": "2026-08-03T08:00:00+00:00",
            "haut_vag": 1.5,
            "per_moy_vag": 5.3,
            "dir_vag": 193.0,
        },
    )

    publish.write_buoy_series(tmp_path, observations)
    first = (tmp_path / "buoys" / "6101001" / "history.json").read_bytes()
    publish.write_buoy_series(tmp_path, observations)

    payload = json.loads(first)
    assert payload == {
        "schema_version": 1,
        "buoy": "6101001",
        "updated": "2026-08-03T08:00:00Z",
        "since": "2026-07-04T08:00:00Z",
        "series": [
            {"t": "2026-07-04T08:00:00Z", "hs": 1.0, "period": 4.0, "direction": 180.0},
            {"t": "2026-08-03T08:00:00Z", "hs": 1.5, "period": 5.3, "direction": 193.0},
        ],
    }
    assert (tmp_path / "buoys" / "6101001" / "history.json").read_bytes() == first
    assert list((tmp_path / "buoys" / "6101001").glob("*.tmp")) == []


def test_archive_run_publishes_the_complete_merged_archive(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    out_dir = tmp_path / "data"
    old = _obs(
        {
            "geo_id_wmo": "6101001",
            "name": "BOUEE_AZUR",
            "lat": 43.36,
            "lon": 7.83,
            "validity_time": "2026-08-01T08:00:00+00:00",
            "haut_vag": 1.0,
            "per_moy_vag": 4.0,
            "dir_vag": 180.0,
        }
    )
    current = _obs(
        {
            "geo_id_wmo": "6101001",
            "name": "BOUEE_AZUR",
            "lat": 43.36,
            "lon": 7.83,
            "validity_time": "2026-08-03T08:00:00+00:00",
            "haut_vag": 1.2,
            "per_moy_vag": 5.0,
            "dir_vag": 190.0,
        }
    )
    archive.write_obs_days(archive_dir, old, key=mfbuoy.KEY_COLUMNS)
    monkeypatch.setattr(mfbuoy, "fetch_buoy_obs", lambda: current)

    archive_obs.run(archive_dir, out_dir)

    history = json.loads((out_dir / "buoys" / "6101001" / "history.json").read_text())
    assert [point["t"] for point in history["series"]] == [
        "2026-08-01T08:00:00Z",
        "2026-08-03T08:00:00Z",
    ]
