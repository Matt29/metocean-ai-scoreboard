"""Backfill orchestration: fetch each source's deep history once, replay only the
missing days offline (résolution 1) — never re-fetch per replayed day.

Fakes below deliberately mirror the real sources' date semantics rather than
accepting any range handed to them (Task 9 review, blocker 1/4): `date_end` is
inclusive of the whole calendar day, exactly like the real Open-Meteo/REFMAR
APIs treat it, and every fake asserts it is never asked for a date beyond
`TODAY` — the exact HTTP 400 the reviewer reproduced against the live
Open-Meteo archive endpoint would otherwise go undetected here."""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from scoreboard import backfill, daily, publish
from scoreboard.config import Station
from scoreboard.features import FEATURE_COLUMNS, WAVE_FEATURE_COLUMNS
from scoreboard.sources import SourceError
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS

TODAY = date(2026, 7, 30)  # backfill replays up to (but not including) TODAY
YESTERDAY = TODAY - timedelta(days=1)

WAVE = Station(id="wave-a", name="Wave A", kind="wave", lat=48.0, lon=-4.0,
               source="candhis", source_id="0001", baseline="marine-best")
TIDE = Station(id="tide-b", name="Tide B", kind="tide", lat=48.4, lon=-4.5,
                source="shom", source_id="0002", baseline="harmonic")
STATIONS = [WAVE, TIDE]
GATE = {
    "wave-a": {"pass": True, "weak": False},
    "tide-b": {"pass": True, "weak": False},
}


BASELINE_MODEL = "ewam"  # not MODEL_COLUMNS[0]: the artefact must really be read
MODEL_HS = {col: 1.0 + i for i, col in enumerate(MODEL_COLUMNS)}


class _FakePipe:
    # Like every fitted sklearn estimator — `model.predict` reorders on it.
    def __init__(self, columns=FEATURE_COLUMNS):
        self.feature_names_in_ = np.asarray(columns)

    def predict(self, x):
        return np.zeros(len(x))


def _artifact(station_id, models_dir=None):
    if station_id == "tide-b":
        return {"model": _FakePipe(), "baseline_model": None, "feature_columns": FEATURE_COLUMNS}
    return {
        "model": _FakePipe(WAVE_FEATURE_COLUMNS),
        "baseline_model": BASELINE_MODEL,
        "feature_columns": WAVE_FEATURE_COLUMNS,
    }


def _wave_obs_df(start, value=1.3):
    idx = pd.date_range(start, TODAY, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"hs": np.full(len(idx), value), "tp": np.full(len(idx), 8.0)}, index=idx)


def _date_end_inclusive_index(start, date_end, freq="1h"):
    """`date_end` covers its whole calendar day (00:00..23:00) — the real
    REFMAR/Open-Meteo date-range semantics, not a bare exclusive cutoff."""
    return pd.date_range(start, date_end + timedelta(days=1), freq=freq, tz="UTC", inclusive="left")


def _tide_obs_df(start, date_end, value=2.0):
    idx = _date_end_inclusive_index(start, date_end)
    return pd.DataFrame({"level": np.full(len(idx), value)}, index=idx)


def _marine_df(date_start, date_end):
    """One Hs column per wave model over the requested (inclusive) window —
    same date semantics as the real Open-Meteo marine archive."""
    idx = _date_end_inclusive_index(date_start, date_end)
    return pd.DataFrame({col: np.full(len(idx), MODEL_HS[col]) for col in MODEL_COLUMNS}, index=idx)


def _wind_df(date_start, date_end, value=3.0):
    idx = _date_end_inclusive_index(date_start, date_end)
    return pd.DataFrame({"wind_u10": np.full(len(idx), value), "wind_v10": np.full(len(idx), -2.0)}, index=idx)


def _wind_models_df(date_start, date_end):
    idx = _date_end_inclusive_index(date_start, date_end)
    return pd.DataFrame(
        {col: np.full(len(idx), 3.0 if col.startswith("wind_u10") else -2.0) for col in MULTI_FORCING_COLUMNS},
        index=idx,
    )


@pytest.fixture
def calls():
    """Counts *deep* fetch invocations — must stay at 1 per source regardless of
    how many days are missing (the whole point of résolution 1) — and records
    the last date range each source was asked for, so a regression like
    blocker 1 (a future `end_date` sent to a real API) is visible in-process."""
    return {
        "candhis": 0, "tide": 0, "marine": 0, "wind": 0,
        "last_wind_range": None, "last_tide_range": None, "last_marine_range": None,
    }


