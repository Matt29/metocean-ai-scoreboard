"""Backfill orchestration: fetch each source's deep history once, replay only the
missing days offline (résolution 1) — never re-fetch per replayed day."""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from scoreboard import backfill, daily
from scoreboard.config import Station

TODAY = date(2026, 7, 30)  # backfill replays up to (but not including) TODAY
YESTERDAY = TODAY - timedelta(days=1)

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
    def predict(self, x):
        return np.zeros(len(x))


def _hourly(start, end, value):
    idx = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    return pd.Series(float(value), index=idx)


def _wave_obs_df(start, value=1.3):
    idx = pd.date_range(start, TODAY, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"hs": np.full(len(idx), value), "tp": np.full(len(idx), 8.0)}, index=idx)


def _tide_obs_df(start, date_end, value=2.0):
    idx = pd.date_range(start, date_end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"level": np.full(len(idx), value)}, index=idx)


def _mfwam_baseline(start, end, value=1.0):
    idx = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"hs_baseline": np.full(len(idx), value)}, index=idx)


def _wind_df(station, date_start, date_end, session=None):
    idx = pd.date_range(date_start, date_end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"wind_u10": np.full(len(idx), 3.0), "wind_v10": np.full(len(idx), -2.0)}, index=idx)


@pytest.fixture
def calls():
    """Counts *deep* fetch invocations — must stay at 1 per source regardless of
    how many days are missing (the whole point of résolution 1)."""
    return {"candhis": 0, "tide": 0, "mfwam": 0, "wind": 0}


@pytest.fixture
def patched_sources(monkeypatch, calls):
    def _candhis(station, start):
        calls["candhis"] += 1
        return _wave_obs_df(start)

    def _tide(station, start, date_end=None):
        calls["tide"] += 1
        return _tide_obs_df(start, date_end)

    def _mfwam(stations, run_date, lookback_days=1, horizon_days=3):
        calls["mfwam"] += 1
        start = pd.Timestamp(run_date, tz="UTC") - pd.Timedelta(days=lookback_days)
        end = pd.Timestamp(run_date, tz="UTC") + pd.Timedelta(days=horizon_days)
        return {"wave-a": _mfwam_baseline(start, end)}

    def _wind(station, date_start, date_end, session=None):
        calls["wind"] += 1
        return _wind_df(station, date_start, date_end)

    monkeypatch.setattr(backfill, "fetch_wave_obs", _candhis)
    monkeypatch.setattr(backfill, "fetch_tide_obs", _tide)
    monkeypatch.setattr(backfill, "fetch_wave_forecast", _mfwam)
    monkeypatch.setattr(backfill, "fetch_wind_history", _wind)
    monkeypatch.setattr(daily.model, "load", lambda station_id, models_dir=None: _FakePipe())
    return monkeypatch


def _history_days(tmp_path, station_id):
    path = tmp_path / station_id / "history.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["days"]


def _seed_history(tmp_path, station_id, dates, backfilled=False):
    for d in dates:
        entry = {
            "date": d.isoformat(),
            "status": "ok",
            "series": [],
            "mae_ia": 0.1,
            "mae_baseline": 0.2,
            "n_points": 1,
            "max_lead_h": 1,
        }
        if backfilled:
            entry["backfilled"] = True
        from scoreboard import publish
        publish.upsert_history(tmp_path, station_id, entry)


def test_only_the_missing_day_is_replayed_chronologically_no_duplicate(tmp_path, patched_sources, calls):
    since = YESTERDAY - timedelta(days=5)  # J-5
    present = [
        since,
        since + timedelta(days=1),
        since + timedelta(days=2),
        since + timedelta(days=4),
        YESTERDAY,
    ]  # since+3 missing
    _seed_history(tmp_path, "wave-a", present)
    pre_existing = _history_days(tmp_path, "wave-a")

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    missing_day = since + timedelta(days=3)
    assert summary["wave-a"] == [missing_day.isoformat()]

    days = _history_days(tmp_path, "wave-a")
    assert len(days) == len(pre_existing) + 1
    replayed = next(d for d in days if d["date"] == missing_day.isoformat())
    assert replayed["backfilled"] is True
    # Pre-existing days untouched (still no "backfilled" key, same content).
    for d in pre_existing:
        untouched = next(x for x in days if x["date"] == d["date"])
        assert untouched == d

    # Only ONE deep fetch per source, however many days were replayed.
    assert calls["candhis"] == 1
    assert calls["mfwam"] == 1


def test_idempotent_rerun_does_not_duplicate_or_rewrite(tmp_path, patched_sources):
    since = YESTERDAY - timedelta(days=2)
    backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)
    days_after_first = _history_days(tmp_path, "wave-a")

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    assert summary["wave-a"] == []  # nothing left to replay
    assert _history_days(tmp_path, "wave-a") == days_after_first


def test_empty_history_backfills_every_day_in_range(tmp_path, patched_sources):
    since = YESTERDAY - timedelta(days=2)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    expected = [(since + timedelta(days=i)).isoformat() for i in range(3)]
    assert summary["wave-a"] == expected
    assert summary["tide-b"] == expected
    days = _history_days(tmp_path, "wave-a")
    assert all(d["backfilled"] is True for d in days)

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days_backfilled"] == 3
    assert row["n_days"] == 3


def test_gate_failing_station_is_never_backfilled(tmp_path, patched_sources):
    since = YESTERDAY - timedelta(days=2)
    gate = {**GATE, "tide-b": {"pass": False, "weak": True}}

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=gate)

    assert "tide-b" not in summary
    assert not (tmp_path / "tide-b").exists()


def test_no_missing_days_means_no_deep_fetch_at_all(tmp_path, patched_sources, calls):
    since = YESTERDAY - timedelta(days=1)
    _seed_history(tmp_path, "wave-a", [since, YESTERDAY])
    _seed_history(tmp_path, "tide-b", [since, YESTERDAY])

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    assert summary["wave-a"] == []
    assert summary["tide-b"] == []
    assert calls["candhis"] == 0
    assert calls["tide"] == 0
    assert calls["mfwam"] == 0
