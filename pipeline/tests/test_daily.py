"""Daily orchestration: fetch, score yesterday, infer today, publish — one station's
SourceError must never take the others down with it."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from scoreboard import daily
from scoreboard.config import Station
from scoreboard.sources import SourceError

RUN_DATE = date(2026, 7, 30)

WAVE = Station(id="wave-a", name="Wave A", kind="wave", lat=48.0, lon=-4.0,
                source="candhis", source_id="0001", baseline="mfwam")
TIDE = Station(id="tide-b", name="Tide B", kind="tide", lat=48.4, lon=-4.5,
                source="shom", source_id="0002", baseline="harmonic")
STATIONS = [WAVE, TIDE]
GATE = {
    "wave-a": {"pass": True, "weak": False},
    "tide-b": {"pass": True, "weak": False},
}


class _FakePipe:
    """Predicts a constant residual — enough to exercise the plumbing."""

    def predict(self, x):
        return np.zeros(len(x))


def _hourly(start, periods, value):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.Series(float(value), index=idx)


def _wave_obs_df(start, periods, value=1.3):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"hs": np.full(periods, value), "tp": np.full(periods, 8.0)}, index=idx)


def _tide_obs_df(start, periods, value=2.0):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"level": np.full(periods, value)}, index=idx)


def _mfwam_baseline(start, periods, value=1.0):
    idx = pd.date_range(start, periods=periods, freq="1h", tz="UTC")
    return pd.DataFrame({"hs_baseline": np.full(periods, value)}, index=idx)


def _wind_df(station, session=None):
    idx = pd.date_range("2026-07-25", periods=24 * 10, freq="1h", tz="UTC")
    return pd.DataFrame({"wind_u10": np.full(len(idx), 3.0), "wind_v10": np.full(len(idx), -2.0)}, index=idx)


@pytest.fixture
def patched_sources(monkeypatch):
    """Every network/model call replaced — daily.py orchestration only."""
    monkeypatch.setattr(
        daily, "fetch_wave_obs",
        lambda station, start: _wave_obs_df(start, 24 * 6),
    )
    monkeypatch.setattr(
        daily, "fetch_tide_obs",
        lambda station, start, date_end=None: _tide_obs_df(start, 24 * 6),
    )
    monkeypatch.setattr(
        daily, "fetch_wave_forecast",
        lambda stations, run_date, lookback_days=1, horizon_days=3: {
            "wave-a": _mfwam_baseline(pd.Timestamp(run_date, tz="UTC") - pd.Timedelta(days=1), 24 * 4)
        },
    )
    monkeypatch.setattr(daily, "fetch_wind_forecast", _wind_df)
    monkeypatch.setattr(daily.model, "load", lambda station_id, models_dir=None: _FakePipe())
    return monkeypatch


def test_first_run_publishes_latest_for_every_passing_station(tmp_path, patched_sources):
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)

    assert summary["wave-a"]["status"] == "ok"
    assert summary["tide-b"]["status"] == "ok"
    for station_id in ["wave-a", "tide-b"]:
        payload = json.loads((tmp_path / station_id / "latest.json").read_text())
        assert payload["schema_version"] == 1
        assert len(payload["series"]) > 0
        assert "ia" in payload["series"][0] and "baseline" in payload["series"][0]

    stations_payload = json.loads((tmp_path / "stations.json").read_text())
    assert {s["id"] for s in stations_payload["stations"]} == {"wave-a", "tide-b"}


def test_a_source_error_on_one_station_does_not_block_the_others(tmp_path, patched_sources):
    def _boom(station, start):
        raise SourceError(station.id, "candhis 429")

    patched_sources.setattr(daily, "fetch_wave_obs", _boom)

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
    assert not (tmp_path / "wave-a" / "latest.json").exists()
    assert (tmp_path / "tide-b" / "latest.json").exists()


def test_failing_station_gets_a_missing_history_entry(tmp_path, patched_sources):
    def _boom(station, start):
        raise SourceError(station.id, "candhis 429")

    patched_sources.setattr(daily, "fetch_wave_obs", _boom)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    assert history["days"][-1] == {"date": RUN_DATE.isoformat(), "status": "missing"}


def test_second_run_scores_the_first_runs_predictions(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)
    next_date = date(2026, 7, 31)

    daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE)

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored_day = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored_day["status"] == "ok"
    assert "mae_ia" in scored_day and "mae_baseline" in scored_day

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 1


def test_gate_failing_station_is_listed_but_never_gets_predictions(tmp_path, patched_sources):
    gate = {**GATE, "tide-b": {"pass": False, "weak": True}}
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=gate)

    assert "tide-b" not in summary
    assert not (tmp_path / "tide-b").exists()
    stations_payload = json.loads((tmp_path / "stations.json").read_text())
    tide_entry = next(s for s in stations_payload["stations"] if s["id"] == "tide-b")
    assert tide_entry["published"] is False
    assert tide_entry["weak"] is True


def test_wind_source_error_marks_station_missing_without_touching_prior_latest(
    tmp_path, patched_sources
):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)
    prior_bytes = (tmp_path / "wave-a" / "latest.json").read_bytes()

    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    patched_sources.setattr(daily, "fetch_wind_forecast", _wind_boom)
    next_date = date(2026, 7, 31)
    summary = daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE)

    assert summary["wave-a"]["status"] == "missing"
    assert (tmp_path / "wave-a" / "latest.json").read_bytes() == prior_bytes


def test_missing_model_artifact_is_isolated_to_its_station(tmp_path, patched_sources):
    def _load(station_id, models_dir=None):
        if station_id == "wave-a":
            raise FileNotFoundError("no such file: wave-a.joblib")
        return _FakePipe()

    patched_sources.setattr(daily.model, "load", _load)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE)

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