@pytest.fixture
def patched_sources(monkeypatch, calls):
    def _candhis(station, start):
        calls["candhis"] += 1
        return _wave_obs_df(start)

    def _tide(station, start, date_end=None):
        calls["tide"] += 1
        calls["last_tide_range"] = (start, date_end)
        assert date_end <= TODAY, f"tide fetch requested a future end date: {date_end}"
        return _tide_obs_df(start, date_end)

    def _marine(station, date_start, date_end, session=None):
        calls["marine"] += 1
        calls["last_marine_range"] = (date_start, date_end)
        assert station.kind == "wave", "marine must never be fetched for a tide station"
        # Same blocker-1 regression check as the wind archive: Open-Meteo
        # rejects an `end_date` beyond its own current date.
        assert date_end <= TODAY, f"marine fetch requested a future end date: {date_end}"
        return _marine_df(date_start, date_end)

    def _wind(station, date_start, date_end, session=None):
        calls["wind"] += 1
        calls["last_wind_range"] = (date_start, date_end)
        # This is the exact regression check for blocker 1: the real
        # archive-api.open-meteo.com rejects `end_date` beyond its own
        # current date with HTTP 400 — verified live by the reviewer.
        assert date_end <= TODAY, f"wind fetch requested a future end date: {date_end}"
        return _wind_df(date_start, date_end)

    def _wind_models(station, date_start, date_end, session=None):
        calls["wind"] += 1
        calls["last_wind_range"] = (date_start, date_end)
        assert station.kind == "wave", "the multi-model wind is the wave path's forcing"
        assert date_end <= TODAY, f"wind fetch requested a future end date: {date_end}"
        return _wind_models_df(date_start, date_end)

    monkeypatch.setattr(backfill, "fetch_wave_obs", _candhis)
    monkeypatch.setattr(backfill, "fetch_tide_obs", _tide)
    monkeypatch.setattr(backfill, "fetch_wave_models_history", _marine)
    monkeypatch.setattr(backfill, "fetch_wind_history", _wind)
    monkeypatch.setattr(backfill, "fetch_wind_models_history", _wind_models)
    monkeypatch.setattr(daily.model, "load_artifact", _artifact)
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
        publish.upsert_history(tmp_path, station_id, entry)


def test_only_the_missing_day_is_replayed_chronologically_no_duplicate(tmp_path, patched_sources, calls):
    since = YESTERDAY - timedelta(days=5)  # J-5
    present = [
        since,
        since + timedelta(days=1),
        since + timedelta(days=2),
        since + timedelta(days=4),
        YESTERDAY,
    ]  # since+3 missing — well inside the window, full forcing coverage available
    _seed_history(tmp_path, "wave-a", present)
    _seed_history(tmp_path, "tide-b", present)  # same gap, so tide-b doesn't also do a full deep fetch
    pre_existing = _history_days(tmp_path, "wave-a")

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    missing_day = since + timedelta(days=3)
    assert summary["wave-a"] == [missing_day.isoformat()]

    days = _history_days(tmp_path, "wave-a")
    assert len(days) == len(pre_existing) + 1
    replayed = next(d for d in days if d["date"] == missing_day.isoformat())
    assert replayed["status"] == "ok"
    assert replayed["backfilled"] is True
    # Pre-existing days untouched (still no "backfilled" key, same content).
    for d in pre_existing:
        untouched = next(x for x in days if x["date"] == d["date"])
        assert untouched == d

    # Only ONE deep fetch per source *per station*, however many days were
    # replayed — every source is per-station now (marine included, unlike the
    # old single multi-station Copernicus subset).
    assert calls["candhis"] == 1
    assert calls["marine"] == 1
    assert calls["wind"] == 2  # wave-a + tide-b, one each


def test_idempotent_rerun_does_not_duplicate_or_rewrite(tmp_path, patched_sources, calls):
    """A window whose last day (`until`) is already present — so nothing left to
    replay is *actually* nothing left, not a day that will be retried anyway."""
    since = YESTERDAY - timedelta(days=2)
    _seed_history(tmp_path, "wave-a", [YESTERDAY])
    _seed_history(tmp_path, "tide-b", [YESTERDAY])

    backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)
    days_after_first = _history_days(tmp_path, "wave-a")
    assert all(d["status"] == "ok" for d in days_after_first)  # no structural gap in this window
    fetch_count_after_first = calls["wind"]

    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    assert summary["wave-a"] == []  # nothing left to replay
    assert _history_days(tmp_path, "wave-a") == days_after_first
    assert calls["wind"] == fetch_count_after_first  # no second deep fetch either


