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
from scoreboard.sources import SourceError

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


def _mfwam_baseline(start, end, value=1.0):
    idx = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    return pd.DataFrame({"hs_baseline": np.full(len(idx), value)}, index=idx)


def _wind_df(date_start, date_end, value=3.0):
    idx = _date_end_inclusive_index(date_start, date_end)
    return pd.DataFrame({"wind_u10": np.full(len(idx), value), "wind_v10": np.full(len(idx), -2.0)}, index=idx)


@pytest.fixture
def calls():
    """Counts *deep* fetch invocations — must stay at 1 per source regardless of
    how many days are missing (the whole point of résolution 1) — and records
    the last date range each source was asked for, so a regression like
    blocker 1 (a future `end_date` sent to a real API) is visible in-process."""
    return {"candhis": 0, "tide": 0, "mfwam": 0, "wind": 0, "last_wind_range": None, "last_tide_range": None}


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

    def _mfwam(stations, run_date, lookback_days=1, horizon_days=3):
        calls["mfwam"] += 1
        start = pd.Timestamp(run_date, tz="UTC") - pd.Timedelta(days=lookback_days)
        end = pd.Timestamp(run_date, tz="UTC") + pd.Timedelta(days=horizon_days)
        return {"wave-a": _mfwam_baseline(start, end)}

    def _wind(station, date_start, date_end, session=None):
        calls["wind"] += 1
        calls["last_wind_range"] = (date_start, date_end)
        # This is the exact regression check for blocker 1: the real
        # archive-api.open-meteo.com rejects `end_date` beyond its own
        # current date with HTTP 400 — verified live by the reviewer.
        assert date_end <= TODAY, f"wind fetch requested a future end date: {date_end}"
        return _wind_df(date_start, date_end)

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
    # replayed — wind is per-station (like daily.run's own live forcing call),
    # not shared like mfwam's single multi-station subset.
    assert calls["candhis"] == 1
    assert calls["mfwam"] == 1
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


def test_empty_history_backfills_every_day_in_range_last_day_structurally_missing(
    tmp_path, patched_sources
):
    """`until` (yesterday) needs forcing through `t0+48h`, i.e. tomorrow 06:00 —
    beyond what any wind archive can serve *today*. That day comes back
    "missing" (not "ok"); résolution 5's retry-on-"missing" is what recovers it
    on a later call (see test_a_missing_day_is_retried_on_a_later_call below),
    not this run. This is the accepted trade-off from blocker 1's review."""
    since = YESTERDAY - timedelta(days=2)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=STATIONS, gate=GATE)

    expected = [(since + timedelta(days=i)).isoformat() for i in range(3)]
    assert summary["wave-a"] == expected  # all three were *attempted*
    assert summary["tide-b"] == expected

    days = {d["date"]: d for d in _history_days(tmp_path, "wave-a")}
    assert days[since.isoformat()]["status"] == "ok"
    assert days[(since + timedelta(days=1)).isoformat()]["status"] == "ok"
    assert days[YESTERDAY.isoformat()]["status"] == "missing"
    assert all(d["backfilled"] is True for d in days.values())

    scores = json.loads((tmp_path / "scores.json").read_text())
    row = next(r for r in scores["stations"] if r["id"] == "wave-a")
    assert row["n_days"] == 2  # "ok" days only
    assert row["n_days_backfilled"] == 2


def test_a_missing_day_is_retried_and_resolved_on_a_later_call(tmp_path, patched_sources, monkeypatch, calls):
    """Résolution 5 / blocker 3: a transient source failure must self-heal, not
    poison a day forever. Simulate a wind outage on the first call (every day
    ends up "missing"), then a healthy source on a later call — the same day
    must be retried (not skipped) and can turn "ok"."""
    since = YESTERDAY

    def _wind_boom(station, date_start, date_end, session=None):
        raise SourceError(station.id, "open-meteo 503")

    monkeypatch.setattr(backfill, "fetch_wind_history", _wind_boom)
    backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)
    first = _history_days(tmp_path, "wave-a")
    assert first[-1]["status"] == "missing"
    assert first[-1]["backfilled"] is True

    # Restore a healthy wind source (still asserts the date-range regression) —
    # the outage is over, obs/mfwam were already fine.
    def _wind_ok(station, date_start, date_end, session=None):
        calls["wind"] += 1
        assert date_end <= TODAY
        return _wind_df(date_start, date_end)

    monkeypatch.setattr(backfill, "fetch_wind_history", _wind_ok)
    summary = backfill.run(since, tmp_path, today=TODAY, stations=[WAVE], gate=GATE)

    assert summary["wave-a"] == [since.isoformat()]  # retried, not skipped
    assert calls["wind"] == 1  # the healthy source really was called this time


def test_deep_fetch_failure_marks_every_missing_day_missing_and_backfilled(tmp_path, patched_sources, monkeypatch):
    since = YESTERDAY - timedelta(days=2)

    def _wind_boom(station, date_start, date_end, session=None):
        raise SourceError(station.id, "open-meteo 503")

    monkeypatch.setattr(backfill, "fetch_wind_history", _wind_boom)
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
    assert calls["mfwam"] == 0
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

    def _live_mfwam(stations, run_date, lookback_days=1, horizon_days=3):
        idx = pd.date_range(pd.Timestamp(run_date, tz="UTC") - pd.Timedelta(days=1), periods=24 * 4, freq="1h")
        return {"wave-a": pd.DataFrame({"hs_baseline": np.full(len(idx), 1.0)}, index=idx)}

    def _live_wind_forecast(station, session=None):
        idx = pd.date_range("2026-07-25", periods=24 * 10, freq="1h", tz="UTC")
        return pd.DataFrame({"wind_u10": np.full(len(idx), 3.0), "wind_v10": np.full(len(idx), -2.0)}, index=idx)

    monkeypatch.setattr(daily, "fetch_wave_obs", _live_wave_obs)
    monkeypatch.setattr(daily, "fetch_wave_forecast", _live_mfwam)
    monkeypatch.setattr(daily, "fetch_wind_forecast", _live_wind_forecast)

    daily.run(TODAY, tmp_path, stations=[WAVE], gate=GATE)

    days_after_daily = {d["date"]: d for d in _history_days(tmp_path, "wave-a")}
    assert days_after_daily[since.isoformat()] == backfilled_day  # untouched, byte-for-byte
