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


def _tide_obs_df(start, date_end, value=2.0):
    """Hourly obs spanning the whole requested window — the harmonic fit floor
    (MIN_TIDE_FIT_DAYS = 30, real lookback = TIDE_FIT_LOOKBACK_DAYS = 90) needs
    the mock to actually reflect the window daily.py asks for, not a fixed
    handful of days."""
    idx = pd.date_range(start, date_end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"level": np.full(len(idx), value)}, index=idx)


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
        lambda station, start, date_end=None: _tide_obs_df(start, date_end),
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
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

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

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
    assert not (tmp_path / "wave-a" / "latest.json").exists()
    assert (tmp_path / "tide-b" / "latest.json").exists()


def test_failing_station_gets_a_missing_history_entry(tmp_path, patched_sources):
    def _boom(station, start):
        raise SourceError(station.id, "candhis 429")

    patched_sources.setattr(daily, "fetch_wave_obs", _boom)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    assert history["days"][-1] == {"date": RUN_DATE.isoformat(), "status": "missing"}


def test_second_run_scores_the_first_runs_predictions(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    next_date = date(2026, 7, 31)

    daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored_day = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored_day["status"] == "ok"
    assert "mae_ia" in scored_day and "mae_baseline" in scored_day

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 1


def test_gate_failing_station_is_listed_but_never_gets_predictions(tmp_path, patched_sources):
    gate = {**GATE, "tide-b": {"pass": False, "weak": True}}
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=gate, archive_dir=tmp_path / "archive")

    assert "tide-b" not in summary
    assert not (tmp_path / "tide-b").exists()
    stations_payload = json.loads((tmp_path / "stations.json").read_text())
    tide_entry = next(s for s in stations_payload["stations"] if s["id"] == "tide-b")
    assert tide_entry["published"] is False
    assert tide_entry["weak"] is True


def test_wind_source_error_marks_station_missing_without_touching_prior_latest(
    tmp_path, patched_sources
):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    prior_bytes = (tmp_path / "wave-a" / "latest.json").read_bytes()

    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    patched_sources.setattr(daily, "fetch_wind_forecast", _wind_boom)
    next_date = date(2026, 7, 31)
    summary = daily.run(next_date, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert (tmp_path / "wave-a" / "latest.json").read_bytes() == prior_bytes


def test_missing_model_artifact_is_isolated_to_its_station(tmp_path, patched_sources):
    def _load(station_id, models_dir=None):
        if station_id == "wave-a":
            raise FileNotFoundError("no such file: wave-a.joblib")
        return _FakePipe()

    patched_sources.setattr(daily.model, "load", _load)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"


def test_arbitrary_inference_exceptions_do_not_escape_and_block_other_stations(
    tmp_path, patched_sources
):
    """sklearn/pandas/utide can raise plain ValueError/KeyError, not just SourceError."""

    def _boom(pipe, x):
        raise ValueError("input X contains NaN")

    patched_sources.setattr(daily.model, "predict", _boom)
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "missing"  # both use model.predict


def test_inference_failure_still_records_a_missing_history_day(tmp_path, patched_sources):
    """Résolution 3: a degraded/unavailable forcing must be a *visible* missing day,
    not a silent gap in history.json."""

    def _wind_boom(station, session=None):
        raise SourceError(station.id, "open-meteo 503")

    patched_sources.setattr(daily, "fetch_wind_forecast", _wind_boom)
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    assert history["days"][-1] == {"date": RUN_DATE.isoformat(), "status": "missing"}


def test_daily_run_archives_the_served_wind_forecast_for_every_published_station(
    tmp_path, patched_sources
):
    """Task A1: the wind forecast fed to the model must be kept, not thrown away,
    so a future retrain can use real ARPEGE instead of ERA5 hindsight."""
    archive_dir = tmp_path / "archive"

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)

    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert set(df["station_id"]) == {"wave-a", "tide-b"}
    assert set(df.columns) == {
        "station_id", "issued", "valid_time", "lead_h", "wind_u10", "wind_v10", "source",
    }
    assert (df["source"] == "meteofrance_arpege_europe").all()
    assert df["lead_h"].min() >= 1


def test_rerunning_the_same_date_does_not_duplicate_archived_forecast_rows(
    tmp_path, patched_sources
):
    archive_dir = tmp_path / "archive"

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)
    n_first = len(pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet"))

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)  # same date again

    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert len(df) == n_first


def test_a_station_whose_inference_fails_is_not_archived(tmp_path, patched_sources):
    """Discriminant case: only wave-a's fetch fails, tide-b still publishes and
    archives — an implementation that never archives anything must not pass this."""
    real_fetch = daily.fetch_wind_forecast

    def _wind_boom(station, session=None):
        if station.id == "wave-a":
            raise SourceError(station.id, "open-meteo 503")
        return real_fetch(station, session)

    patched_sources.setattr(daily, "fetch_wind_forecast", _wind_boom)
    archive_dir = tmp_path / "archive"

    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=archive_dir)

    assert summary["wave-a"]["status"] == "missing"
    assert summary["tide-b"]["status"] == "ok"
    df = pd.read_parquet(archive_dir / f"{RUN_DATE.isoformat()}.parquet")
    assert set(df["station_id"]) == {"tide-b"}


def test_archiving_failure_does_not_fail_the_run(tmp_path, patched_sources, monkeypatch, caplog):
    """The scoreboard publish must survive a broken archive write (e.g. a full
    disk, a permissions error) — visible in logs, never in the run's outcome."""
    monkeypatch.setattr(
        daily.archive, "write_day",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )

    with caplog.at_level("WARNING", logger="scoreboard.daily"):
        summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["wave-a"]["status"] == "ok"
    assert (tmp_path / "wave-a" / "latest.json").exists()
    assert any(
        "archiving served wind forecast failed" in record.message for record in caplog.records
    )


def test_rerunning_the_same_date_does_not_invent_a_scored_day(tmp_path, patched_sources):
    """Regression for the bug the reviewer reproduced: re-running `--date` must
    never score a station's own just-published latest.json against itself."""
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    assert not (tmp_path / "wave-a" / "history.json").exists()

    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")  # same date again

    assert not (tmp_path / "wave-a" / "history.json").exists()  # still no invented day
    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 0


def test_scored_day_carries_n_points_and_max_lead_h(tmp_path, patched_sources):
    daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")
    daily.run(date(2026, 7, 31), tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    history = json.loads((tmp_path / "wave-a" / "history.json").read_text())
    scored_day = next(d for d in history["days"] if d["date"] == RUN_DATE.isoformat())
    assert scored_day["n_points"] == len(scored_day["series"])
    assert scored_day["max_lead_h"] >= 1


def test_harmonic_fit_below_30_days_of_tide_obs_marks_the_station_missing(
    tmp_path, patched_sources
):
    """Blocker 1 regression: a short tide history must never silently fit a
    degenerate harmonic baseline (utide can't separate M2/S2/N2 on a few days)."""
    patched_sources.setattr(
        daily, "fetch_tide_obs",
        lambda station, start, date_end=None: _tide_obs_df(date_end - pd.Timedelta(days=10), date_end),
    )
    summary = daily.run(RUN_DATE, tmp_path, stations=STATIONS, gate=GATE, archive_dir=tmp_path / "archive")

    assert summary["tide-b"]["status"] == "missing"
    assert not (tmp_path / "tide-b" / "latest.json").exists()