def test_empty_history_backfills_every_day_in_range_last_day_is_horizon_limited(
    tmp_path, patched_sources
):
    """`until` (yesterday) needs its sources through `t0+48h`, i.e. tomorrow
    06:00 — beyond what any archive can serve *today*. The two kinds hit that
    wall differently, and both behaviours are deliberate:

    * tide's harmonic baseline is *generated* over the full 48 h, so the forcing
      it needs is 15% short and `features`' coverage floor refuses the day —
      "missing", recovered by résolution 5's retry-on-"missing" (blocker 1);
    * wave's baseline is itself an archived field, so it simply stops at the
      same wall as the forcing: the day is issued "ok" over a *shorter* horizon
      rather than not at all. It is flagged `backfilled` and will not be retried
      (`_missing_dates` treats "ok" as done), so it stays permanently short —
      accepted: a verified 40 h day beats an absent one.
    """
    since = YESTERDAY - timedelta(days=2)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    expected = [(since + timedelta(days=i)).isoformat() for i in range(3)]
    assert summary["wave-a"] == expected  # all three were *attempted*
    assert summary["tide-b"] == expected

    days = {d["date"]: d for d in _history_days(tmp_path, "wave-a")}
    assert days[since.isoformat()]["status"] == "ok"
    assert days[(since + timedelta(days=1)).isoformat()]["status"] == "ok"
    last = days[YESTERDAY.isoformat()]
    assert last["status"] == "ok"
    assert last["max_lead_h"] < 48  # horizon-limited, not full
    assert all(d["backfilled"] is True for d in days.values())

    tide_days = {d["date"]: d for d in _history_days(tmp_path, "tide-b")}
    assert tide_days[YESTERDAY.isoformat()]["status"] == "missing"

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 3  # "ok" days only
    assert row["n_days_backfilled"] == 3


def test_a_missing_day_is_retried_and_resolved_on_a_later_call(tmp_path, patched_sources, monkeypatch, calls):
    """Résolution 5 / blocker 3: a transient source failure must self-heal, not
    poison a day forever. Simulate a wind outage on the first call (every day
    ends up "missing"), then a healthy source on a later call — the same day
    must be retried (not skipped) and can turn "ok"."""
    since = YESTERDAY

    def _wind_boom(station, date_start, date_end, session=None):
        raise SourceError(station.id, "open-meteo 503")

    monkeypatch.setattr(backfill, "fetch_wind_models_history", _wind_boom)
    backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)
    first = _history_days(tmp_path, "wave-a")
    assert first[-1]["status"] == "missing"
    assert first[-1]["backfilled"] is True

    # Restore a healthy wind source (still asserts the date-range regression) —
    # the outage is over, obs/marine were already fine.
    def _wind_ok(station, date_start, date_end, session=None):
        calls["wind"] += 1
        assert date_end <= TODAY
        return _wind_models_df(date_start, date_end)

    monkeypatch.setattr(backfill, "fetch_wind_models_history", _wind_ok)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)

    assert summary["wave-a"] == [since.isoformat()]  # retried, not skipped
    assert calls["wind"] == 1  # the healthy source really was called this time


def test_deep_fetch_failure_marks_every_missing_day_missing_and_backfilled(tmp_path, patched_sources, monkeypatch):
    since = YESTERDAY - timedelta(days=2)

    def _wind_boom(station, date_start, date_end, session=None):
        raise SourceError(station.id, "open-meteo 503")

    monkeypatch.setattr(backfill, "fetch_wind_history", _wind_boom)
    monkeypatch.setattr(backfill, "fetch_wind_models_history", _wind_boom)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    expected = [(since + timedelta(days=i)).isoformat() for i in range(3)]
    assert summary["wave-a"] == expected
    days = _history_days(tmp_path, "wave-a")
    assert all(d["status"] == "missing" and d["backfilled"] is True for d in days)


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
    assert calls["marine"] == 0
    assert calls["wind"] == 0


