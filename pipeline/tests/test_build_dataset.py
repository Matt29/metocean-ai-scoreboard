"""build_dataset.py wave path: one raw multi-model parquet per station."""

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from scoreboard.config import Station
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_dataset.py"


def _load_build_dataset():
    spec = importlib.util.spec_from_file_location("build_dataset", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_dataset"] = module
    spec.loader.exec_module(module)
    return module


STATION = Station(
    id="pierres-noires",
    name="Les Pierres Noires",
    kind="wave",
    lat=48.29,
    lon=-4.97,
    source="candhis",
    source_id="02911",
    baseline="marine-best",
)
MFBUOY_STATION = Station(
    id="gascogne-bouee",
    name="Bouée Gascogne",
    kind="wave",
    lat=45.22,
    lon=-4.97,
    source="mfbuoy",
    source_id="6200001",
    baseline="marine-best",
    active=False,
)


def _hourly(cols, start, periods, value):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({c: value for c in cols}, index=idx)


def test_build_wave_writes_one_raw_parquet_per_station(tmp_path):
    bd = _load_build_dataset()
    bd.OUT_DIR = tmp_path

    obs = pd.DataFrame(
        {"hs": [1.0, 2.0, 3.0]},
        index=pd.date_range("2026-01-01", periods=3, freq="30min", tz="UTC"),
    )
    waves = _hourly(MODEL_COLUMNS, "2026-01-01", 4, 1.5)
    winds = _hourly(MULTI_FORCING_COLUMNS, "2026-01-01", 4, 0.5)

    with (
        patch.object(bd, "fetch_wave_obs", return_value=obs) as m_obs,
        patch.object(bd, "fetch_wave_models_history", return_value=waves) as m_waves,
        patch.object(bd, "fetch_wind_models_history", return_value=winds) as m_winds,
    ):
        start, end = date(2026, 1, 1), date(2026, 1, 2)
        out = bd.build_wave([STATION], start, end)

        m_obs.assert_called_once_with(STATION, start)
        m_waves.assert_called_once_with(STATION, start, end)
        m_winds.assert_called_once_with(STATION, start, end)

    raw = out["pierres-noires"]
    expected_cols = ["hs"] + MODEL_COLUMNS + MULTI_FORCING_COLUMNS
    assert list(raw.columns) == expected_cols
    assert raw.index.name == "time" or raw.index.tz is not None

    written = pd.read_parquet(tmp_path / "pierres-noires_raw.parquet")
    assert list(written.columns) == expected_cols
    assert str(written.index.tz) == "UTC"
    # obs resampled 30min -> 1h before the join
    assert written["hs"].notna().sum() == 2


def test_build_wave_reads_mfbuoy_obs_from_archive_without_api_fetch(tmp_path):
    bd = _load_build_dataset()
    bd.OUT_DIR = tmp_path / "train"
    bd.OUT_DIR.mkdir()
    archive = tmp_path / "obs"
    archive.mkdir()
    pd.DataFrame(
        {
            "geo_id_wmo": ["6200001", "6100001", "6200001"],
            "validity_time": [
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T01:00:00+00:00",
            ],
            "haut_vag": [1.2, 9.9, 1.4],
        }
    ).to_parquet(archive / "2026-01-01.parquet")
    waves = _hourly(MODEL_COLUMNS, "2026-01-01", 2, 1.5)
    winds = _hourly(MULTI_FORCING_COLUMNS, "2026-01-01", 2, 0.5)

    with (
        patch.object(bd, "fetch_wave_obs") as m_candhis,
        patch.object(bd, "fetch_wave_models_history", return_value=waves),
        patch.object(bd, "fetch_wind_models_history", return_value=winds),
    ):
        out = bd.build_wave(
            [MFBUOY_STATION],
            date(2026, 1, 1),
            date(2026, 1, 2),
            obs_archive_dir=archive,
        )

    m_candhis.assert_not_called()
    assert out["gascogne-bouee"]["hs"].dropna().tolist() == [1.2, 1.4]


def test_cli_only_selects_inactive_pilot_with_explicit_flag(tmp_path, monkeypatch):
    bd = _load_build_dataset()
    bd.OUT_DIR = tmp_path
    selected = []
    monkeypatch.setattr(bd, "load_env", lambda: None)
    monkeypatch.setattr(
        bd,
        "build_wave",
        lambda stations, start, end: selected.append({s.id for s in stations}),
    )

    monkeypatch.setattr(sys, "argv", ["build_dataset.py", "--kind", "wave", "--days", "1"])
    assert bd.main() == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_dataset.py",
            "--kind",
            "wave",
            "--days",
            "1",
            "--station",
            "gascogne-bouee",
            "--include-pilots",
        ],
    )
    assert bd.main() == 0

    assert "gascogne-bouee" not in selected[0]
    assert selected[1] == {"gascogne-bouee"}


def test_cli_rejects_inactive_station_without_pilot_opt_in(tmp_path, monkeypatch):
    bd = _load_build_dataset()
    bd.OUT_DIR = tmp_path
    monkeypatch.setattr(bd, "load_env", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_dataset.py", "--kind", "wave", "--station", "gascogne-bouee"],
    )

    with pytest.raises(SystemExit, match="2"):
        bd.main()
