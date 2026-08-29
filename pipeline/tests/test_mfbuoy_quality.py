"""Contrat public du rapport qualité des observations de bouées."""

from __future__ import annotations

from datetime import date
import json

import pandas as pd

from scoreboard.sources import mfbuoy


NOW = pd.Timestamp("2026-08-29T12:00:00Z")
WAVE_IDS = {"6101001", "6101002"}


def _hourly_rows(
    buoy_id: str,
    *,
    end: pd.Timestamp = NOW,
    periods: int = 24,
    hs: float | None = 1.2,
) -> list[dict]:
    return [
        {
            "geo_id_wmo": buoy_id,
            "validity_time": timestamp.isoformat(),
            "haut_vag": hs,
        }
        for timestamp in pd.date_range(end=end, periods=periods, freq="h")
    ]


def test_quality_report_is_healthy_for_fresh_complete_wave_buoys():
    obs = pd.DataFrame(_hourly_rows("6101001") + _hourly_rows("6101002"))

    report = mfbuoy.quality_report(obs, now=NOW, wave_ids=WAVE_IDS)

    assert report.latest_timestamp == NOW
    assert report.freshness == pd.Timedelta(0)
    assert report.is_fresh is True
    assert {item.buoy_id: item.hs_completeness for item in report.buoys} == {
        "6101001": 1.0,
        "6101002": 1.0,
    }
    assert report.is_healthy is True


def test_read_archived_buoy_obs_filters_source_and_inclusive_calendar_days(tmp_path):
    pd.DataFrame(
        [
            {
                "geo_id_wmo": "6101001",
                "validity_time": "2026-08-01T23:00:00+00:00",
                "haut_vag": 0.8,
            },
            {
                "geo_id_wmo": "6101001",
                "validity_time": "2026-08-02T00:00:00+00:00",
                "haut_vag": 1.0,
            },
            {
                "geo_id_wmo": "6101002",
                "validity_time": "2026-08-02T12:00:00+00:00",
                "haut_vag": 9.9,
            },
            {
                "geo_id_wmo": "6101001",
                "validity_time": "2026-08-02T23:00:00+00:00",
                "haut_vag": None,
            },
        ]
    ).to_parquet(tmp_path / "2026-08-02.parquet")
    pd.DataFrame(
        [
            {
                "geo_id_wmo": "6101001",
                "validity_time": "2026-08-03T00:00:00+00:00",
                "haut_vag": 1.4,
            },
        ]
    ).to_parquet(tmp_path / "2026-08-03.parquet")

    obs = mfbuoy.read_archived_buoy_obs(
        tmp_path,
        "6101001",
        date(2026, 8, 2),
        date(2026, 8, 2),
    )

    assert obs.index.name == "time"
    assert str(obs.index.tz) == "UTC"
    assert list(obs.index) == [
        pd.Timestamp("2026-08-02T00:00:00Z"),
        pd.Timestamp("2026-08-02T23:00:00Z"),
    ]
    assert list(obs.columns) == ["hs"]
    assert obs["hs"].iloc[0] == 1.0
    assert pd.isna(obs["hs"].iloc[1])  # aucune plausibilité/complétude appliquée ici


def test_quality_report_keeps_missing_known_wave_buoys_and_excludes_non_wave_buoys():
    rows = _hourly_rows("6101001", periods=19)
    rows += _hourly_rows("6101035", hs=None)  # BOUEE_SARDAIGNE: fraîche mais non-wave
    obs = pd.DataFrame(rows)

    report = mfbuoy.quality_report(obs, now=NOW, wave_ids=WAVE_IDS)

    by_id = {item.buoy_id: item for item in report.buoys}
    assert set(by_id) == WAVE_IDS
    assert by_id["6101001"].hs_completeness == 19 / 24
    assert by_id["6101002"].hs_completeness == 0.0
    assert report.failing_buoy_ids == ("6101001", "6101002")
    assert report.has_collective_hs_failure is True
    assert report.is_healthy is False


def test_quality_report_rejects_a_timestamp_from_the_future():
    obs = pd.DataFrame(_hourly_rows("6101001", end=NOW + pd.Timedelta(hours=1), periods=24))

    report = mfbuoy.quality_report(obs, now=NOW, wave_ids={"6101001"})

    assert report.freshness == -pd.Timedelta(hours=1)
    assert report.is_fresh is False


def test_quality_rendering_warns_without_raising_and_produces_a_job_summary():
    obs = pd.DataFrame(_hourly_rows("6101001", end=NOW - pd.Timedelta(hours=4), periods=18))
    report = mfbuoy.quality_report(obs, now=NOW, wave_ids=WAVE_IDS)

    warnings = mfbuoy.quality_warnings(report)
    summary = mfbuoy.quality_summary(report)

    assert any("fraîcheur" in warning and "4h" in warning for warning in warnings)
    assert any("panne collective" in warning for warning in warnings)
    assert any("6101001" in warning and "75.0%" in warning for warning in warnings)
    assert any("6101002" in warning and "0.0%" in warning for warning in warnings)
    assert all(warning.startswith("::warning title=Qualité bouées::") for warning in warnings)
    assert "## Qualité des bouées Météo-France" in summary
    assert "Seuil fraîcheur : 3h" in summary
    assert "Seuil Hs : 80%" in summary
    assert "| 6101002 | 0/24 | 0.0% | ⚠️ |" in summary


def test_quality_command_emits_annotations_and_appends_github_summary(tmp_path, capsys):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    pd.DataFrame(_hourly_rows("6101001", periods=18)).to_parquet(archive_dir / "2026-08-29.parquet")
    catalog = tmp_path / "buoys.json"
    catalog.write_text(
        json.dumps(
            {
                "buoys": [
                    {"id": "6101001", "wave": True},
                    {"id": "6101035", "wave": False},
                ]
            }
        )
    )
    github_summary = tmp_path / "summary.md"

    status = mfbuoy.quality_main(
        [
            "--archive-dir",
            str(archive_dir),
            "--catalog",
            str(catalog),
            "--summary",
            str(github_summary),
            "--now",
            NOW.isoformat(),
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "::warning title=Qualité bouées::6101001 Hs 75.0%" in output
    assert "BOUEE_SARDAIGNE" not in output
    assert "## Qualité des bouées Météo-France" in github_summary.read_text()


def test_quality_command_remembers_historical_wave_ids_during_a_current_outage(tmp_path, capsys):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    pd.DataFrame(
        [
            {
                "geo_id_wmo": "6101001",
                "validity_time": "2026-08-20T12:00:00+00:00",
                "haut_vag": 1.1,
            }
        ]
    ).to_parquet(archive_dir / "2026-08-20.parquet")
    pd.DataFrame(_hourly_rows("6101001", hs=None)).to_parquet(archive_dir / "2026-08-29.parquet")
    catalog = tmp_path / "buoys.json"
    catalog.write_text(json.dumps({"buoys": [{"id": "6101001", "wave": False}]}))

    status = mfbuoy.quality_main(
        [
            "--archive-dir",
            str(archive_dir),
            "--catalog",
            str(catalog),
            "--now",
            NOW.isoformat(),
        ]
    )

    output = capsys.readouterr().out
    assert status == 0
    assert "panne collective Hs possible" in output
    assert "6101001 Hs 0.0%" in output
    assert "aucun identifiant de bouée wave connu" not in output