def test_backfill_never_writes_latest_json(tmp_path, patched_sources):
    """Blocker 2: a past issue must never sit in `latest.json` — that file is
    what the live daily cron reads back to decide what to score next."""
    since = YESTERDAY - timedelta(days=1)
    backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    assert not (tmp_path / "wave-a" / "latest.json").exists()
    assert not (tmp_path / "tide-b" / "latest.json").exists()


def test_daily_run_after_backfill_does_not_destroy_the_backfilled_day(tmp_path, patched_sources, monkeypatch):
    """Blocker 2's regression scenario end-to-end: backfill a day, then run the
    live `daily.run` the next day — the backfilled "ok" entry must survive
    byte-for-byte, not be silently re-scored (and likely downgraded to
    "missing", since daily._fetch_obs only looks back OBS_LOOKBACK_DAYS)."""
    since = YESTERDAY - timedelta(days=1)
    backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)
    backfilled_day = next(
        d for d in _history_days(tmp_path, "wave-a") if d["date"] == since.isoformat()
    )
    assert backfilled_day["status"] == "ok"
    assert not (tmp_path / "wave-a" / "latest.json").exists()

    # Now the live daily path, patched the same way test_daily.py does it.
    def _live_wave_obs(station, start):
        idx = pd.date_range(start, TODAY, freq="1h", tz="UTC")
        return pd.DataFrame({"hs": np.full(len(idx), 1.3), "tp": np.full(len(idx), 8.0)}, index=idx)

    def _live_marine(station, session=None, forecast_days=3):
        idx = pd.date_range(pd.Timestamp(TODAY, tz="UTC"), periods=24 * forecast_days, freq="1h")
        return pd.DataFrame({c: np.full(len(idx), MODEL_HS[c]) for c in MODEL_COLUMNS}, index=idx)

    def _live_wind_models(station, session=None):
        idx = pd.date_range("2026-07-25", periods=24 * 10, freq="1h", tz="UTC")
        return pd.DataFrame(
            {c: np.full(len(idx), 3.0 if c.startswith("wind_u10") else -2.0) for c in MULTI_FORCING_COLUMNS},
            index=idx,
        )

    monkeypatch.setattr(daily, "fetch_wave_obs", _live_wave_obs)
    monkeypatch.setattr(daily, "fetch_wave_models_forecast", _live_marine)
    monkeypatch.setattr(daily, "fetch_wind_models_forecast", _live_wind_models)

    daily.run(TODAY, tmp_path, stations=[WAVE], gate=GATE, archive_dir=tmp_path / "archive")

    days_after_daily = {d["date"]: d for d in _history_days(tmp_path, "wave-a")}
    survived = days_after_daily[since.isoformat()]
    # The day may legitimately *gain* verified points (`_rescore_pending`
    # completes its still-pending leads against the fresh obs) — what blocker 2
    # forbids is any downgrade: already-scored points must survive verbatim,
    # status and backfilled flag included.
    assert survived["status"] == "ok"
    assert survived["backfilled"] is True
    assert survived["n_points"] >= backfilled_day["n_points"]
    assert survived["max_lead_h"] >= backfilled_day["max_lead_h"]
    scored = {p["t"]: p for p in survived["series"]}
    assert all(scored[p["t"]] == p for p in backfilled_day["series"])  # no point lost or altered


def test_backfill_wave_replays_off_the_marine_history_with_the_artefact_baseline(
    tmp_path, patched_sources, calls
):
    """Task 6: the replayed wave baseline comes from `fetch_wave_models_history`
    (CMEMS is out of the path) and from the artefact's own column — one deep
    marine fetch for the whole window, never one per replayed day."""
    since = YESTERDAY - timedelta(days=2)

    summary = backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)

    assert summary["wave-a"] == [(since + timedelta(days=i)).isoformat() for i in range(3)]
    assert calls["marine"] == 1  # one deep fetch, three replayed days
    assert not hasattr(backfill, "fetch_wave_forecast")  # MFWAM/CMEMS gone

    # The pre-roll must cover the first replayed day's -24h baseline lookback.
    marine_start, marine_end = calls["last_marine_range"]
    assert marine_start < since
    assert marine_end <= TODAY

    replayed = next(d for d in _history_days(tmp_path, "wave-a") if d["date"] == since.isoformat())
    assert replayed["status"] == "ok"
    assert replayed["baseline_model"] == BASELINE_MODEL
    # Baseline served = the artefact's column, not the first model available.
    assert {p["baseline"] for p in replayed["series"]} == {MODEL_HS[f"hs_{BASELINE_MODEL}"]}
